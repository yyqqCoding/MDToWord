from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.domain.enums import AgentRunStatus, FeedbackStatus, GateRoute
from agent.domain.errors import (
    ClaimTokenMismatchError,
    FeedbackNotFoundError,
    InvalidEditError,
    PatchPolicyError,
    SourceAccessError,
)
from agent.gate import execute_feedback_gate
from agent.providers.base import ModelProvider
from agent.reproduction import generate_reproduction_test, plan_reproduction
from agent.repositories.base import AgentRunRepository, FeedbackRepository
from agent.sandbox.client import SandboxClient
from agent.sandbox.contracts import JobType, SandboxArtifacts, SandboxJob
from agent.state import AgentState
from agent.telemetry.base import NoopTelemetry, Telemetry, ToolTrace
from agent.tools.edits import StructuredEditTools
from agent.tools.source import SourceReader
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.preparation import SourceWorkspace
from agent.domain.reproduction import (
    ReproductionAttemptArtifact,
    ReproductionDisposition,
    ReproductionReport,
    classify_reproduction_result,
)


GRAPH_VERSION = "agent-graph-v2"
POLICY_VERSION = "reproduction-policy-v2"

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


def build_gate_graph(
    *,
    feedback_repository: FeedbackRepository,
    run_repository: AgentRunRepository,
    provider: ModelProvider,
    artifact_store: ArtifactStore,
    checkpointer: BaseCheckpointSaver,
    min_confidence: float,
    reproduction: ReproductionDependencies | None = None,
    interrupt_after: Sequence[str] | None = None,
):
    """构建 Gate Graph；配置阶段 D 依赖时仅让已接受后端缺陷进入复现子图。"""

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
                            error_code=getattr(exc, "error_code", "source_read_failed"),
                            error_type=type(exc).__name__,
                        )
                        raise
                    observation.succeed(
                        {
                            "path": source_item.path,
                            "start_line": source_item.start_line,
                            "end_line": source_item.end_line,
                            "content_bytes": len(source_item.content.encode("utf-8")),
                        }
                    )
                    source_items.append(source_item)
            source_files = tuple(source_items)
            previous_report = None
            if state.reproduction_result_ref is not None:
                previous_report = artifact_store.read_reproduction_result(
                    state.reproduction_result_ref
                ).report
            execution = await generate_reproduction_test(
                task,
                plan=plan,
                source_files=source_files,
                previous_report=previous_report,
                provider=reproduction.test_provider,
                timeout_seconds=reproduction.model_timeout_seconds,
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
        builder.add_edge("finish_reproduction", END)
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
