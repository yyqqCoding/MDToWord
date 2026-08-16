from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    GateCategory,
    GateRoute,
    RiskLevel,
)
from agent.domain.content import contains_mermaid_diagram
from agent.domain.errors import (
    ClaimTokenMismatchError,
    ExternalDependencyError,
    FeedbackNotFoundError,
    InvalidEditError,
    InvalidModelResponseError,
    PatchPolicyError,
    PublicationError,
    SourceAccessError,
)
from agent.domain.repair import (
    RepairAttemptArtifact,
    RepairDisposition,
    RepairReport,
    build_validation_result,
    classify_target_validation,
)
from agent.gate import execute_feedback_gate
from agent.providers.base import ModelProvider
from agent.publishing.contracts import (
    PublicationDisposition,
    PublicationEvidence,
    PublicationFile,
    PublicationRequest,
    PullRequestPublisher,
)
from agent.reproduction import (
    ReproductionModelExecution,
    build_mermaid_test_fallback,
    generate_reproduction_test,
    plan_reproduction,
)
from agent.repair import generate_fix
from agent.repositories.base import AgentRunRepository, FeedbackRepository
from agent.sandbox.client import SandboxClient
from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)
from agent.state import AgentState
from agent.telemetry.base import NoopTelemetry, Telemetry, ToolTrace
from agent.tools.edits import StructuredEditTools
from agent.tools.source import SourceReader
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.paths import resolve_snapshot_path
from agent.workspace.validation import (
    compose_validated_patch,
    materialize_validated_files,
    normalize_authorized_patch,
)
from agent.workspace.preparation import SourceWorkspace
from agent.domain.reproduction import (
    ReproductionAttemptArtifact,
    ReproductionDisposition,
    ReproductionReport,
    SourceReadRequest,
    classify_reproduction_result,
)


GRAPH_VERSION = "agent-graph-v7"
POLICY_VERSION = "publication-policy-v6"

_ROUTE_TO_FEEDBACK_STATUS = {
    GateRoute.ACCEPTED_BACKEND_BUG: FeedbackStatus.REPRODUCING,
    GateRoute.REJECTED_IRRELEVANT: FeedbackStatus.REJECTED_IRRELEVANT,
    GateRoute.QUARANTINED_SECURITY: FeedbackStatus.QUARANTINED_SECURITY,
    GateRoute.OUT_OF_SCOPE: FeedbackStatus.OUT_OF_SCOPE,
    GateRoute.NEEDS_HUMAN: FeedbackStatus.NEEDS_HUMAN,
    GateRoute.DUPLICATE: FeedbackStatus.DUPLICATE,
}


@dataclass(frozen=True)
class ReproductionDependencies:
    plan_provider: ModelProvider
    test_provider: ModelProvider
    source_workspace: SourceWorkspace
    edit_tools: StructuredEditTools
    sandbox_client: SandboxClient
    telemetry: Telemetry = field(default_factory=NoopTelemetry)
    # 长源码上下文比 Gate 请求耗时更高，但仍由配置限制在有界范围内。
    model_timeout_seconds: float = 180.0


@dataclass(frozen=True)
class RepairDependencies:
    fix_provider: ModelProvider
    telemetry: Telemetry = field(default_factory=NoopTelemetry)
    model_timeout_seconds: float = 180.0
    max_model_calls: int = 8
    max_tool_calls: int = 30
    max_total_tokens: int = 200_000
    max_sandbox_seconds: int = 900
    baseline_skipped: int = 0


@dataclass(frozen=True)
class PublishingDependencies:
    publisher: PullRequestPublisher
    trace_url_template: str
    telemetry: Telemetry = field(default_factory=NoopTelemetry)

    def __post_init__(self) -> None:
        if "{trace_id}" not in self.trace_url_template:
            raise ValueError("trace URL template must contain {trace_id}")


def build_gate_graph(
    *,
    feedback_repository: FeedbackRepository,
    run_repository: AgentRunRepository,
    provider: ModelProvider,
    artifact_store: ArtifactStore,
    checkpointer: BaseCheckpointSaver,
    min_confidence: float,
    reproduction: ReproductionDependencies | None = None,
    repair: RepairDependencies | None = None,
    publishing: PublishingDependencies | None = None,
    interrupt_after: Sequence[str] | None = None,
):
    """构建可恢复 Graph；后续阶段依赖必须按 D -> E -> F 顺序启用。"""

    if repair is not None and reproduction is None:
        raise ValueError("repair graph requires reproduction dependencies")
    if publishing is not None and repair is None:
        raise ValueError("publishing graph requires repair dependencies")

    async def start_gate(state: AgentState) -> dict[str, object]:
        feedback = await feedback_repository.get(state.feedback_id)
        if feedback is None:
            raise FeedbackNotFoundError(f"feedback {state.feedback_id} does not exist")
        if feedback.claim_token != state.claim_token:
            raise ClaimTokenMismatchError(
                f"claim token does not own feedback {state.feedback_id}"
            )
        if feedback.status is FeedbackStatus.CLAIMED:
            await feedback_repository.transition(
                feedback.id,
                claim_token=state.claim_token,
                target=FeedbackStatus.GATING,
            )
        elif feedback.status is not FeedbackStatus.GATING:
            raise ClaimTokenMismatchError("feedback is no longer resumable by this run")
        await run_repository.mark_gating(state.run_id)
        return {"status": AgentRunStatus.GATING}

    async def classify_gate(state: AgentState) -> dict[str, object]:
        if state.task_artifact_ref is None:
            raise ValueError("gate state is missing task_artifact_ref")
        task = artifact_store.read_task(state.task_artifact_ref)
        duplicate = await feedback_repository.find_open_by_fingerprint(
            task.content_fingerprint,
            excluding_feedback_id=task.feedback_id,
        )
        execution = await execute_feedback_gate(
            task,
            provider,
            duplicate_found=duplicate is not None,
            min_confidence=min_confidence,
        )
        result = execution.result
        gate_ref = artifact_store.write_gate_ref(state.run_id, result)
        return {
            "route": result.route.value,
            "category": result.category.value,
            "risk": result.risk,
            "gate_result_ref": gate_ref,
            "model_calls": state.model_calls + result.model_calls,
            "tool_calls": state.tool_calls + result.tool_calls,
            "usage": {
                "input_tokens": state.usage.input_tokens + execution.input_tokens,
                "output_tokens": state.usage.output_tokens + execution.output_tokens,
                "total_tokens": state.usage.total_tokens + execution.total_tokens,
                "estimated_cost": state.usage.estimated_cost + execution.estimated_cost,
            },
        }

    async def route_feedback(state: AgentState) -> dict[str, object]:
        if state.gate_result_ref is None:
            raise ValueError("gate state is missing gate_result_ref")
        result = artifact_store.read_gate(state.gate_result_ref)
        target = _ROUTE_TO_FEEDBACK_STATUS[result.route]
        feedback = await feedback_repository.get(state.feedback_id)
        if feedback is None:
            raise FeedbackNotFoundError(f"feedback {state.feedback_id} does not exist")
        if feedback.claim_token != state.claim_token:
            raise ClaimTokenMismatchError(
                f"claim token does not own feedback {state.feedback_id}"
            )
        # 节点可能在数据库写入后、checkpoint 前中断；目标状态相同即视为幂等成功。
        if feedback.status is FeedbackStatus.GATING:
            await feedback_repository.transition(
                feedback.id,
                claim_token=state.claim_token,
                target=target,
                category=result.category,
                risk=result.risk,
            )
        elif feedback.status is not target:
            raise ClaimTokenMismatchError("feedback route was finalized by another run")
        if result.route is GateRoute.ACCEPTED_BACKEND_BUG and reproduction is not None:
            await run_repository.mark_preparing_source(
                state.run_id,
                result,
                input_tokens=state.usage.input_tokens,
                output_tokens=state.usage.output_tokens,
                total_tokens=state.usage.total_tokens,
                estimated_cost=state.usage.estimated_cost,
            )
            return {"status": AgentRunStatus.PREPARING_SOURCE}
        await run_repository.complete_gate(
            state.run_id,
            result,
            input_tokens=state.usage.input_tokens,
            output_tokens=state.usage.output_tokens,
            total_tokens=state.usage.total_tokens,
            estimated_cost=state.usage.estimated_cost,
        )
        return {"status": AgentRunStatus.COMPLETED}

    def route_after_gate(state: AgentState) -> str:
        if (
            reproduction is not None
            and state.route == GateRoute.ACCEPTED_BACKEND_BUG.value
        ):
            return "prepare_source"
        return "end"

    builder = StateGraph(AgentState)
    builder.add_node("start_gate", start_gate)
    builder.add_node("classify_gate", classify_gate)
    builder.add_node("route_feedback", route_feedback)
    builder.add_edge(START, "start_gate")
    builder.add_edge("start_gate", "classify_gate")
    builder.add_edge("classify_gate", "route_feedback")
    if reproduction is None:
        builder.add_edge("route_feedback", END)
    else:
        async def prepare_source(state: AgentState) -> dict[str, object]:
            with reproduction.telemetry.start_tool(
                ToolTrace(
                    operation="prepare-source-snapshot",
                    round=None,
                    input_summary={"run_id": str(state.run_id)},
                )
            ) as observation:
                try:
                    reference, snapshot = await reproduction.source_workspace.prepare(
                        state.run_id
                    )
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "source_prepare_failed"),
                        error_type=type(exc).__name__,
                    )
                    raise
                observation.succeed(
                    {
                        "base_sha": snapshot.base_sha,
                        "snapshot_sha256": snapshot.source_snapshot_sha256,
                    }
                )
            await run_repository.mark_reproducing(
                state.run_id,
                base_sha=snapshot.base_sha,
            )
            return {
                "status": AgentRunStatus.REPRODUCING,
                "base_sha": snapshot.base_sha,
                "source_snapshot_ref": reference,
                "tool_calls": state.tool_calls + 1,
            }

        async def create_reproduction_plan(state: AgentState) -> dict[str, object]:
            if (
                state.task_artifact_ref is None
                or state.category is None
                or state.source_snapshot_ref is None
            ):
                raise ValueError("reproduction state is missing gate context")
            task = artifact_store.read_task(state.task_artifact_ref)
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            allowed_source_paths = SourceReader(snapshot.root).list_readable_paths()
            execution = await plan_reproduction(
                task,
                category=state.category,
                allowed_source_paths=allowed_source_paths,
                provider=reproduction.plan_provider,
                timeout_seconds=reproduction.model_timeout_seconds,
            )
            plan = execution.output
            plan_ref = artifact_store.write_reproduction_plan_ref(state.run_id, plan)
            return {
                "reproduction_plan_ref": plan_ref,
                "model_calls": state.model_calls + execution.model_calls,
                "usage": _add_usage(state, execution),
            }

        async def generate_test_edit(state: AgentState) -> dict[str, object]:
            if (
                state.task_artifact_ref is None
                or state.reproduction_plan_ref is None
                or state.source_snapshot_ref is None
            ):
                raise ValueError("reproduction state is missing source or plan")
            task = artifact_store.read_task(state.task_artifact_ref)
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            previous_report = None
            if state.reproduction_result_ref is not None:
                previous_report = artifact_store.read_reproduction_result(
                    state.reproduction_result_ref
                ).report
            regression_path = resolve_snapshot_path(
                snapshot.root,
                "backend/tests/test_feedback_regressions.py",
                must_exist=False,
            )
            existing_test_source = (
                regression_path.read_text(encoding="utf-8")
                if regression_path.is_file()
                else ""
            )
            fallback = build_mermaid_test_fallback(
                task,
                plan=plan,
                previous_report=previous_report,
                existing_test_source=existing_test_source,
            )
            source_files = ()
            if fallback is not None:
                # 第二轮模板由 Controller 确定性生成，不产生模型调用或不可信源码输出。
                execution = ReproductionModelExecution(
                    output=fallback,
                    model_calls=0,
                )
            else:
                reader = SourceReader(snapshot.root)
                source_items = []
                for request in plan.files_to_read:
                    with reproduction.telemetry.start_tool(
                        ToolTrace(
                            operation="read-source-file",
                            round=state.reproduction_round + 1,
                            input_summary={
                                "path": request.path,
                                "start_line": request.start_line,
                                "end_line": request.end_line,
                            },
                        )
                    ) as observation:
                        try:
                            source_item = reader.read_source_file(
                                request.path,
                                start_line=request.start_line,
                                end_line=request.end_line,
                            )
                        except Exception as exc:
                            observation.fail(
                                error_code=getattr(
                                    exc,
                                    "error_code",
                                    "source_read_failed",
                                ),
                                error_type=type(exc).__name__,
                            )
                            raise
                        observation.succeed(
                            {
                                "path": source_item.path,
                                "start_line": source_item.start_line,
                                "end_line": source_item.end_line,
                                "content_bytes": len(
                                    source_item.content.encode("utf-8")
                                ),
                            }
                        )
                        source_items.append(source_item)
                source_files = tuple(source_items)
                try:
                    execution = await generate_reproduction_test(
                        task,
                        plan=plan,
                        source_files=source_files,
                        previous_report=previous_report,
                        existing_test_source=existing_test_source,
                        provider=reproduction.test_provider,
                        timeout_seconds=reproduction.model_timeout_seconds,
                    )
                except InvalidModelResponseError:
                    fallback = build_mermaid_test_fallback(
                        task,
                        plan=plan,
                        previous_report=previous_report,
                        existing_test_source=existing_test_source,
                        after_invalid_model_response=True,
                    )
                    if fallback is None:
                        raise
                    # Mermaid 的图形 Oracle 和测试结构均已受信，可在模型格式重试耗尽后
                    # 确定性接管；普通反馈仍保留严格 Schema 失败边界。
                    execution = ReproductionModelExecution(
                        output=fallback,
                        model_calls=0,
                    )
            generated = execution.output
            submitted = None
            edit_error: Exception | None = None
            with reproduction.telemetry.start_tool(
                ToolTrace(
                    operation="submit-test-edits",
                    round=state.reproduction_round + 1,
                    input_summary={"edit_count": len(generated.edits)},
                )
            ) as observation:
                try:
                    submitted = reproduction.edit_tools.submit_test_edits(
                        state.run_id,
                        snapshot.root,
                        generated.edits,
                        target_test_selector=generated.target_test_selector,
                    )
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "test_edit_rejected"),
                        error_type=type(exc).__name__,
                    )
                    edit_error = exc
                else:
                    observation.succeed(
                        {
                            "patch_sha256": submitted.sha256,
                            "changed_files": list(submitted.changed_files),
                            "added_lines": submitted.added_lines,
                        }
                    )
            next_round = state.reproduction_round + 1
            if edit_error is not None:
                if isinstance(edit_error, (InvalidEditError, SourceAccessError)):
                    disposition = ReproductionDisposition.INVALID_TEST
                    error_code = "invalid_test_edit"
                    summary = "generated test edit is not valid Python or text"
                elif isinstance(edit_error, PatchPolicyError):
                    disposition = ReproductionDisposition.SECURITY_REJECTED
                    error_code = "test_edit_security_rejected"
                    summary = "generated test edit violates patch policy"
                else:
                    raise edit_error
                report = ReproductionReport(
                    disposition=disposition,
                    round=next_round,
                    target_test_selector=plan.target_test_selector,
                    expected_failure_kind=plan.expected_failure_kind,
                    failure_code=error_code,
                    failure_summary=summary,
                )
                result_ref = artifact_store.write_reproduction_result_ref(
                    state.run_id,
                    _synthetic_reproduction_attempt(
                        state,
                        round_number=next_round,
                        report=report,
                        error_code=error_code,
                    ),
                )
                return {
                    "test_patch_ref": None,
                    "reproduction_result_ref": result_ref,
                    "reproduction_round": next_round,
                    "model_calls": state.model_calls + execution.model_calls,
                    "tool_calls": state.tool_calls + len(source_files) + 1,
                    "usage": _add_usage(state, execution),
                    "last_error_code": error_code,
                }
            assert submitted is not None
            return {
                "test_patch_ref": submitted.artifact_ref,
                "fix_source_paths": generated.files_needed_for_fix,
                "reproduction_round": next_round,
                "model_calls": state.model_calls + execution.model_calls,
                "tool_calls": state.tool_calls + len(source_files) + 1,
                "usage": _add_usage(state, execution),
                "last_error_code": None,
            }

        def route_after_test_edit(state: AgentState) -> str:
            if state.last_error_code == "test_edit_security_rejected":
                return "finish"
            if state.last_error_code == "invalid_test_edit":
                return "revise" if state.reproduction_round < 2 else "finish"
            return "sandbox"

        async def run_reproduction_in_sandbox(state: AgentState) -> dict[str, object]:
            if (
                state.base_sha is None
                or state.source_snapshot_ref is None
                or state.test_patch_ref is None
                or state.reproduction_plan_ref is None
            ):
                raise ValueError("reproduction state is missing sandbox inputs")
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            patch = artifact_store.read_patch(state.test_patch_ref)
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            job = SandboxJob(
                job_id=uuid5(
                    NAMESPACE_URL,
                    f"mdtoword:{state.run_id}:reproduction:{state.reproduction_round}",
                ),
                run_id=state.run_id,
                job_type=JobType.REPRODUCE_TARGET,
                base_sha=state.base_sha,
                source_snapshot_sha256=snapshot.source_snapshot_sha256,
                test_patch_sha256=_sha256_bytes(patch),
                target_test_selector=plan.target_test_selector,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            with reproduction.telemetry.start_tool(
                ToolTrace(
                    operation="run-reproduction",
                    round=state.reproduction_round,
                    input_summary={
                        "job_id": str(job.job_id),
                        "target_test_selector": plan.target_test_selector,
                        "base_sha": state.base_sha,
                    },
                )
            ) as observation:
                try:
                    result = await reproduction.sandbox_client.submit(
                        SandboxArtifacts(
                            job=job,
                            source_archive=snapshot.archive_path.read_bytes(),
                            test_patch=patch,
                        )
                    )
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "sandbox_failed"),
                        error_type=type(exc).__name__,
                    )
                    raise
                observation.succeed(
                    {
                        "status": result.status.value,
                        "exit_code": result.exit_code,
                        "timed_out": result.timed_out,
                        "junit": (
                            {
                                "tests": result.junit_summary.tests,
                                "failures": result.junit_summary.failures,
                                "errors": result.junit_summary.errors,
                                "skipped": result.junit_summary.skipped,
                                "target_collected": result.junit_summary.target_collected,
                                "target_outcome": result.junit_summary.target_outcome.value,
                                "target_failure_type": result.junit_summary.target_failure_type,
                            }
                            if result.junit_summary
                            else None
                        ),
                    }
                )
            attempt = ReproductionAttemptArtifact(
                round=state.reproduction_round,
                test_patch_sha256=job.test_patch_sha256,
                files_needed_for_fix=state.fix_source_paths,
                extension_sync_required=False,
                sandbox_result=result,
            )
            result_ref = artifact_store.write_reproduction_result_ref(
                state.run_id,
                attempt,
            )
            return {
                "reproduction_result_ref": result_ref,
                "tool_calls": state.tool_calls + 1,
            }

        async def classify_reproduction(state: AgentState) -> dict[str, object]:
            if (
                state.reproduction_result_ref is None
                or state.reproduction_plan_ref is None
            ):
                raise ValueError("reproduction state is missing result")
            attempt = artifact_store.read_reproduction_result(
                state.reproduction_result_ref
            )
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            report = classify_reproduction_result(
                attempt.sandbox_result,
                expected_failure_kind=plan.expected_failure_kind,
                round_number=state.reproduction_round,
                target_test_selector=plan.target_test_selector,
            )
            result_ref = artifact_store.write_reproduction_result_ref(
                state.run_id,
                attempt.model_copy(update={"report": report}),
            )
            return {"reproduction_result_ref": result_ref}

        def route_after_reproduction(state: AgentState) -> str:
            if state.reproduction_result_ref is None:
                raise ValueError("reproduction state is missing classified result")
            report = artifact_store.read_reproduction_result(
                state.reproduction_result_ref
            ).report
            if report is None:
                raise ValueError("reproduction result is not classified")
            if report.disposition in {
                ReproductionDisposition.REPRODUCED,
                ReproductionDisposition.SECURITY_REJECTED,
            }:
                return "finish"
            return "revise" if state.reproduction_round < 2 else "finish"

        async def finish_reproduction(state: AgentState) -> dict[str, object]:
            if state.reproduction_result_ref is None:
                raise ValueError("reproduction state is missing final result")
            report = artifact_store.read_reproduction_result(
                state.reproduction_result_ref
            ).report
            if report is None:
                raise ValueError("reproduction result is not classified")
            feedback = await feedback_repository.get(state.feedback_id)
            if feedback is None:
                raise FeedbackNotFoundError(f"feedback {state.feedback_id} does not exist")
            if feedback.claim_token != state.claim_token:
                raise ClaimTokenMismatchError(
                    f"claim token does not own feedback {state.feedback_id}"
                )
            target = {
                ReproductionDisposition.REPRODUCED: FeedbackStatus.REPAIRING,
                ReproductionDisposition.SECURITY_REJECTED: FeedbackStatus.SECURITY_REJECTED,
            }.get(report.disposition, FeedbackStatus.CANNOT_REPRODUCE)
            if feedback.status is FeedbackStatus.REPRODUCING:
                await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=target,
                )
            elif feedback.status is not target:
                raise ClaimTokenMismatchError("feedback reproduction was finalized elsewhere")
            security_rejected = (
                report.disposition is ReproductionDisposition.SECURITY_REJECTED
            )
            reproduction_confirmed = (
                report.disposition is ReproductionDisposition.REPRODUCED
            )
            await run_repository.complete_reproduction(
                state.run_id,
                report,
                model_calls=state.model_calls,
                tool_calls=state.tool_calls,
                input_tokens=state.usage.input_tokens,
                output_tokens=state.usage.output_tokens,
                total_tokens=state.usage.total_tokens,
                estimated_cost=state.usage.estimated_cost,
                reproduction_confirmed=reproduction_confirmed,
                security_rejected=security_rejected,
            )
            return {
                "status": (
                    AgentRunStatus.SECURITY_REJECTED
                    if security_rejected
                    else (
                        AgentRunStatus.REPAIRING
                        if reproduction_confirmed
                        else AgentRunStatus.COMPLETED
                    )
                )
            }

        def route_after_reproduction_finish(state: AgentState) -> str:
            if repair is not None and state.status is AgentRunStatus.REPAIRING:
                return "repair"
            return "end"

        async def generate_fix_edit(state: AgentState) -> dict[str, object]:
            assert repair is not None
            if (
                state.task_artifact_ref is None
                or state.reproduction_plan_ref is None
                or state.reproduction_result_ref is None
                or state.source_snapshot_ref is None
                or state.test_patch_ref is None
            ):
                raise ValueError("repair state is missing reproduction context")
            task = artifact_store.read_task(state.task_artifact_ref)
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            reproduction_attempt = artifact_store.read_reproduction_result(
                state.reproduction_result_ref
            )
            if reproduction_attempt.report is None:
                raise ValueError("repair requires a classified reproduction")
            if not _budget_allows(state, repair, model_calls=1):
                return {"last_error_code": "budget_exhausted"}
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            reader = SourceReader(snapshot.root)
            requested_paths = state.fix_source_paths or tuple(
                item.path
                for item in plan.files_to_read
                if item.path.startswith("backend/app/")
            )
            if not requested_paths:
                requested_paths = ("backend/app/normalizer.py",)
            if (
                contains_mermaid_diagram(task.markdown_content)
                and plan.oracle.trusted_assertion_name()
                == "assert_minimum_drawing_count"
            ):
                # Mermaid 是预装的平台能力：模型可读取其受信 API，但仍不能修改实现或依赖。
                requested_paths = tuple(
                    dict.fromkeys((*requested_paths, "backend/app/mermaid_renderer.py"))
                )
            if not _budget_allows(
                state,
                repair,
                model_calls=1,
                tool_calls=len(requested_paths) + 1,
            ):
                return {"last_error_code": "budget_exhausted"}

            ranges = {item.path: item for item in plan.files_to_read}
            source_files = []
            for path in requested_paths:
                request = ranges.get(path)
                start_line, end_line = _fix_source_line_range(path, request)
                with repair.telemetry.start_tool(
                    ToolTrace(
                        operation="read-fix-source-file",
                        round=state.repair_round + 1,
                        input_summary={
                            "path": path,
                            "start_line": start_line,
                            "end_line": end_line,
                        },
                    )
                ) as observation:
                    try:
                        source = reader.read_source_file(
                            path,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    except Exception as exc:
                        observation.fail(
                            error_code=getattr(exc, "error_code", "source_read_failed"),
                            error_type=type(exc).__name__,
                        )
                        raise
                    observation.succeed(
                        {
                            "path": source.path,
                            "start_line": source.start_line,
                            "end_line": source.end_line,
                            "content_bytes": len(source.content.encode("utf-8")),
                        }
                    )
                    source_files.append(source)

            previous_attempt = (
                artifact_store.read_repair_result(state.repair_result_ref)
                if state.repair_result_ref is not None
                else None
            )
            test_patch = artifact_store.read_patch(state.test_patch_ref)
            execution = await generate_fix(
                task,
                plan=plan,
                reproduction_report=reproduction_attempt.report,
                source_files=tuple(source_files),
                test_patch_summary={
                    "sha256": _sha256_bytes(test_patch),
                    "changed_files": ["backend/tests/test_feedback_regressions.py"],
                },
                previous_report=previous_attempt.report if previous_attempt else None,
                previous_fix_summary=(
                    previous_attempt.fix_summary if previous_attempt else None
                ),
                provider=repair.fix_provider,
                timeout_seconds=repair.model_timeout_seconds,
            )
            next_model_calls = state.model_calls + execution.model_calls
            next_usage = _add_usage(state, execution)
            next_round = state.repair_round + 1
            if (
                next_model_calls > repair.max_model_calls
                or int(next_usage["total_tokens"]) > repair.max_total_tokens
            ):
                return {
                    "repair_round": next_round,
                    "model_calls": next_model_calls,
                    "tool_calls": state.tool_calls + len(source_files),
                    "usage": next_usage,
                    "last_error_code": "budget_exhausted",
                }

            generated = execution.output
            submitted = None
            edit_error: Exception | None = None
            with repair.telemetry.start_tool(
                ToolTrace(
                    operation="submit-fix-edits",
                    round=next_round,
                    input_summary={"edit_count": len(generated.edits)},
                )
            ) as observation:
                try:
                    submitted = reproduction.edit_tools.submit_fix_edits(
                        state.run_id,
                        snapshot.root,
                        generated.edits,
                    )
                    compose_validated_patch(
                        snapshot.root,
                        test_patch,
                        artifact_store.read_patch(submitted.artifact_ref),
                    )
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "fix_edit_rejected"),
                        error_type=type(exc).__name__,
                    )
                    edit_error = exc
                else:
                    observation.succeed(
                        {
                            "patch_sha256": submitted.sha256,
                            "changed_files": list(submitted.changed_files),
                            "added_lines": submitted.added_lines,
                        }
                    )
            common = {
                "repair_round": next_round,
                "model_calls": next_model_calls,
                "tool_calls": state.tool_calls + len(source_files) + 1,
                "usage": next_usage,
                "fix_summary": generated.summary,
                "risk": generated.risk_level,
            }
            if edit_error is not None:
                needs_human = isinstance(edit_error, ExternalDependencyError)
                security = isinstance(edit_error, PatchPolicyError) and not needs_human
                report = RepairReport(
                    disposition=(
                        RepairDisposition.NEEDS_HUMAN
                        if needs_human
                        else (
                            RepairDisposition.SECURITY_REJECTED
                            if security
                            else RepairDisposition.INVALID_RESULT
                        )
                    ),
                    round=next_round,
                    failure_code=(
                        "external_dependency_required"
                        if needs_human
                        else (
                            "fix_edit_security_rejected"
                            if security
                            else "invalid_fix_edit"
                        )
                    ),
                    failure_summary=_fix_edit_failure_summary(
                        edit_error,
                        needs_human=needs_human,
                        security=security,
                    ),
                )
                attempt = _synthetic_repair_attempt(
                    state,
                    round_number=next_round,
                    report=report,
                    summary=generated.summary,
                    risk=generated.risk_level,
                )
                return {
                    **common,
                    "fix_patch_ref": None,
                    "repair_result_ref": artifact_store.write_repair_result_ref(
                        state.run_id,
                        attempt,
                    ),
                    "last_error_code": report.failure_code,
                }
            assert submitted is not None
            return {
                **common,
                "fix_patch_ref": submitted.artifact_ref,
                "repair_result_ref": None,
                "last_error_code": None,
            }

        def route_after_fix_edit(state: AgentState) -> str:
            if state.last_error_code == "budget_exhausted":
                return "budget"
            if state.last_error_code == "external_dependency_required":
                return "finish"
            if state.last_error_code == "fix_edit_security_rejected":
                return "finish"
            if state.last_error_code == "invalid_fix_edit":
                return "revise" if state.repair_round < 2 else "finish"
            return "sandbox"

        async def run_target_validation(state: AgentState) -> dict[str, object]:
            assert repair is not None
            if not _budget_allows(state, repair, tool_calls=1):
                return {"last_error_code": "budget_exhausted"}
            if (
                state.base_sha is None
                or state.source_snapshot_ref is None
                or state.test_patch_ref is None
                or state.fix_patch_ref is None
                or state.reproduction_plan_ref is None
            ):
                raise ValueError("repair state is missing target validation inputs")
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            test_patch = artifact_store.read_patch(state.test_patch_ref)
            fix_patch = artifact_store.read_patch(state.fix_patch_ref)
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            job = SandboxJob(
                job_id=uuid5(
                    NAMESPACE_URL,
                    f"mdtoword:{state.run_id}:repair:{state.repair_round}",
                ),
                run_id=state.run_id,
                job_type=JobType.VALIDATE_TARGET,
                base_sha=state.base_sha,
                source_snapshot_sha256=snapshot.source_snapshot_sha256,
                test_patch_sha256=_sha256_bytes(test_patch),
                fix_patch_sha256=_sha256_bytes(fix_patch),
                target_test_selector=plan.target_test_selector,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            with repair.telemetry.start_tool(
                ToolTrace(
                    operation="run-target-validation",
                    round=state.repair_round,
                    input_summary={
                        "job_id": str(job.job_id),
                        "target_test_selector": plan.target_test_selector,
                    },
                )
            ) as observation:
                try:
                    result = await reproduction.sandbox_client.submit(
                        SandboxArtifacts(
                            job=job,
                            source_archive=snapshot.archive_path.read_bytes(),
                            test_patch=test_patch,
                            fix_patch=fix_patch,
                        )
                    )
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "sandbox_failed"),
                        error_type=type(exc).__name__,
                    )
                    raise
                observation.succeed(_sandbox_summary(result))
            attempt = RepairAttemptArtifact(
                round=state.repair_round,
                fix_patch_sha256=job.fix_patch_sha256,
                changed_files=compose_validated_patch(
                    snapshot.root,
                    test_patch,
                    fix_patch,
                ).changed_files,
                fix_summary=state.fix_summary or "backend repair",
                risk_level=state.risk,
                sandbox_result=result,
            )
            return {
                "repair_result_ref": artifact_store.write_repair_result_ref(
                    state.run_id,
                    attempt,
                ),
                "tool_calls": state.tool_calls + 1,
                "sandbox_duration_ms": state.sandbox_duration_ms + result.duration_ms,
                "last_error_code": (
                    "budget_exhausted"
                    if state.sandbox_duration_ms + result.duration_ms
                    > repair.max_sandbox_seconds * 1000
                    else None
                ),
            }

        async def classify_target(state: AgentState) -> dict[str, object]:
            if state.repair_result_ref is None:
                raise ValueError("repair state is missing target result")
            attempt = artifact_store.read_repair_result(state.repair_result_ref)
            report = classify_target_validation(
                attempt.sandbox_result,
                round_number=state.repair_round,
            )
            reference = artifact_store.write_repair_result_ref(
                state.run_id,
                attempt.model_copy(update={"report": report}),
            )
            return {
                "repair_result_ref": reference,
                "last_error_code": report.failure_code,
            }

        def route_after_target(state: AgentState) -> str:
            if state.last_error_code == "budget_exhausted":
                return "budget"
            if state.repair_result_ref is None:
                raise ValueError("repair state is missing classified target result")
            report = artifact_store.read_repair_result(state.repair_result_ref).report
            if report is None:
                raise ValueError("repair target result is not classified")
            if report.disposition is RepairDisposition.TARGET_PASSED:
                return "validate"
            if report.disposition is RepairDisposition.SECURITY_REJECTED:
                return "finish"
            return "revise" if state.repair_round < 2 else "finish"

        async def finish_repair_success(state: AgentState) -> dict[str, object]:
            if state.repair_result_ref is None:
                raise ValueError("repair state is missing successful target result")
            report = artifact_store.read_repair_result(state.repair_result_ref).report
            if report is None:
                raise ValueError("repair result is not classified")
            feedback = await _owned_feedback(
                feedback_repository,
                state.feedback_id,
                state.claim_token,
            )
            if feedback.status is FeedbackStatus.REPAIRING:
                await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=FeedbackStatus.VALIDATING,
                )
            elif feedback.status is not FeedbackStatus.VALIDATING:
                raise ClaimTokenMismatchError("feedback repair was finalized elsewhere")
            await run_repository.mark_validating(
                state.run_id,
                report,
                **_usage_arguments(state),
            )
            return {"status": AgentRunStatus.VALIDATING}

        async def finish_repair_failure(state: AgentState) -> dict[str, object]:
            if state.repair_result_ref is None:
                raise ValueError("repair state is missing failed result")
            report = artifact_store.read_repair_result(state.repair_result_ref).report
            if report is None:
                raise ValueError("repair result is not classified")
            security = report.disposition is RepairDisposition.SECURITY_REJECTED
            needs_human = report.disposition is RepairDisposition.NEEDS_HUMAN
            feedback = await _owned_feedback(
                feedback_repository,
                state.feedback_id,
                state.claim_token,
            )
            target = (
                FeedbackStatus.NEEDS_HUMAN
                if needs_human
                else (
                    FeedbackStatus.SECURITY_REJECTED
                    if security
                    else FeedbackStatus.FAILED
                )
            )
            if feedback.status is FeedbackStatus.REPAIRING:
                await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=target,
                    error_code=report.failure_code,
                    error_message=report.failure_summary,
                )
            elif feedback.status is not target:
                raise ClaimTokenMismatchError("feedback repair was finalized elsewhere")
            await run_repository.complete_repair_failure(
                state.run_id,
                report,
                security_rejected=security,
                **_usage_arguments(state),
            )
            return {
                "status": (
                    AgentRunStatus.SECURITY_REJECTED
                    if security
                    else AgentRunStatus.COMPLETED
                ),
                "route": (
                    GateRoute.NEEDS_HUMAN.value if needs_human else state.route
                ),
            }

        async def validate_final(state: AgentState) -> dict[str, object]:
            assert repair is not None
            if not _budget_allows(state, repair, tool_calls=3):
                return {"last_error_code": "budget_exhausted"}
            if (
                state.base_sha is None
                or state.source_snapshot_ref is None
                or state.test_patch_ref is None
                or state.fix_patch_ref is None
                or state.reproduction_plan_ref is None
            ):
                raise ValueError("final validation state is incomplete")
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            plan = artifact_store.read_reproduction_plan(state.reproduction_plan_ref)
            test_patch = artifact_store.read_patch(state.test_patch_ref)
            fix_patch = artifact_store.read_patch(state.fix_patch_ref)
            validated = compose_validated_patch(snapshot.root, test_patch, fix_patch)
            normalized_test_patch = normalize_authorized_patch(
                snapshot.root,
                test_patch,
            )
            validated_ref = artifact_store.write_patch_ref(
                state.run_id,
                "validated.patch",
                validated.content,
            )

            results = []
            specifications = (
                ("reproduce-baseline", JobType.REPRODUCE_TARGET, False),
                ("run-target-tests", JobType.VALIDATE_TARGET, True),
                ("run-full-tests", JobType.VALIDATE_FULL, True),
            )
            duration_ms = state.sandbox_duration_ms
            for operation, job_type, include_fix in specifications:
                if duration_ms >= repair.max_sandbox_seconds * 1000:
                    return {
                        "tool_calls": state.tool_calls + len(results),
                        "sandbox_duration_ms": duration_ms,
                        "last_error_code": "budget_exhausted",
                    }
                job = SandboxJob(
                    job_id=uuid5(
                        NAMESPACE_URL,
                        f"mdtoword:{state.run_id}:final:{operation}",
                    ),
                    run_id=state.run_id,
                    job_type=job_type,
                    base_sha=state.base_sha,
                    source_snapshot_sha256=snapshot.source_snapshot_sha256,
                    test_patch_sha256=_sha256_bytes(test_patch),
                    fix_patch_sha256=(
                        _sha256_bytes(fix_patch) if include_fix else None
                    ),
                    target_test_selector=plan.target_test_selector,
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
                with repair.telemetry.start_tool(
                    ToolTrace(
                        operation=operation,
                        round=None,
                        input_summary={
                            "job_id": str(job.job_id),
                            "base_sha": state.base_sha,
                        },
                    )
                ) as observation:
                    try:
                        result = await reproduction.sandbox_client.submit(
                            SandboxArtifacts(
                                job=job,
                                source_archive=snapshot.archive_path.read_bytes(),
                                test_patch=test_patch,
                                fix_patch=fix_patch if include_fix else None,
                            )
                        )
                    except Exception as exc:
                        observation.fail(
                            error_code=getattr(exc, "error_code", "sandbox_failed"),
                            error_type=type(exc).__name__,
                        )
                        raise
                    observation.succeed(_sandbox_summary(result))
                expected_workspace_hash = (
                    validated.sha256 if include_fix else normalized_test_patch.sha256
                )
                if result.workspace_diff_sha256 not in {
                    None,
                    expected_workspace_hash,
                }:
                    result = result.model_copy(
                        update={
                            "status": SandboxStatus.SECURITY_REJECTED,
                            "error_code": "workspace_diff_mismatch",
                        }
                    )
                results.append(result)
                duration_ms += result.duration_ms

            if duration_ms > repair.max_sandbox_seconds * 1000:
                return {
                    "tool_calls": state.tool_calls + 3,
                    "sandbox_duration_ms": duration_ms,
                    "last_error_code": "budget_exhausted",
                }

            baseline_result, target_result, full_result = results
            trusted_check = plan.oracle.trusted_assertion_name() or plan.oracle.kind.value
            validation = build_validation_result(
                base_sha=state.base_sha,
                source_snapshot_sha256=snapshot.source_snapshot_sha256,
                test_patch_sha256=_sha256_bytes(test_patch),
                fix_patch_sha256=_sha256_bytes(fix_patch),
                target_test_selector=plan.target_test_selector,
                expected_failure_kind=plan.expected_failure_kind,
                trusted_docx_check=trusted_check,
                baseline_result=baseline_result,
                target_result=target_result,
                full_result=full_result,
                baseline_skipped=repair.baseline_skipped,
                changed_files=validated.changed_files,
                validated_patch_ref=validated_ref,
                validated_patch_sha256=validated.sha256,
            )
            validation_ref = artifact_store.write_validation_ref(
                state.run_id,
                validation,
            )
            return {
                "validation_result_ref": validation_ref,
                "validated_patch_sha256": (
                    validated.sha256 if validation.passed else None
                ),
                "tool_calls": state.tool_calls + 3,
                "sandbox_duration_ms": duration_ms,
                "last_error_code": validation.failure_code,
            }

        def route_after_final_validation(state: AgentState) -> str:
            if state.last_error_code == "budget_exhausted":
                return "budget"
            return "finish"

        async def finish_validation(state: AgentState) -> dict[str, object]:
            if state.validation_result_ref is None:
                raise ValueError("final validation result is missing")
            validation = artifact_store.read_validation(state.validation_result_ref)
            feedback = await _owned_feedback(
                feedback_repository,
                state.feedback_id,
                state.claim_token,
            )
            target = FeedbackStatus.VALIDATED if validation.passed else FeedbackStatus.FAILED
            if feedback.status is FeedbackStatus.VALIDATING:
                feedback = await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=target,
                    error_code=validation.failure_code,
                    error_message=validation.failure_summary,
                )
            elif feedback.status not in {target, FeedbackStatus.PUBLISHING}:
                raise ClaimTokenMismatchError("feedback validation was finalized elsewhere")
            if publishing is not None and validation.passed:
                if feedback.status is FeedbackStatus.VALIDATED:
                    await feedback_repository.transition(
                        feedback.id,
                        claim_token=state.claim_token,
                        target=FeedbackStatus.PUBLISHING,
                    )
                elif feedback.status is not FeedbackStatus.PUBLISHING:
                    raise ClaimTokenMismatchError(
                        "feedback publication was started elsewhere"
                    )
            await run_repository.complete_validation(
                state.run_id,
                validation,
                publish_pending=publishing is not None,
                **_usage_arguments(state),
            )
            return {
                "status": (
                    AgentRunStatus.PUBLISHING
                    if publishing is not None and validation.passed
                    else AgentRunStatus.COMPLETED
                )
            }

        def route_after_validation_finish(state: AgentState) -> str:
            return "publish" if state.status is AgentRunStatus.PUBLISHING else "end"

        async def publish_pull_request(state: AgentState) -> dict[str, object]:
            assert publishing is not None
            if (
                state.validation_result_ref is None
                or state.source_snapshot_ref is None
                or state.repair_result_ref is None
            ):
                raise ValueError("publication state is missing validated artifacts")
            validation = artifact_store.read_validation(state.validation_result_ref)
            if not validation.passed:
                raise PublicationError("publisher received failed validation")
            patch = artifact_store.read_patch(validation.validated_patch_ref)
            snapshot = reproduction.source_workspace.resolve(state.source_snapshot_ref)
            try:
                # 发布前重新物化并校验已通过测试的补丁，防止 Artifact 被替换或文件集合漂移。
                materialized = materialize_validated_files(
                    snapshot.root,
                    patch,
                    expected_sha256=validation.validated_patch_sha256,
                    expected_files=validation.changed_files,
                )
            except PatchPolicyError as exc:
                raise PublicationError(
                    "validated patch failed publication integrity checks"
                ) from exc

            run = await run_repository.get(state.run_id)
            if run is None or run.category is None:
                raise PublicationError("publication run summary is incomplete")
            repair_attempt = artifact_store.read_repair_result(
                state.repair_result_ref
            )
            request = PublicationRequest(
                feedback_id=state.feedback_id,
                validation=validation,
                validated_patch=patch,
                files=tuple(
                    PublicationFile(path=item.path, content=item.content)
                    for item in materialized
                ),
                evidence=PublicationEvidence(
                    category=GateCategory(run.category),
                    risk=state.risk,
                    graph_version=run.graph_version,
                    policy_version=run.policy_version,
                    prompt_versions=run.prompt_versions,
                    provider=run.provider or "unknown",
                    model=run.model or "unknown",
                    model_calls=state.model_calls,
                    tool_calls=state.tool_calls,
                    input_tokens=state.usage.input_tokens,
                    output_tokens=state.usage.output_tokens,
                    total_tokens=state.usage.total_tokens,
                    estimated_cost=str(state.usage.estimated_cost),
                    extension_sync_required=repair_attempt.extension_sync_required,
                    trace_id=state.trace_id,
                    trace_url=publishing.trace_url_template.format(
                        trace_id=state.trace_id
                    ),
                ),
            )
            with publishing.telemetry.start_tool(
                ToolTrace(
                    operation="publish-pr",
                    round=None,
                    input_summary={
                        "feedback_id_prefix": str(state.feedback_id)[:8],
                        "base_sha": validation.base_sha,
                        "validated_patch_sha256": validation.validated_patch_sha256,
                    },
                )
            ) as observation:
                try:
                    result = await publishing.publisher.publish(request)
                except Exception as exc:
                    observation.fail(
                        error_code=getattr(exc, "error_code", "publication_failed"),
                        error_type=type(exc).__name__,
                    )
                    raise
                observation.succeed(
                    {
                        "disposition": result.disposition.value,
                        "branch": result.branch,
                        "pr_number": result.pr_number,
                        "reused": result.reused,
                    }
                )
            return {
                "publication_result_ref": artifact_store.write_publication_ref(
                    state.run_id,
                    result,
                ),
                "pr_url": result.pr_url,
                "tool_calls": state.tool_calls + 1,
                "last_error_code": (
                    "stale_base"
                    if result.disposition is PublicationDisposition.STALE_BASE
                    else None
                ),
            }

        async def finish_publication(state: AgentState) -> dict[str, object]:
            if state.publication_result_ref is None:
                raise ValueError("publication result is missing")
            result = artifact_store.read_publication(state.publication_result_ref)
            if result.disposition is PublicationDisposition.STALE_BASE:
                feedback = await feedback_repository.get(state.feedback_id)
                if feedback is None:
                    raise FeedbackNotFoundError(
                        f"feedback {state.feedback_id} does not exist"
                    )
                if (
                    feedback.claim_token != state.claim_token
                    and feedback.status is not FeedbackStatus.PENDING
                ):
                    raise ClaimTokenMismatchError(
                        "stale publication is owned by another run"
                    )
                if feedback.status is FeedbackStatus.PUBLISHING:
                    feedback = await feedback_repository.transition(
                        feedback.id,
                        claim_token=state.claim_token,
                        target=FeedbackStatus.STALE_BASE,
                        error_code="stale_base",
                        error_message="repository main changed before publication",
                    )
                if feedback.status is FeedbackStatus.STALE_BASE:
                    # 主分支漂移只自动重排一次，连续漂移交给人工处理以避免无限重试。
                    requeue_target = (
                        FeedbackStatus.PENDING
                        if feedback.stale_requeue_count == 0
                        else FeedbackStatus.NEEDS_HUMAN
                    )
                    feedback = await feedback_repository.transition(
                        feedback.id,
                        claim_token=state.claim_token,
                        target=requeue_target,
                        error_code=(
                            None
                            if requeue_target is FeedbackStatus.PENDING
                            else "stale_base_repeated"
                        ),
                        error_message=(
                            None
                            if requeue_target is FeedbackStatus.PENDING
                            else "repository main changed twice before publication"
                        ),
                    )
                elif feedback.status not in {
                    FeedbackStatus.PENDING,
                    FeedbackStatus.NEEDS_HUMAN,
                }:
                    raise ClaimTokenMismatchError(
                        "stale publication was finalized elsewhere"
                    )
                await run_repository.complete_stale_base(
                    state.run_id,
                    tool_calls=state.tool_calls,
                )
                return {
                    "status": AgentRunStatus.STALE_BASE,
                    "route": (
                        GateRoute.NEEDS_HUMAN.value
                        if feedback.status is FeedbackStatus.NEEDS_HUMAN
                        else state.route
                    ),
                    "last_error_code": "stale_base",
                }

            feedback = await _owned_feedback(
                feedback_repository,
                state.feedback_id,
                state.claim_token,
            )
            if result.pr_url is None:
                raise PublicationError("opened publication is missing pull request URL")
            if feedback.status is FeedbackStatus.PUBLISHING:
                await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=FeedbackStatus.PR_OPENED,
                    pr_url=result.pr_url,
                )
            elif feedback.status is not FeedbackStatus.PR_OPENED:
                raise ClaimTokenMismatchError("publication was finalized elsewhere")
            await run_repository.complete_publication(
                state.run_id,
                pr_url=result.pr_url,
                tool_calls=state.tool_calls,
            )
            return {
                "status": AgentRunStatus.COMPLETED,
                "pr_url": result.pr_url,
                "last_error_code": None,
            }

        async def finish_budget_exhausted(state: AgentState) -> dict[str, object]:
            feedback = await _owned_feedback(
                feedback_repository,
                state.feedback_id,
                state.claim_token,
            )
            if feedback.status in {FeedbackStatus.REPAIRING, FeedbackStatus.VALIDATING}:
                await feedback_repository.transition(
                    feedback.id,
                    claim_token=state.claim_token,
                    target=FeedbackStatus.FAILED,
                    error_code="budget_exhausted",
                    error_message="run budget was exhausted",
                )
            elif feedback.status is not FeedbackStatus.FAILED:
                raise ClaimTokenMismatchError("feedback budget status was finalized elsewhere")
            await run_repository.exhaust_budget(
                state.run_id,
                **_usage_arguments(state),
            )
            return {
                "status": AgentRunStatus.BUDGET_EXHAUSTED,
                "last_error_code": "budget_exhausted",
            }

        builder.add_node("prepare_source", prepare_source)
        builder.add_node("plan_reproduction", create_reproduction_plan)
        builder.add_node("generate_test_edit", generate_test_edit)
        builder.add_node("run_reproduction_in_sandbox", run_reproduction_in_sandbox)
        builder.add_node("classify_reproduction", classify_reproduction)
        builder.add_node("finish_reproduction", finish_reproduction)
        builder.add_conditional_edges(
            "route_feedback",
            route_after_gate,
            {"prepare_source": "prepare_source", "end": END},
        )
        builder.add_edge("prepare_source", "plan_reproduction")
        builder.add_edge("plan_reproduction", "generate_test_edit")
        builder.add_conditional_edges(
            "generate_test_edit",
            route_after_test_edit,
            {
                "revise": "generate_test_edit",
                "sandbox": "run_reproduction_in_sandbox",
                "finish": "finish_reproduction",
            },
        )
        builder.add_edge("run_reproduction_in_sandbox", "classify_reproduction")
        builder.add_conditional_edges(
            "classify_reproduction",
            route_after_reproduction,
            {"revise": "generate_test_edit", "finish": "finish_reproduction"},
        )
        if repair is None:
            builder.add_edge("finish_reproduction", END)
        else:
            builder.add_node("generate_fix_edit", generate_fix_edit)
            builder.add_node("run_target_validation", run_target_validation)
            builder.add_node("classify_target", classify_target)
            builder.add_node("finish_repair_success", finish_repair_success)
            builder.add_node("finish_repair_failure", finish_repair_failure)
            builder.add_node("validate_final", validate_final)
            builder.add_node("finish_validation", finish_validation)
            builder.add_node("finish_budget_exhausted", finish_budget_exhausted)
            if publishing is not None:
                builder.add_node("publish_pull_request", publish_pull_request)
                builder.add_node("finish_publication", finish_publication)
            builder.add_conditional_edges(
                "finish_reproduction",
                route_after_reproduction_finish,
                {"repair": "generate_fix_edit", "end": END},
            )
            builder.add_conditional_edges(
                "generate_fix_edit",
                route_after_fix_edit,
                {
                    "revise": "generate_fix_edit",
                    "sandbox": "run_target_validation",
                    "finish": "finish_repair_failure",
                    "budget": "finish_budget_exhausted",
                },
            )
            builder.add_conditional_edges(
                "run_target_validation",
                lambda state: (
                    "budget"
                    if state.last_error_code == "budget_exhausted"
                    else "classify"
                ),
                {
                    "budget": "finish_budget_exhausted",
                    "classify": "classify_target",
                },
            )
            builder.add_conditional_edges(
                "classify_target",
                route_after_target,
                {
                    "revise": "generate_fix_edit",
                    "validate": "finish_repair_success",
                    "finish": "finish_repair_failure",
                    "budget": "finish_budget_exhausted",
                },
            )
            builder.add_edge("finish_repair_success", "validate_final")
            builder.add_conditional_edges(
                "validate_final",
                route_after_final_validation,
                {
                    "budget": "finish_budget_exhausted",
                    "finish": "finish_validation",
                },
            )
            builder.add_edge("finish_repair_failure", END)
            if publishing is None:
                builder.add_edge("finish_validation", END)
            else:
                builder.add_conditional_edges(
                    "finish_validation",
                    route_after_validation_finish,
                    {"publish": "publish_pull_request", "end": END},
                )
                builder.add_edge("publish_pull_request", "finish_publication")
                builder.add_edge("finish_publication", END)
            builder.add_edge("finish_budget_exhausted", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after else None,
        name="feedback-agent" if reproduction is not None else "feedback-gate",
    )


def _add_usage(state: AgentState, execution: object) -> dict[str, Decimal | int]:
    return {
        "input_tokens": state.usage.input_tokens + int(getattr(execution, "input_tokens")),
        "output_tokens": state.usage.output_tokens + int(getattr(execution, "output_tokens")),
        "total_tokens": state.usage.total_tokens + int(getattr(execution, "total_tokens")),
        "estimated_cost": state.usage.estimated_cost
        + Decimal(getattr(execution, "estimated_cost")),
    }


def _sha256_bytes(content: bytes) -> str:
    from hashlib import sha256

    return sha256(content).hexdigest()


def _budget_allows(
    state: AgentState,
    limits: RepairDependencies,
    *,
    model_calls: int = 0,
    tool_calls: int = 0,
) -> bool:
    return (
        state.model_calls + model_calls <= limits.max_model_calls
        and state.tool_calls + tool_calls <= limits.max_tool_calls
        and state.usage.total_tokens <= limits.max_total_tokens
        and state.sandbox_duration_ms <= limits.max_sandbox_seconds * 1000
    )


def _usage_arguments(state: AgentState) -> dict[str, object]:
    return {
        "model_calls": state.model_calls,
        "tool_calls": state.tool_calls,
        "input_tokens": state.usage.input_tokens,
        "output_tokens": state.usage.output_tokens,
        "total_tokens": state.usage.total_tokens,
        "estimated_cost": state.usage.estimated_cost,
    }


async def _owned_feedback(
    repository: FeedbackRepository,
    feedback_id: UUID,
    claim_token: UUID,
):
    feedback = await repository.get(feedback_id)
    if feedback is None:
        raise FeedbackNotFoundError(f"feedback {feedback_id} does not exist")
    if feedback.claim_token != claim_token:
        raise ClaimTokenMismatchError(f"claim token does not own feedback {feedback_id}")
    return feedback


def _sandbox_summary(result: SandboxResult) -> dict[str, object]:
    junit = result.junit_summary
    return {
        "status": result.status.value,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_ms": result.duration_ms,
        "junit": (
            {
                "tests": junit.tests,
                "failures": junit.failures,
                "errors": junit.errors,
                "skipped": junit.skipped,
                "target_collected": junit.target_collected,
                "target_outcome": junit.target_outcome.value,
            }
            if junit
            else None
        ),
    }


def _fix_source_line_range(
    path: str,
    request: SourceReadRequest | None,
) -> tuple[int, int]:
    """修复编辑必须看到固定快照中的完整目标，避免依据文件头猜测 search 文本。"""

    if path in {
        "backend/app/normalizer.py",
        "backend/app/pandoc_runner.py",
        "backend/app/mermaid_renderer.py",
    }:
        return 1, 1000
    if request is None:
        return 1, 1000
    return request.start_line, request.end_line


def _fix_edit_failure_summary(
    error: Exception,
    *,
    needs_human: bool,
    security: bool,
) -> str:
    """仅把受信本地校验器的稳定原因交给下一轮，不回显模型编辑或源码。"""

    if needs_human:
        return "generated fix requires an external dependency or deployment change"
    if security:
        return "generated fix edit violates patch policy"
    if isinstance(error, InvalidEditError):
        return f"generated fix edit was rejected: {error}"
    return "generated fix edit was rejected by local policy"


def _synthetic_repair_attempt(
    state: AgentState,
    *,
    round_number: int,
    report: RepairReport,
    summary: str,
    risk: RiskLevel,
) -> RepairAttemptArtifact:
    now = datetime.now(UTC)
    return RepairAttemptArtifact(
        round=round_number,
        fix_patch_sha256="0" * 64,
        fix_summary=summary,
        risk_level=risk,
        sandbox_result=SandboxResult(
            job_id=uuid5(
                NAMESPACE_URL,
                f"mdtoword:{state.run_id}:fix-edit:{round_number}",
            ),
            status=(
                SandboxStatus.SECURITY_REJECTED
                if report.disposition is RepairDisposition.SECURITY_REJECTED
                else SandboxStatus.FAILED
            ),
            started_at=now,
            finished_at=now,
            duration_ms=0,
            error_code=report.failure_code,
        ),
        report=report,
    )


def _synthetic_reproduction_attempt(
    state: AgentState,
    *,
    round_number: int,
    report: ReproductionReport,
    error_code: str,
) -> ReproductionAttemptArtifact:
    from agent.sandbox.contracts import SandboxResult, SandboxStatus

    now = datetime.now(UTC)
    return ReproductionAttemptArtifact(
        round=round_number,
        test_patch_sha256="0" * 64,
        sandbox_result=SandboxResult(
            job_id=uuid5(
                NAMESPACE_URL,
                f"mdtoword:{state.run_id}:test-edit:{round_number}",
            ),
            status=(
                SandboxStatus.SECURITY_REJECTED
                if report.disposition is ReproductionDisposition.SECURITY_REJECTED
                else SandboxStatus.FAILED
            ),
            started_at=now,
            finished_at=now,
            duration_ms=0,
            error_code=error_code,
        ),
        report=report,
    )
