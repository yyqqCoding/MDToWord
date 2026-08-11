from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from agent.domain.enums import AgentRunStatus, FeedbackStatus, GateRoute
from agent.domain.errors import (
    InvalidModelResponseError,
    ModelProviderError,
    SourceAccessError,
)
from agent.domain.models import AgentRunRecord, FeedbackRecord, TaskArtifact
from agent.gate import GATE_PROMPT_VERSION
from agent.graph import (
    GRAPH_VERSION,
    POLICY_VERSION,
    ReproductionDependencies,
    build_gate_graph,
)
from agent.providers.base import ModelProvider
from agent.providers.observed import ObservedModelProvider
from agent.reproduction import (
    REPRODUCTION_PLAN_PROMPT_VERSION,
    TEST_GENERATION_PROMPT_VERSION,
)
from agent.repositories.base import AgentRunRepository, FeedbackRepository
from agent.state import AgentState
from agent.telemetry.base import NoopTelemetry, RunTrace, Telemetry
from agent.workspace.artifacts import ArtifactStore


@dataclass(frozen=True)
class GateRunOutcome:
    run_id: UUID
    feedback_id: UUID
    route: GateRoute | None
    completed: bool


class GateController:
    """创建或恢复 Gate 运行；用户原文在进入 Graph 前先写入受控 Artifact。"""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        run_repository: AgentRunRepository,
        provider: ModelProvider,
        artifact_store: ArtifactStore,
        checkpointer: BaseCheckpointSaver,
        min_confidence: float = 0.80,
        extension_version: str = "unknown",
        interrupt_after: Sequence[str] | None = None,
        telemetry: Telemetry | None = None,
        environment: str = "development",
        reproduction: ReproductionDependencies | None = None,
    ) -> None:
        self._feedback_repository = feedback_repository
        self._run_repository = run_repository
        self._provider = provider
        self._telemetry = telemetry or NoopTelemetry()
        self._environment = environment
        self._artifact_store = artifact_store
        self._checkpointer = checkpointer
        self._min_confidence = min_confidence
        self._extension_version = extension_version
        observed_reproduction = None
        if reproduction is not None:
            observed_reproduction = replace(
                reproduction,
                plan_provider=ObservedModelProvider(
                    reproduction.plan_provider,
                    self._telemetry,
                    operation="plan_reproduction",
                    prompt_version=REPRODUCTION_PLAN_PROMPT_VERSION,
                ),
                test_provider=ObservedModelProvider(
                    reproduction.test_provider,
                    self._telemetry,
                    operation="generate_test",
                    prompt_version=TEST_GENERATION_PROMPT_VERSION,
                ),
                telemetry=self._telemetry,
            )
        self._reproduction_enabled = observed_reproduction is not None
        self.graph = build_gate_graph(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            provider=ObservedModelProvider(
                provider,
                self._telemetry,
                operation="gate",
                prompt_version=GATE_PROMPT_VERSION,
            ),
            artifact_store=artifact_store,
            checkpointer=checkpointer,
            min_confidence=min_confidence,
            reproduction=observed_reproduction,
            interrupt_after=interrupt_after,
        )

    async def start(self, feedback: FeedbackRecord) -> GateRunOutcome:
        if feedback.status is not FeedbackStatus.CLAIMED or feedback.claim_token is None:
            raise ValueError("GateController requires a claimed feedback record")

        run_id = uuid4()
        trace_id = _trace_id_for_run(run_id)
        task_ref = self._artifact_store.write_task_ref(
            run_id,
            TaskArtifact.from_feedback(feedback),
        )
        run = AgentRunRecord(
            id=run_id,
            feedback_id=feedback.id,
            claim_token=feedback.claim_token,
            trace_id=trace_id,
            langfuse_trace_id=trace_id,
            status=AgentRunStatus.CREATED,
            extension_version=self._extension_version,
            provider=getattr(self._provider, "provider", "unknown"),
            model=getattr(self._provider, "model", "unknown"),
            graph_version=GRAPH_VERSION,
            prompt_versions={
                "gate": GATE_PROMPT_VERSION,
                **(
                    {
                        "plan_reproduction": REPRODUCTION_PLAN_PROMPT_VERSION,
                        "generate_test": TEST_GENERATION_PROMPT_VERSION,
                    }
                    if self._reproduction_enabled
                    else {}
                ),
            },
            policy_version=POLICY_VERSION,
            artifact_path=self._artifact_store.run_ref(run_id),
            task_artifact_ref=task_ref,
        )
        await self._run_repository.create(run)
        state = AgentState(
            run_id=run_id,
            feedback_id=feedback.id,
            claim_token=feedback.claim_token,
            trace_id=trace_id,
            status=AgentRunStatus.CREATED,
            extension_version=self._extension_version,
            task_artifact_ref=task_ref,
        )
        return await self._invoke(
            state,
            run_id,
            feedback_hash=feedback.content_fingerprint,
            provider=run.provider or "unknown",
            model=run.model or "unknown",
        )

    async def resume(self, run_id: UUID) -> GateRunOutcome:
        run = await self._run_repository.get(run_id)
        if run is None:
            raise ValueError(f"agent run {run_id} does not exist")
        if run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.REPAIRING,
            AgentRunStatus.SECURITY_REJECTED,
            AgentRunStatus.FAILED,
        }:
            return _outcome_from_run(run)

        feedback = await self._feedback_repository.get(run.feedback_id)
        feedback_hash = feedback.content_fingerprint if feedback is not None else "unknown"
        config = _thread_config(run_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            return await self._invoke_resumed(
                None,
                run,
                feedback_hash=feedback_hash,
            )
        else:
            state = AgentState(
                run_id=run.id,
                feedback_id=run.feedback_id,
                claim_token=run.claim_token,
                trace_id=run.trace_id,
                status=run.status,
                extension_version=run.extension_version,
                task_artifact_ref=run.task_artifact_ref,
            )
            return await self._invoke_resumed(
                state,
                run,
                feedback_hash=feedback_hash,
            )

    async def _invoke(
        self,
        state: AgentState,
        run_id: UUID,
        *,
        feedback_hash: str,
        provider: str,
        model: str,
    ) -> GateRunOutcome:
        return await self._invoke_with_trace(
            state,
            run_id=run_id,
            trace_id=state.trace_id,
            feedback_hash=feedback_hash,
            provider=provider,
            model=model,
        )

    async def _invoke_resumed(
        self,
        state: AgentState | None,
        run: AgentRunRecord,
        *,
        feedback_hash: str,
    ) -> GateRunOutcome:
        return await self._invoke_with_trace(
            state,
            run_id=run.id,
            trace_id=run.trace_id,
            feedback_hash=feedback_hash,
            provider=run.provider or "unknown",
            model=run.model or "unknown",
        )

    async def _invoke_with_trace(
        self,
        state: AgentState | None,
        *,
        run_id: UUID,
        trace_id: str,
        feedback_hash: str,
        provider: str,
        model: str,
    ) -> GateRunOutcome:
        trace = RunTrace(
            trace_id=trace_id,
            run_id=str(run_id),
            session_id=_session_id_for_feedback(feedback_hash),
            feedback_hash=feedback_hash,
            provider=provider,
            model=model,
            graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION,
            environment=self._environment,
        )
        with self._telemetry.start_run(trace) as observation:
            try:
                output = await self.graph.ainvoke(state, _thread_config(run_id))
            except (ModelProviderError, InvalidModelResponseError, SourceAccessError) as exc:
                await self._finalize_run_failure(run_id, exc)
                observation.finish(route=None, status="failed")
                raise
            final_state = AgentState.model_validate(output)
            outcome = _outcome_from_state(final_state)
            observation.finish(
                route=outcome.route.value if outcome.route else None,
                status=final_state.status.value,
            )
            return outcome

    async def _finalize_run_failure(
        self,
        run_id: UUID,
        error: ModelProviderError | InvalidModelResponseError | SourceAccessError,
    ) -> None:
        """终结确定性或已耗尽重试的失败，避免 Scheduler 无限恢复同一节点。"""

        run = await self._run_repository.get(run_id)
        if run is None:
            return
        feedback = await self._feedback_repository.get(run.feedback_id)
        error_message = type(error).__name__
        if (
            feedback is not None
            and feedback.claim_token == run.claim_token
            and feedback.status
            in {
                FeedbackStatus.CLAIMED,
                FeedbackStatus.GATING,
                FeedbackStatus.REPRODUCING,
            }
        ):
            await self._feedback_repository.transition(
                feedback.id,
                claim_token=run.claim_token,
                target=FeedbackStatus.FAILED,
                error_code=error.error_code,
                error_message=error_message,
            )
        await self._run_repository.fail(
            run_id,
            error_code=error.error_code,
            error_message=error_message,
        )


def _thread_config(run_id: UUID) -> dict[str, dict[str, str]]:
    # thread_id 固定等于 agent_run_id，数据库摘要与 checkpoint 可直接关联。
    return {"configurable": {"thread_id": str(run_id)}}


def _trace_id_for_run(run_id: UUID) -> str:
    # Langfuse 使用 W3C 32 位十六进制 Trace ID；由 run_id 可重复推导。
    return sha256(f"mdtoword-agent:{run_id}".encode()).hexdigest()[:32]


def _session_id_for_feedback(feedback_hash: str) -> str:
    return sha256(f"feedback:{feedback_hash}".encode()).hexdigest()


def _outcome_from_state(state: AgentState) -> GateRunOutcome:
    return GateRunOutcome(
        run_id=state.run_id,
        feedback_id=state.feedback_id,
        route=GateRoute(state.route) if state.route else None,
        completed=state.status is AgentRunStatus.COMPLETED,
    )


def _outcome_from_run(run: AgentRunRecord) -> GateRunOutcome:
    return GateRunOutcome(
        run_id=run.id,
        feedback_id=run.feedback_id,
        route=run.route,
        completed=run.status is AgentRunStatus.COMPLETED,
    )
