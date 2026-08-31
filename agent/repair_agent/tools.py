"""Repair Agent 注册的结构化工具与受信 conversion probe。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from agent.domain.enums import RiskLevel
from agent.domain.errors import (
    BudgetExceededError,
    SandboxExecutionError,
    ToolAuthorizationError,
    ToolPreconditionError,
)
from agent.domain.models import TaskArtifact
from agent.domain.repair import (
    RepairAttemptArtifact,
    RepairDisposition,
    RepairReport,
    classify_target_validation,
)
from agent.domain.reproduction import (
    ExpectedFailureKind,
    ReproductionAttemptArtifact,
    ReproductionDisposition,
    classify_reproduction_result,
)
from agent.repair_agent.state import RepairAgentState
from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
)
from agent.tools.edits import StructuredEditTools
from agent.tools.source import PathScope, SourceReader
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import Edit, EditMode


@dataclass(frozen=True)
class RepairAgentContext:
    """只由 Controller 注入；模型不能构造或覆盖。"""

    run_id: UUID
    feedback_id: UUID
    task: TaskArtifact
    source_snapshot_ref: str
    source_workspace: Any
    artifact_store: ArtifactStore
    edit_tools: StructuredEditTools
    sandbox_client: Any
    max_reproduction_rounds: int
    max_repair_rounds: int
    max_sandbox_seconds: int
    allow_repair: bool = True


class ProbeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["reproducing", "repairing"]
    test_patch_ref: str | None
    target_test_selector: str | None
    expected_failure_kind: str | None
    reproduction_result_ref: str | None
    reproduction_confirmed: bool
    reproduction_round: int
    sandbox_duration_ms: int
    summary: dict[str, object]


@tool
async def read_source_file(
    path: str,
    start_line: int,
    end_line: int,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """读取白名单内源码的指定行范围；禁止绝对路径、路径穿越和超限读取。"""

    _require_phase(runtime.state, {"reproducing", "repairing"})
    snapshot = runtime.context.source_workspace.resolve(
        runtime.context.source_snapshot_ref
    )
    result = SourceReader(snapshot.root).read_source_file(
        path,
        start_line=start_line,
        end_line=end_line,
    )
    return _command(
        runtime,
        {"tool_calls": 1},
        {"source": result.model_dump(mode="json")},
    )


@tool
async def search_source(
    query: str,
    path_scope: PathScope,
    max_results: int,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """在白名单源码中做有界字面量搜索，不接受正则或 Shell 模式。"""

    _require_phase(runtime.state, {"reproducing", "repairing"})
    snapshot = runtime.context.source_workspace.resolve(
        runtime.context.source_snapshot_ref
    )
    results = SourceReader(snapshot.root).search_source(
        query,
        path_scope=path_scope,
        max_results=max_results,
    )
    return _command(
        runtime,
        {"tool_calls": 1},
        {"results": [item.model_dump(mode="json") for item in results]},
    )


@tool
async def submit_test_edits(
    edits: tuple[Edit, ...],
    target_test_selector: str,
    expected_failure_kind: ExpectedFailureKind,
    reason: str,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """在转换成功分支提交语义回归测试；只生成经 Policy 检查的 test.patch。"""

    state, context = runtime.state, runtime.context
    _require_phase(state, {"reproducing"})
    round_number = int(state.get("reproduction_round", 0)) + 1
    if round_number > context.max_reproduction_rounds:
        raise BudgetExceededError("reproduction round budget exhausted")
    prefix = f"test_feedback_{context.feedback_id.hex[:8]}_"
    if not target_test_selector.startswith(prefix):
        raise ValueError("target_test_selector must use the current feedback prefix")
    if len(reason.strip()) < 1 or len(reason) > 600:
        raise ValueError("reason must contain 1..600 characters")
    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    _validate_model_test_append(snapshot.root, edits)
    submitted = context.edit_tools.submit_test_edits(
        context.run_id,
        snapshot.root,
        edits,
        target_test_selector=target_test_selector,
    )
    return _command(
        runtime,
        {
            "test_patch_ref": submitted.artifact_ref,
            "target_test_selector": target_test_selector,
            "expected_failure_kind": expected_failure_kind.value,
            "reproduction_round": round_number,
            "reproduction_confirmed": False,
            "reproduction_result_ref": None,
            "tool_calls": 1,
        },
        {
            "accepted": True,
            "patch_sha256": submitted.sha256,
            "changed_files": submitted.changed_files,
            "added_lines": submitted.added_lines,
            "deleted_lines": submitted.deleted_lines,
            "next": "run_sandbox",
        },
    )


@tool
async def submit_fix_edits(
    edits: tuple[Edit, ...],
    summary: str,
    risk: Literal["low", "medium", "high"],
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """提交最小后端修复；只能修改本地 Policy 的 fix 白名单。"""

    state, context = runtime.state, runtime.context
    _require_phase(state, {"repairing"})
    round_number = int(state.get("repair_round", 0)) + 1
    if round_number > context.max_repair_rounds:
        raise BudgetExceededError("repair round budget exhausted")
    if len(summary.strip()) < 1 or len(summary) > 1000:
        raise ValueError("summary must contain 1..1000 characters")
    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    submitted = context.edit_tools.submit_fix_edits(
        context.run_id,
        snapshot.root,
        edits,
    )
    return _command(
        runtime,
        {
            "fix_patch_ref": submitted.artifact_ref,
            "fix_summary": summary,
            "fix_risk": risk,
            "repair_round": round_number,
            "repair_confirmed": False,
            "repair_result_ref": None,
            "tool_calls": 1,
        },
        {
            "accepted": True,
            "patch_sha256": submitted.sha256,
            "changed_files": submitted.changed_files,
            "added_lines": submitted.added_lines,
            "deleted_lines": submitted.deleted_lines,
            "next": "run_sandbox",
        },
    )


@tool
async def run_sandbox(
    reason: str,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """执行当前阶段固定 Sandbox Job；模型不能提供命令、patch、job ID 或 pytest 参数。"""

    state, context = runtime.state, runtime.context
    _require_phase(state, {"reproducing", "repairing"})
    if int(state.get("sandbox_duration_ms", 0)) >= context.max_sandbox_seconds * 1000:
        raise BudgetExceededError("sandbox time budget exhausted")
    if len(reason.strip()) < 1 or len(reason) > 600:
        raise ValueError("reason must contain 1..600 characters")
    if state["phase"] == "reproducing":
        return await _run_reproduction(runtime)
    return await _run_repair(runtime)


@tool
async def complete_reproduction(
    evidence_summary: str,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """在受信基线结果已确认目标失败后，把 Agent 推进 repairing 阶段。"""

    state = runtime.state
    _require_phase(state, {"reproducing"})
    if not state.get("reproduction_confirmed") or not state.get(
        "reproduction_result_ref"
    ):
        raise ValueError("trusted reproduction evidence is missing")
    next_update: dict[str, object] = {"tool_calls": 1}
    if runtime.context.allow_repair:
        next_update["phase"] = "repairing"
    else:
        next_update["terminal"] = "completed"
    return _command(
        runtime,
        next_update,
        {
            "accepted": True,
            "phase": "repairing" if runtime.context.allow_repair else "completed",
            "evidence": _bounded(evidence_summary, 600),
            "next": (
                "inspect source and submit a fix"
                if runtime.context.allow_repair
                else "trusted outer reproduction finalization"
            ),
        },
    )


@tool
async def complete_repair(
    evidence_summary: str,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """在受信目标结果已通过后结束工具循环；外层仍会执行独立验证。"""

    state = runtime.state
    _require_phase(state, {"repairing"})
    if not state.get("repair_confirmed") or not state.get("repair_result_ref"):
        raise ValueError("trusted target validation evidence is missing")
    return _command(
        runtime,
        {"terminal": "completed", "tool_calls": 1},
        {
            "accepted": True,
            "candidate_ready": True,
            "evidence": _bounded(evidence_summary, 600),
            "next": "trusted final validation",
        },
    )


@tool
async def report_blocked(
    code: Literal[
        "cannot_reproduce",
        "needs_human",
        "external_dependency_required",
        "budget_exhausted",
    ],
    summary: str,
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    """用稳定原因终止无法安全继续的候选，不得提交任意错误码。"""

    state, context = runtime.state, runtime.context
    _require_phase(state, {"reproducing", "repairing"})
    if code == "external_dependency_required":
        raise ToolAuthorizationError(
            "only local Patch Policy can prove an external dependency requirement"
        )
    if code == "cannot_reproduce" and (
        state.get("phase") != "reproducing"
        or int(state.get("reproduction_round", 0))
        < context.max_reproduction_rounds
    ):
        raise ValueError(
            "cannot_reproduce requires exhausting the reproduction round budget"
        )
    if code == "budget_exhausted" and (
        int(state.get("sandbox_duration_ms", 0))
        < context.max_sandbox_seconds * 1000
    ):
        raise ValueError("budget_exhausted is not supported by trusted counters")
    if code == "needs_human" and not (
        int(state.get("reproduction_round", 0))
        or int(state.get("repair_round", 0))
    ):
        raise ValueError("needs_human requires at least one attempted candidate")
    repair_result_ref = state.get("repair_result_ref")
    if state.get("phase") == "repairing" and code == "needs_human":
        if not repair_result_ref:
            raise ValueError("repairing needs_human requires a trusted sandbox result")
        attempt = context.artifact_store.read_repair_result(repair_result_ref)
        updated = attempt.model_copy(
            update={
                "report": RepairReport(
                    disposition=RepairDisposition.NEEDS_HUMAN,
                    round=attempt.round,
                    failure_code=code,
                    failure_summary=_bounded(summary, 1000),
                )
            }
        )
        repair_result_ref = context.artifact_store.write_repair_result_ref(
            context.run_id,
            updated,
        )
    return _command(
        runtime,
        {
            "terminal": "blocked",
            "blocked_code": code,
            "blocked_summary": _bounded(summary, 1000),
            "repair_result_ref": repair_result_ref,
            "tool_calls": 1,
        },
        {"accepted": True, "blocked": True, "code": code},
    )


REPAIR_TOOLS = (
    read_source_file,
    search_source,
    submit_test_edits,
    submit_fix_edits,
    run_sandbox,
    complete_reproduction,
    complete_repair,
    report_blocked,
)


async def run_conversion_probe(context: RepairAgentContext) -> ProbeOutcome:
    """用受信固定测试先区分转换抛错与转换成功，不调用模型。"""

    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    selector = f"test_feedback_{context.feedback_id.hex[:8]}_conversion_probe"
    fixture_name = f"{selector}.md"
    fixture_path = f"backend/tests/fixtures/feedback/{fixture_name}"
    regression_path = snapshot.root / "backend/tests/test_feedback_regressions.py"
    existing = regression_path.read_text("utf-8") if regression_path.is_file() else ""
    test_source = _append_conversion_test(existing, selector, fixture_name)
    submitted = context.edit_tools.submit_test_edits(
        context.run_id,
        snapshot.root,
        (
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=test_source,
            ),
            Edit(
                path=fixture_path,
                mode=EditMode.FULL_FILE,
                content=context.task.markdown_content.rstrip("\n") + "\n",
            ),
        ),
        target_test_selector=selector,
    )
    patch = context.artifact_store.read_patch(submitted.artifact_ref)
    job = _job(
        context,
        phase="conversion-probe",
        round_number=1,
        job_type=JobType.REPRODUCE_TARGET,
        selector=selector,
        test_patch=patch,
        fix_patch=None,
    )
    result = await context.sandbox_client.submit(
        SandboxArtifacts(
            job=job,
            source_archive=snapshot.archive_path.read_bytes(),
            test_patch=patch,
        )
    )
    report = classify_reproduction_result(
        result,
        expected_failure_kind=ExpectedFailureKind.UNEXPECTED_CONVERSION_ERROR,
        round_number=1,
        target_test_selector=selector,
    )
    result_ref = context.artifact_store.write_reproduction_result_ref(
        context.run_id,
        ReproductionAttemptArtifact(
            round=1,
            test_patch_sha256=submitted.sha256,
            files_needed_for_fix=(),
            sandbox_result=result,
            report=report,
        ),
    )
    summary = _sandbox_summary(result)
    if report.disposition is ReproductionDisposition.REPRODUCED:
        return ProbeOutcome(
            phase="repairing",
            test_patch_ref=submitted.artifact_ref,
            target_test_selector=selector,
            expected_failure_kind=ExpectedFailureKind.UNEXPECTED_CONVERSION_ERROR.value,
            reproduction_result_ref=result_ref,
            reproduction_confirmed=True,
            reproduction_round=1,
            sandbox_duration_ms=result.duration_ms,
            summary=summary,
        )
    if report.disposition is ReproductionDisposition.NOT_REPRODUCED:
        return ProbeOutcome(
            phase="reproducing",
            test_patch_ref=None,
            target_test_selector=None,
            expected_failure_kind=None,
            reproduction_result_ref=None,
            reproduction_confirmed=False,
            reproduction_round=0,
            sandbox_duration_ms=result.duration_ms,
            summary=summary,
        )
    raise SandboxExecutionError(
        "conversion probe did not produce a trusted pass or ConversionError",
        safe_details={
            "probe_disposition": report.disposition.value,
            "failure_code": report.failure_code,
        },
    )


async def _run_reproduction(
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    state, context = runtime.state, runtime.context
    patch_ref = state.get("test_patch_ref")
    selector = state.get("target_test_selector")
    raw_kind = state.get("expected_failure_kind")
    round_number = int(state.get("reproduction_round", 0))
    if not patch_ref or not selector or not raw_kind or round_number < 1:
        raise ToolPreconditionError(
            "submit_test_edits must succeed before run_sandbox",
            safe_details={"required_action": "submit_test_edits"},
        )
    expected_kind = ExpectedFailureKind(raw_kind)
    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    patch = context.artifact_store.read_patch(patch_ref)
    job = _job(
        context,
        phase="reproduction",
        round_number=round_number,
        job_type=JobType.REPRODUCE_TARGET,
        selector=selector,
        test_patch=patch,
        fix_patch=None,
    )
    result = await context.sandbox_client.submit(
        SandboxArtifacts(
            job=job,
            source_archive=snapshot.archive_path.read_bytes(),
            test_patch=patch,
        )
    )
    report = classify_reproduction_result(
        result,
        expected_failure_kind=expected_kind,
        round_number=round_number,
        target_test_selector=selector,
    )
    reference = context.artifact_store.write_reproduction_result_ref(
        context.run_id,
        ReproductionAttemptArtifact(
            round=round_number,
            test_patch_sha256=_sha256(patch),
            files_needed_for_fix=(),
            sandbox_result=result,
            report=report,
        ),
    )
    summary = _sandbox_summary(result, disposition=report.disposition.value)
    return _command(
        runtime,
        {
            "reproduction_result_ref": reference,
            "reproduction_confirmed": (
                report.disposition is ReproductionDisposition.REPRODUCED
            ),
            "last_sandbox_summary": summary,
            "tool_calls": 1,
            "sandbox_duration_ms": result.duration_ms,
        },
        summary,
    )


async def _run_repair(
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
) -> Command:
    state, context = runtime.state, runtime.context
    test_ref = state.get("test_patch_ref")
    fix_ref = state.get("fix_patch_ref")
    selector = state.get("target_test_selector")
    round_number = int(state.get("repair_round", 0))
    if not test_ref or not fix_ref or not selector or round_number < 1:
        raise ToolPreconditionError(
            "submit_fix_edits must succeed before run_sandbox",
            safe_details={"required_action": "submit_fix_edits"},
        )
    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    test_patch = context.artifact_store.read_patch(test_ref)
    fix_patch = context.artifact_store.read_patch(fix_ref)
    job = _job(
        context,
        phase="repair",
        round_number=round_number,
        job_type=JobType.VALIDATE_TARGET,
        selector=selector,
        test_patch=test_patch,
        fix_patch=fix_patch,
    )
    result = await context.sandbox_client.submit(
        SandboxArtifacts(
            job=job,
            source_archive=snapshot.archive_path.read_bytes(),
            test_patch=test_patch,
            fix_patch=fix_patch,
        )
    )
    report = classify_target_validation(result, round_number=round_number)
    reference = context.artifact_store.write_repair_result_ref(
        context.run_id,
        RepairAttemptArtifact(
            round=round_number,
            fix_patch_sha256=_sha256(fix_patch),
            changed_files=(),
            fix_summary=str(state.get("fix_summary") or ""),
            risk_level=RiskLevel(str(state.get("fix_risk") or "medium")),
            sandbox_result=result,
            report=report,
        ),
    )
    summary = _sandbox_summary(result, disposition=report.disposition.value)
    return _command(
        runtime,
        {
            "repair_result_ref": reference,
            "repair_confirmed": report.disposition is RepairDisposition.TARGET_PASSED,
            "last_sandbox_summary": summary,
            "tool_calls": 1,
            "sandbox_duration_ms": result.duration_ms,
        },
        summary,
    )


def _job(
    context: RepairAgentContext,
    *,
    phase: str,
    round_number: int,
    job_type: JobType,
    selector: str,
    test_patch: bytes,
    fix_patch: bytes | None,
) -> SandboxJob:
    snapshot = context.source_workspace.resolve(context.source_snapshot_ref)
    return SandboxJob(
        job_id=uuid5(
            NAMESPACE_URL,
            f"mdtoword:{context.run_id}:repair-agent:{phase}:{round_number}",
        ),
        run_id=context.run_id,
        job_type=job_type,
        base_sha=snapshot.base_sha,
        source_snapshot_sha256=snapshot.source_snapshot_sha256,
        test_patch_sha256=_sha256(test_patch),
        fix_patch_sha256=_sha256(fix_patch) if fix_patch is not None else None,
        target_test_selector=selector,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def _command(
    runtime: ToolRuntime[RepairAgentContext, RepairAgentState],
    update: dict[str, object],
    payload: dict[str, object],
) -> Command:
    tool_call_id = runtime.tool_call_id
    if tool_call_id is None:
        raise ToolAuthorizationError("tool call id is missing")
    return Command(
        update={
            **update,
            "messages": [
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False, default=str),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _require_phase(state: RepairAgentState, phases: set[str]) -> None:
    if state.get("phase") not in phases or state.get("terminal") is not None:
        raise ToolAuthorizationError("tool is not authorized in the current phase")


def _sandbox_summary(
    result: SandboxResult,
    *,
    disposition: str | None = None,
) -> dict[str, object]:
    junit = result.junit_summary
    return {
        "status": result.status.value,
        "disposition": disposition,
        "error_code": result.error_code,
        "duration_ms": result.duration_ms,
        "target": (
            {
                "collected": junit.target_collected,
                "outcome": junit.target_outcome.value,
                "failure_type": junit.target_failure_type,
                "message": _bounded(junit.target_message, 1000),
                "failures": junit.failures,
                "errors": junit.errors,
                "skipped": junit.skipped,
            }
            if junit is not None
            else None
        ),
    }


def _append_conversion_test(existing: str, selector: str, fixture_name: str) -> str:
    content = existing.rstrip("\n")
    if content:
        content += "\n\n\n"
    return content + (
        f"def {selector}(tmp_path):\n"
        "    from pathlib import Path\n\n"
        "    from app.pandoc_runner import convert_markdown_to_docx\n\n"
        f'    fixture = Path(__file__).parent / "fixtures" / "feedback" / "{fixture_name}"\n'
        '    markdown = fixture.read_text(encoding="utf-8")\n'
        "    convert_markdown_to_docx(markdown, tmp_path)\n"
    )


def _validate_model_test_append(snapshot_root: Any, edits: tuple[Edit, ...]) -> None:
    """模型只能保留唯一锚点后追加回归；受信 probe 不经过此模型边界。"""

    regression_path = "backend/tests/test_feedback_regressions.py"
    candidates = [edit for edit in edits if edit.path == regression_path]
    if len(candidates) != 1:
        raise ValueError("exactly one regression test file edit is required")
    edit = candidates[0]
    existing_path = snapshot_root / regression_path
    existing = existing_path.read_text("utf-8") if existing_path.is_file() else ""
    if existing:
        if edit.mode is not EditMode.SEARCH_REPLACE:
            raise ValueError("an existing regression file requires search_replace append")
        if edit.search is None or edit.replace is None:
            raise ValueError("regression append requires search and replace")
        if not edit.replace.startswith(edit.search):
            raise ValueError("regression append must preserve its exact search anchor")
        appended = edit.replace[len(edit.search) :]
        if not appended.strip():
            raise ValueError("regression append must add a new test")
    elif edit.mode is not EditMode.FULL_FILE:
        raise ValueError("an empty regression file requires full_file")


def _sha256(content: bytes) -> str:
    from hashlib import sha256

    return sha256(content).hexdigest()


def _bounded(value: str, limit: int) -> str:
    return value.replace("\x00", "").strip()[:limit]
