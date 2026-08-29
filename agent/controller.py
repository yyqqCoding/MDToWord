from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import logging
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from agent.domain.enums import AgentRunStatus, FeedbackStatus, GateRoute
from agent.domain.errors import AgentRunNotFoundError
from agent.domain.failures import (
    FailureEvent,
    FailureHandling,
    FailureKind,
    FailureRecorder,
    FailureSnapshot,
    LocatedFailure,
    failure_cause_from_exception,
)
from agent.domain.models import AgentRunRecord, FeedbackRecord, TaskArtifact
from agent.gate import GATE_PROMPT_VERSION
from agent.graph import (
    GRAPH_VERSION,
    POLICY_VERSION,
    RepairDependencies,
    ReproductionDependencies,
    PublishingDependencies,
    build_gate_graph,
)
from agent.providers.base import ModelProvider
from agent.providers.observed import ObservedModelProvider
from agent.reproduction import (
    REPRODUCTION_PLAN_PROMPT_VERSION,
    TEST_GENERATION_PROMPT_VERSION,
)
from agent.repair import FIX_GENERATION_PROMPT_VERSION
from agent.repositories.base import AgentRunRepository, FeedbackRepository
from agent.state import AgentState
from agent.telemetry.base import NoopTelemetry, RunTrace, Telemetry
from agent.workspace.artifacts import ArtifactStore


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateRunOutcome:
    run_id: UUID
    feedback_id: UUID
    route: GateRoute | None
    completed: bool
    status: AgentRunStatus
    error_code: str | None = None
    pr_url: str | None = None
    issue_url: str | None = None


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
        dry_run: bool = True,
        reproduction: ReproductionDependencies | None = None,
        repair: RepairDependencies | None = None,
        publishing: PublishingDependencies | None = None,
        failure_recorder: FailureRecorder | None = None,
    ) -> None:
        self._feedback_repository = feedback_repository
        self._run_repository = run_repository
        self._provider = provider
        self._telemetry = telemetry or NoopTelemetry()
        self._failure_recorder = failure_recorder or FailureRecorder(self._telemetry)
        self._environment = environment
        self._dry_run = dry_run
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
                failure_recorder=self._failure_recorder,
            )
        self._reproduction_enabled = observed_reproduction is not None
        observed_repair = None
        if repair is not None:
            observed_repair = replace(
                repair,
                fix_provider=ObservedModelProvider(
                    repair.fix_provider,
                    self._telemetry,
                    operation="generate_fix",
                    prompt_version=FIX_GENERATION_PROMPT_VERSION,
                ),
                telemetry=self._telemetry,
            )
        self._repair_enabled = observed_repair is not None
        observed_publishing = (
            replace(publishing, telemetry=self._telemetry)
            if publishing is not None
            else None
        )
        self._publishing_enabled = observed_publishing is not None
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
            repair=observed_repair,
            publishing=observed_publishing,
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
                        **(
                            {"generate_fix": FIX_GENERATION_PROMPT_VERSION}
                            if self._repair_enabled
                            else {}
                        ),
                    }
                    if self._reproduction_enabled
                    else {}
                ),
            },
            policy_version=POLICY_VERSION,
            dry_run=self._dry_run,
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
            dry_run=self._dry_run,
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
        if (
            self._publishing_enabled
            and run.status is AgentRunStatus.FAILED
            and _is_publication_error(run.error_code)
        ):
            # 只重开 GitHub 发布节点；checkpoint 已固定验证结果，不重跑模型或 Sandbox。
            await self._feedback_repository.retry_publication(
                run.feedback_id,
                claim_token=run.claim_token,
            )
            run = await self._run_repository.retry_publication(run.id)
        if run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.SECURITY_REJECTED,
            AgentRunStatus.FAILED,
            AgentRunStatus.BUDGET_EXHAUSTED,
            AgentRunStatus.STALE_BASE,
        }:
            return _outcome_from_run(run)
        if run.status is AgentRunStatus.REPAIRING and not self._repair_enabled:
            return _outcome_from_run(run)
        if run.status is AgentRunStatus.PUBLISHING and not self._publishing_enabled:
            return _outcome_from_run(run)
        if (
            run.status is AgentRunStatus.PUBLISHING_ISSUE
            and not self._publishing_enabled
        ):
            return _outcome_from_run(run)

        feedback = await self._feedback_repository.get(run.feedback_id)
        feedback_hash = feedback.content_fingerprint if feedback is not None else "unknown"
        config = _thread_config(run_id)
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            if (
                self._repair_enabled
                and run.status is AgentRunStatus.REPAIRING
                and not snapshot.next
            ):
                # 阶段 D 的旧 Graph 已在 finish_reproduction 后到达 END；升级到阶段 E
                # 时从该确定性节点追加新 checkpoint，使同一 run 继续而不重跑 Gate。
                await self.graph.aupdate_state(
                    config,
                    {},
                    as_node="finish_reproduction",
                )
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
            except Exception as exc:
                failed_run = await self._finalize_run_failure(run_id, exc)
                observation.finish(route=None, status="failed")
                return _outcome_from_run(failed_run)
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
        error: Exception,
    ) -> AgentRunRecord:
        """终结确定性或已耗尽重试的失败，避免 Scheduler 无限恢复同一节点。"""

        run = await self._run_repository.get(run_id)
        if run is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        usage = {
            "model_calls": run.model_calls,
            "tool_calls": run.tool_calls,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "total_tokens": run.total_tokens,
            "estimated_cost": run.estimated_cost,
        }
        # Graph 节点可能已写入 checkpoint、但尚未走到数据库汇总节点；失败终结时
        # 取两者单调最大值，避免丢失已完成的模型、工具与 Sandbox 计量。
        checkpoint_node: str | None = None
        try:
            snapshot = await self.graph.aget_state(_thread_config(run_id))
            pending_nodes = getattr(snapshot, "next", ())
            checkpoint_node = str(pending_nodes[0]) if pending_nodes else None
            if snapshot.values:
                checkpoint_state = AgentState.model_validate(snapshot.values)
                if checkpoint_state.run_id == run_id:
                    checkpoint_usage = {
                        "model_calls": checkpoint_state.model_calls,
                        "tool_calls": checkpoint_state.tool_calls,
                        "input_tokens": checkpoint_state.usage.input_tokens,
                        "output_tokens": checkpoint_state.usage.output_tokens,
                        "total_tokens": checkpoint_state.usage.total_tokens,
                        "estimated_cost": checkpoint_state.usage.estimated_cost,
                    }
                    usage = {
                        key: max(usage[key], checkpoint_usage[key])
                        for key in usage
                    }
        except Exception as checkpoint_error:
            # Checkpoint诊断失败不能遮蔽原始失败；只记录异常类型，继续用run摘要终结。
            _LOGGER.warning(
                "failure checkpoint lookup failed: %s",
                type(checkpoint_error).__name__,
            )
        phase = getattr(error, "phase", None) or run.status.value
        node = getattr(error, "node", None) or checkpoint_node or "controller_run"
        operation = getattr(error, "operation", None) or node
        located = LocatedFailure(
            cause=failure_cause_from_exception(error, operation=operation),
            phase=phase,
            node=node,
        )
        attempt = min(max(1, int(getattr(error, "attempt", 1))), 3)
        max_attempts = min(
            max(attempt, int(getattr(error, "max_attempts", attempt))),
            3,
        )
        failure = FailureSnapshot.final(
            located,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        self._failure_recorder.record(
            FailureEvent(
                failure=located,
                attempt=attempt,
                max_attempts=max_attempts,
                handling=FailureHandling.STOP,
            )
        )
        feedback = await self._feedback_repository.get(run.feedback_id)
        error_message = type(error).__name__
        if failure.kind is FailureKind.SECURITY:
            run_status = AgentRunStatus.SECURITY_REJECTED
            feedback_status = FeedbackStatus.SECURITY_REJECTED
        elif failure.code == "budget_exhausted":
            run_status = AgentRunStatus.BUDGET_EXHAUSTED
            feedback_status = FeedbackStatus.FAILED
        else:
            run_status = AgentRunStatus.FAILED
            feedback_status = (
                FeedbackStatus.NEEDS_HUMAN
                if failure.code in {"auth_error", "sandbox_auth_error"}
                else FeedbackStatus.FAILED
            )
        if (
            feedback is not None
            and feedback.claim_token == run.claim_token
            and feedback.status
            in {
                FeedbackStatus.CLAIMED,
                FeedbackStatus.GATING,
                FeedbackStatus.REPRODUCING,
                FeedbackStatus.REPAIRING,
                FeedbackStatus.VALIDATING,
                FeedbackStatus.PUBLISHING,
                FeedbackStatus.PUBLISHING_ISSUE,
            }
        ):
            await self._feedback_repository.transition(
                feedback.id,
                claim_token=run.claim_token,
                target=feedback_status,
                error_code=failure.code,
                error_message=error_message,
            )
        return await self._run_repository.fail(
            run_id,
            error_code=failure.code,
            error_message=error_message,
            failure=failure,
            terminal_status=run_status,
            **usage,
        )


def _thread_config(run_id: UUID) -> dict[str, dict[str, str]]:
    # thread_id 固定等于 agent_run_id，数据库摘要与 checkpoint 可直接关联。
    return {"configurable": {"thread_id": str(run_id)}}


def _trace_id_for_run(run_id: UUID) -> str:
    # Langfuse 使用 W3C 32 位十六进制 Trace ID；由 run_id 可重复推导。
    return sha256(f"mdtoword-agent:{run_id}".encode()).hexdigest()[:32]


def _session_id_for_feedback(feedback_hash: str) -> str:
    return sha256(f"feedback:{feedback_hash}".encode()).hexdigest()


def _is_publication_error(error_code: str | None) -> bool:
    return error_code in {
        "publication_failed",
        "publication_auth_error",
        "publication_conflict",
        "issue_publication_failed",
    }


def _outcome_from_state(state: AgentState) -> GateRunOutcome:
    return GateRunOutcome(
        run_id=state.run_id,
        feedback_id=state.feedback_id,
        route=GateRoute(state.route) if state.route else None,
        completed=state.status is AgentRunStatus.COMPLETED,
        status=state.status,
        error_code=state.last_error_code,
        pr_url=state.pr_url,
        issue_url=state.issue_url,
    )


def _outcome_from_run(run: AgentRunRecord) -> GateRunOutcome:
    return GateRunOutcome(
        run_id=run.id,
        feedback_id=run.feedback_id,
        route=run.route,
        completed=run.status is AgentRunStatus.COMPLETED,
        status=run.status,
        error_code=run.error_code,
        pr_url=run.pr_url,
        issue_url=run.issue_url,
    )
