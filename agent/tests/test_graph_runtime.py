import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.controller import GateController
from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    FeedbackType,
    GateCategory,
    GateIntent,
    GateRoute,
)
from agent.domain.gate import GateClassification
from agent.domain.errors import ModelAuthError
from agent.domain.models import FeedbackRecord
from agent.providers.base import StructuredModelResponse
from agent.providers.fake import FakeModelProvider
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.workspace.artifacts import ArtifactStore


def make_feedback(*, description: str = "导出的 Word 没有表格") -> FeedbackRecord:
    return FeedbackRecord(
        id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="| A | B |\n|---|---|\n| 1 | 2 |",
        description=description,
        contact="user@example.com",
    )


def accepted_classification() -> GateClassification:
    return GateClassification(
        intent=GateIntent.BUG_REPORT,
        category=GateCategory.TABLE_PARSING,
        relevance=0.98,
        sufficient_information=True,
        injection_suspected=False,
        requires_extension_change=False,
        reason="后端导出表格结构错误",
    )


def test_gate_graph_persists_only_small_state_and_finalizes_route(tmp_path: Path):
    async def scenario():
        feedback = make_feedback()
        feedback_repository = FakeFeedbackRepository([feedback])
        claimed = await feedback_repository.claim_next(
            now=feedback.created_at,
            lease_seconds=60,
            max_attempts=3,
        )
        assert claimed is not None
        run_repository = FakeAgentRunRepository()
        checkpointer = InMemorySaver()
        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=FakeModelProvider([accepted_classification()]),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=checkpointer,
        )

        outcome = await controller.start(claimed)
        snapshot = await controller.graph.aget_state(
            {"configurable": {"thread_id": str(outcome.run_id)}}
        )
        return (
            outcome,
            snapshot.values,
            await feedback_repository.get(feedback.id),
            await run_repository.get(outcome.run_id),
            sorted(path.name for path in (tmp_path / str(outcome.run_id)).iterdir()),
        )

    outcome, state, stored_feedback, stored_run, artifact_names = asyncio.run(scenario())

    assert outcome.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert stored_feedback is not None
    assert stored_feedback.status is FeedbackStatus.REPRODUCING
    assert stored_run is not None
    assert stored_run.status is AgentRunStatus.COMPLETED
    assert state["status"] == AgentRunStatus.COMPLETED
    assert state["tool_calls"] == 0
    assert "markdown_content" not in state
    assert "description" not in state
    assert "contact" not in state
    assert artifact_names == ["gate.json", "task.redacted.json"]


def test_gate_graph_resumes_after_classification_without_second_model_call(
    tmp_path: Path,
):
    async def scenario():
        feedback = make_feedback()
        feedback_repository = FakeFeedbackRepository([feedback])
        claimed = await feedback_repository.claim_next(
            now=feedback.created_at,
            lease_seconds=60,
            max_attempts=3,
        )
        assert claimed is not None
        provider = FakeModelProvider([accepted_classification()])
        run_repository = FakeAgentRunRepository()
        checkpointer = InMemorySaver()
        first = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=provider,
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=checkpointer,
            interrupt_after=["classify_gate"],
        )
        interrupted = await first.start(claimed)
        gating_feedback = await feedback_repository.get(feedback.id)
        gating_run = await run_repository.get(interrupted.run_id)

        resumed_controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=provider,
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=checkpointer,
        )
        resumed = await resumed_controller.resume(interrupted.run_id)
        return (
            provider,
            interrupted,
            resumed,
            gating_feedback,
            gating_run,
            await feedback_repository.get(feedback.id),
        )

    provider, interrupted, resumed, gating_feedback, gating_run, final_feedback = (
        asyncio.run(scenario())
    )

    assert interrupted.completed is False
    assert gating_feedback is not None
    assert gating_feedback.status is FeedbackStatus.GATING
    assert gating_run is not None
    assert gating_run.status is AgentRunStatus.GATING
    assert resumed.completed is True
    assert final_feedback is not None
    assert final_feedback.status is FeedbackStatus.REPRODUCING
    assert len(provider.requests) == 1


def test_gate_graph_persists_real_provider_usage_and_deterministic_trace_id(
    tmp_path: Path,
):
    class UsageProvider:
        provider = "openai_compatible"
        model = "compatible-model"

        async def generate_structured(
            self,
            messages,
            response_schema,
            *,
            tools,
            timeout_seconds,
        ):
            del messages, response_schema, tools, timeout_seconds
            return StructuredModelResponse(
                output=accepted_classification(),
                provider=self.provider,
                model=self.model,
                provider_request_id="request-1",
                input_tokens=100,
                output_tokens=25,
                total_tokens=125,
                estimated_cost=Decimal("0.0025"),
            )

    async def scenario():
        feedback = make_feedback()
        feedback_repository = FakeFeedbackRepository([feedback])
        claimed = await feedback_repository.claim_next(
            now=feedback.created_at,
            lease_seconds=60,
            max_attempts=3,
        )
        assert claimed is not None
        run_repository = FakeAgentRunRepository()
        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=UsageProvider(),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=InMemorySaver(),
        )
        outcome = await controller.start(claimed)
        return await run_repository.get(outcome.run_id)

    run = asyncio.run(scenario())

    assert run is not None
    assert run.input_tokens == 100
    assert run.output_tokens == 25
    assert run.total_tokens == 125
    assert run.estimated_cost == Decimal("0.0025")
    assert run.langfuse_trace_id == run.trace_id
    assert len(run.trace_id) == 32
    int(run.trace_id, 16)


def test_provider_failure_terminalizes_run_and_feedback(tmp_path: Path):
    class FailingProvider:
        provider = "openai_compatible"
        model = "compatible-model"

        async def generate_structured(
            self,
            messages,
            response_schema,
            *,
            tools,
            timeout_seconds,
        ):
            del messages, response_schema, tools, timeout_seconds
            raise ModelAuthError("safe message")

    async def scenario():
        feedback = make_feedback()
        feedback_repository = FakeFeedbackRepository([feedback])
        claimed = await feedback_repository.claim_next(
            now=feedback.created_at,
            lease_seconds=60,
            max_attempts=3,
        )
        assert claimed is not None
        run_repository = FakeAgentRunRepository()
        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=FailingProvider(),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=InMemorySaver(),
        )
        with pytest.raises(ModelAuthError):
            await controller.start(claimed)
        run = await run_repository.find_resumable()
        stored_feedback = await feedback_repository.get(feedback.id)
        all_runs = [
            item
            for item in run_repository._records.values()
        ]
        return run, stored_feedback, all_runs[0]

    resumable, feedback, failed_run = asyncio.run(scenario())

    assert resumable is None
    assert feedback is not None
    assert feedback.status is FeedbackStatus.FAILED
    assert feedback.last_error_code == "auth_error"
    assert failed_run.status is AgentRunStatus.FAILED
    assert failed_run.error_message == "ModelAuthError"
