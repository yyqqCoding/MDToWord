from collections.abc import Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.domain.enums import AgentRunStatus, FeedbackStatus, GateRoute
from agent.domain.errors import ClaimTokenMismatchError, FeedbackNotFoundError
from agent.gate import execute_feedback_gate
from agent.providers.base import ModelProvider
from agent.repositories.base import AgentRunRepository, FeedbackRepository
from agent.state import AgentState
from agent.workspace.artifacts import ArtifactStore


GRAPH_VERSION = "gate-graph-v1"
POLICY_VERSION = "gate-policy-v1"

_ROUTE_TO_FEEDBACK_STATUS = {
    GateRoute.ACCEPTED_BACKEND_BUG: FeedbackStatus.REPRODUCING,
    GateRoute.REJECTED_IRRELEVANT: FeedbackStatus.REJECTED_IRRELEVANT,
    GateRoute.QUARANTINED_SECURITY: FeedbackStatus.QUARANTINED_SECURITY,
    GateRoute.OUT_OF_SCOPE: FeedbackStatus.OUT_OF_SCOPE,
    GateRoute.NEEDS_HUMAN: FeedbackStatus.NEEDS_HUMAN,
    GateRoute.DUPLICATE: FeedbackStatus.DUPLICATE,
}


def build_gate_graph(
    *,
    feedback_repository: FeedbackRepository,
    run_repository: AgentRunRepository,
    provider: ModelProvider,
    artifact_store: ArtifactStore,
    checkpointer: BaseCheckpointSaver,
    min_confidence: float,
    interrupt_after: Sequence[str] | None = None,
):
    """构建 B2 Gate-only Graph；所有节点只返回允许写入 State 的字段。"""

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
        await run_repository.complete_gate(
            state.run_id,
            result,
            input_tokens=state.usage.input_tokens,
            output_tokens=state.usage.output_tokens,
            total_tokens=state.usage.total_tokens,
            estimated_cost=state.usage.estimated_cost,
        )
        return {"status": AgentRunStatus.COMPLETED}

    builder = StateGraph(AgentState)
    builder.add_node("start_gate", start_gate)
    builder.add_node("classify_gate", classify_gate)
    builder.add_node("route_feedback", route_feedback)
    builder.add_edge(START, "start_gate")
    builder.add_edge("start_gate", "classify_gate")
    builder.add_edge("classify_gate", "route_feedback")
    builder.add_edge("route_feedback", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after else None,
        name="feedback-gate",
    )
