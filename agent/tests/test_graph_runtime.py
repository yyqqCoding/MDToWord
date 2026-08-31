import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.controller import GateController
from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    FeedbackType,
    GateArea,
    GateCategory,
    GateIntent,
    GateRoute,
)
from agent.domain.gate import GateClassification
from agent.domain.failures import FailureHandling, FailureKind, FailureSnapshot
from agent.domain.errors import (
    BudgetExceededError,
    ModelAuthError,
    ModelTimeoutError,
    SourceAccessError,
    SourceAuthenticationError,
)
from agent.domain.models import AgentRunRecord, FeedbackRecord
from agent.graph import PublishingDependencies
from agent.publishing.contracts import IssuePublicationResult
from agent.providers.base import StructuredModelResponse
from agent.providers.fake import FakeModelProvider
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.state import AgentState, UsageTotals
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


def test_issue_route_publishes_without_source_or_sandbox(tmp_path: Path):
    class FakeIssuePublisher:
        def __init__(self) -> None:
            self.requests = []

        async def publish_issue(self, request):
            self.requests.append(request)
            return IssuePublicationResult(
                issue_number=18,
                issue_url="https://github.com/yyqqCoding/MDToWord/issues/18",
            )

    async def scenario():
        feedback = FeedbackRecord(
            id=uuid4(),
            feedback_type=FeedbackType.FEATURE,
            description="希望增加 PDF 导出",
        )
        feedback_repository = FakeFeedbackRepository([feedback])
        claimed = await feedback_repository.claim_next(
            now=feedback.created_at,
            lease_seconds=60,
            max_attempts=3,
        )
        assert claimed is not None
        issue_publisher = FakeIssuePublisher()
        run_repository = FakeAgentRunRepository()
        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=FakeModelProvider(
                [
                    GateClassification(
                        intent=GateIntent.FEATURE_REQUEST,
                        area=GateArea.CROSS_COMPONENT,
                        category=GateCategory.FEATURE_REQUEST,
                        relevance=0.98,
                        sufficient_information=True,
                        injection_suspected=False,
                        requires_extension_change=False,
                        reason="PDF export feature",
                        issue_title="增加 PDF 导出",
                        issue_summary="用户希望增加 PDF 导出能力。",
                    )
                ]
            ),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=InMemorySaver(),
            dry_run=False,
            publishing=PublishingDependencies(
                trace_url_template="https://trace.example/{trace_id}",
                issue_publisher=issue_publisher,
            ),
        )

        outcome = await controller.start(claimed)
        return (
            outcome,
            issue_publisher.requests,
            await feedback_repository.get(feedback.id),
            await run_repository.get(outcome.run_id),
            sorted(path.name for path in (tmp_path / str(outcome.run_id)).iterdir()),
        )

    outcome, requests, stored_feedback, stored_run, artifacts = asyncio.run(scenario())

    assert outcome.route is GateRoute.ISSUE_REQUIRED
    assert outcome.issue_url == "https://github.com/yyqqCoding/MDToWord/issues/18"
    assert len(requests) == 1
    assert requests[0].draft.title == "增加 PDF 导出"
    assert stored_feedback is not None
    assert stored_feedback.status is FeedbackStatus.ISSUE_OPENED
    assert stored_run is not None
    assert stored_run.status is AgentRunStatus.COMPLETED
    assert stored_run.issue_url == outcome.issue_url
    assert artifacts == [
        "gate.json",
        "issue-publication.json",
        "task.redacted.json",
    ]


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


@pytest.mark.parametrize(
    ("failure", "error_code", "feedback_status", "run_status"),
    (
        (
            ModelAuthError("safe message"),
            "auth_error",
            FeedbackStatus.NEEDS_HUMAN,
            AgentRunStatus.FAILED,
        ),
        (
            SourceAccessError("safe message"),
            "source_access_denied",
            FeedbackStatus.SECURITY_REJECTED,
            AgentRunStatus.SECURITY_REJECTED,
        ),
        (
            SourceAuthenticationError("safe message"),
            "source_auth_error",
            FeedbackStatus.NEEDS_HUMAN,
            AgentRunStatus.FAILED,
        ),
        (
            BudgetExceededError("safe message"),
            "budget_exhausted",
            FeedbackStatus.FAILED,
            AgentRunStatus.BUDGET_EXHAUSTED,
        ),
        (
            RuntimeError("secret body"),
            "unexpected_error",
            FeedbackStatus.FAILED,
            AgentRunStatus.FAILED,
        ),
    ),
)
def test_non_retryable_failure_terminalizes_run_and_feedback(
    tmp_path: Path,
    failure: Exception,
    error_code: str,
    feedback_status: FeedbackStatus,
    run_status: AgentRunStatus,
):
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
            raise failure

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
        outcome = await controller.start(claimed)
        run = await run_repository.find_resumable()
        stored_feedback = await feedback_repository.get(feedback.id)
        all_runs = [
            item
            for item in run_repository._records.values()
        ]
        return outcome, run, stored_feedback, all_runs[0]

    outcome, resumable, feedback, failed_run = asyncio.run(scenario())

    assert outcome.status is run_status
    assert resumable is None
    assert feedback is not None
    assert feedback.status is feedback_status
    assert feedback.last_error_code == error_code
    assert failed_run.status is run_status
    assert failed_run.error_message == type(failure).__name__
    assert failed_run.failure is not None
    assert failed_run.failure.code == error_code
    assert failed_run.failure.phase == "gating"
    assert failed_run.failure.node == "classify_gate"


def test_failure_finalization_persists_newer_checkpoint_usage(tmp_path: Path):
    async def scenario():
        feedback = make_feedback()
        claim_token = UUID("11111111-1111-4111-8111-111111111111")
        feedback.status = FeedbackStatus.REPAIRING
        feedback.claim_token = claim_token
        feedback_repository = FakeFeedbackRepository([feedback])
        run_id = uuid4()
        run = AgentRunRecord(
            id=run_id,
            feedback_id=feedback.id,
            claim_token=claim_token,
            trace_id="a" * 32,
            status=AgentRunStatus.REPAIRING,
            graph_version="test",
            policy_version="test",
            artifact_path=f"run://{run_id}",
            task_artifact_ref=f"run://{run_id}/task.redacted.json",
            model_calls=3,
            tool_calls=8,
            total_tokens=100,
        )
        run_repository = FakeAgentRunRepository([run])
        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=FakeModelProvider([]),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=InMemorySaver(),
        )
        checkpoint_state = AgentState(
            run_id=run_id,
            feedback_id=feedback.id,
            claim_token=claim_token,
            trace_id=run.trace_id,
            status=AgentRunStatus.REPAIRING,
            model_calls=4,
            tool_calls=11,
            usage=UsageTotals(
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                estimated_cost=Decimal("0.012"),
            ),
        )

        class CheckpointGraph:
            async def aget_state(self, config):
                del config
                return SimpleNamespace(values=checkpoint_state.model_dump())

        controller.graph = CheckpointGraph()
        await controller._finalize_run_failure(run_id, ModelTimeoutError("timeout"))
        return await run_repository.get(run_id), await feedback_repository.get(feedback.id)

    failed_run, failed_feedback = asyncio.run(scenario())

    assert failed_run is not None and failed_run.status is AgentRunStatus.FAILED
    assert failed_run.model_calls == 4
    assert failed_run.tool_calls == 11
    assert failed_run.input_tokens == 120
    assert failed_run.output_tokens == 30
    assert failed_run.total_tokens == 150
    assert failed_run.estimated_cost == Decimal("0.012")
    assert failed_run.failure is not None
    assert failed_run.failure.code == "timeout"
    assert failed_run.failure.phase == "repairing"
    assert failed_feedback is not None
    assert failed_feedback.status is FeedbackStatus.FAILED
    assert failed_feedback.last_error_code == "timeout"


@pytest.mark.parametrize(
    "legacy_error_type",
    ("GraphRecursionError", "ModelCallLimitExceededError"),
)
def test_explicit_resume_reopens_legacy_agent_budget_failure(
    tmp_path: Path,
    legacy_error_type: str,
):
    async def scenario():
        feedback = make_feedback()
        claim_token = UUID("11111111-1111-4111-8111-111111111111")
        feedback.status = FeedbackStatus.FAILED
        feedback.claim_token = claim_token
        feedback.last_error_code = "unexpected_error"
        feedback.last_error_message = legacy_error_type
        feedback_repository = FakeFeedbackRepository([feedback])
        run_id = uuid4()
        failure = FailureSnapshot(
            code="unexpected_error",
            kind=FailureKind.PERMANENT,
            component="runtime",
            operation="repair_agent",
            phase="reproducing",
            node="repair_agent",
            handling=FailureHandling.STOP,
            attempt=1,
            max_attempts=1,
            safe_details={"error_type": legacy_error_type},
        )
        run = AgentRunRecord(
            id=run_id,
            feedback_id=feedback.id,
            claim_token=claim_token,
            trace_id="a" * 32,
            status=AgentRunStatus.FAILED,
            graph_version="test",
            policy_version="test",
            artifact_path=f"run://{run_id}",
            task_artifact_ref=f"run://{run_id}/task.redacted.json",
            error_code="unexpected_error",
            error_message=legacy_error_type,
            failure=failure,
        )
        run_repository = FakeAgentRunRepository([run])
        checkpoint_state = AgentState(
            run_id=run_id,
            feedback_id=feedback.id,
            claim_token=claim_token,
            trace_id=run.trace_id,
            status=AgentRunStatus.REPRODUCING,
            task_artifact_ref=run.task_artifact_ref,
        )

        class ResumeGraph:
            async def aget_state(self, config):
                del config
                return SimpleNamespace(values=checkpoint_state.model_dump())

            async def ainvoke(self, state, config):
                del state, config
                reopened_feedback = await feedback_repository.get(feedback.id)
                reopened_run = await run_repository.get(run_id)
                assert reopened_feedback is not None
                assert reopened_feedback.status is FeedbackStatus.REPRODUCING
                assert reopened_run is not None
                assert reopened_run.status is AgentRunStatus.REPRODUCING
                return checkpoint_state.model_dump()

        controller = GateController(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=FakeModelProvider([]),
            artifact_store=ArtifactStore(tmp_path),
            checkpointer=InMemorySaver(),
        )
        controller.graph = ResumeGraph()
        outcome = await controller.resume(run_id)
        return outcome, await run_repository.get(run_id), await feedback_repository.get(feedback.id)

    outcome, run, feedback = asyncio.run(scenario())

    assert outcome.status is AgentRunStatus.REPRODUCING
    assert run is not None and run.error_code is None and run.failure is None
    assert feedback is not None and feedback.last_error_code is None
