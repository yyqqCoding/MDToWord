import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    GateCategory,
    GateRoute,
    RiskLevel,
)
from agent.domain.errors import (
    AgentRunNotFoundError,
    ClaimTokenMismatchError,
    DuplicateAgentRunError,
    DuplicateFeedbackError,
    FeedbackNotFoundError,
    InvalidStatusTransitionError,
)
from agent.domain.gate import GateResult
from agent.domain.models import AgentRunRecord, FeedbackRecord
from agent.domain.reproduction import ReproductionReport
from agent.domain.repair import RepairDisposition, RepairReport, ValidationResult
from agent.domain.transitions import ensure_agent_run_transition
from agent.domain.transitions import ensure_feedback_transition


_OPEN_STATUSES = frozenset(
    {
        FeedbackStatus.PENDING,
        FeedbackStatus.CLAIMED,
        FeedbackStatus.GATING,
        FeedbackStatus.REPRODUCING,
        FeedbackStatus.REPAIRING,
        FeedbackStatus.VALIDATING,
        FeedbackStatus.VALIDATED,
        FeedbackStatus.PUBLISHING,
        FeedbackStatus.PR_OPENED,
    }
)


class FakeFeedbackRepository:
    """进程内测试仓库，复刻 PostgreSQL 的原子 claim 与条件更新语义。"""

    def __init__(self, feedback: list[FeedbackRecord] | None = None) -> None:
        self._records = {item.id: item.model_copy(deep=True) for item in feedback or []}
        self._lock = asyncio.Lock()

    async def add(self, feedback: FeedbackRecord) -> None:
        async with self._lock:
            if feedback.id in self._records:
                raise DuplicateFeedbackError(f"feedback {feedback.id} already exists")
            self._records[feedback.id] = feedback.model_copy(deep=True)

    async def get(self, feedback_id: UUID) -> FeedbackRecord | None:
        async with self._lock:
            record = self._records.get(feedback_id)
            return record.model_copy(deep=True) if record is not None else None

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None:
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("lease_seconds and max_attempts must be positive")

        async with self._lock:
            lease_cutoff = now - timedelta(seconds=lease_seconds)
            # 先终结耗尽尝试次数的记录，避免它们永久停留在 pending/claimed。
            for record in self._ordered_records():
                if (
                    (
                        record.status is FeedbackStatus.PENDING
                        or (
                            record.status is FeedbackStatus.CLAIMED
                            and record.claimed_at is not None
                            and record.claimed_at <= lease_cutoff
                        )
                    )
                    and record.attempt_count >= max_attempts
                ):
                    record.status = FeedbackStatus.NEEDS_HUMAN
                    record.claim_token = None
                    record.claimed_at = None
                    record.last_error_code = "claim_attempts_exhausted"
                    record.last_error_message = "claim lease expired after maximum attempts"
                    record.updated_at = now

            candidate = next(
                (
                    record
                    for record in self._ordered_records()
                    if self._is_claimable(record, lease_cutoff, max_attempts)
                ),
                None,
            )
            if candidate is None:
                return None
            return self._claim_record(candidate, now)

    async def claim_by_id(
        self,
        feedback_id: UUID,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None:
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("lease_seconds and max_attempts must be positive")

        async with self._lock:
            record = self._records.get(feedback_id)
            if record is None:
                raise FeedbackNotFoundError(f"feedback {feedback_id} does not exist")
            lease_cutoff = now - timedelta(seconds=lease_seconds)
            if record.attempt_count >= max_attempts and (
                record.status is FeedbackStatus.PENDING
                or (
                    record.status is FeedbackStatus.CLAIMED
                    and record.claimed_at is not None
                    and record.claimed_at <= lease_cutoff
                )
            ):
                record.status = FeedbackStatus.NEEDS_HUMAN
                record.claim_token = None
                record.claimed_at = None
                record.last_error_code = "claim_attempts_exhausted"
                record.updated_at = now
                return None
            if not self._is_claimable(record, lease_cutoff, max_attempts):
                return None
            return self._claim_record(record, now)

    async def transition(
        self,
        feedback_id: UUID,
        *,
        claim_token: UUID,
        target: FeedbackStatus,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        category: GateCategory | None = None,
        risk: RiskLevel | None = None,
    ) -> FeedbackRecord:
        async with self._lock:
            record = self._records.get(feedback_id)
            if record is None:
                raise FeedbackNotFoundError(f"feedback {feedback_id} does not exist")
            if record.claim_token != claim_token:
                raise ClaimTokenMismatchError(
                    f"claim token does not own feedback {feedback_id}"
                )
            ensure_feedback_transition(record.status, target)
            if record.status is FeedbackStatus.STALE_BASE and target is FeedbackStatus.PENDING:
                # stale base 独立于普通 claim 重试，产品契约只允许重新排队一次。
                if record.stale_requeue_count >= 1:
                    raise InvalidStatusTransitionError(
                        "stale base feedback can only be requeued once"
                    )
                record.stale_requeue_count += 1
                record.claim_token = None
                record.claimed_at = None
            record.status = target
            record.updated_at = now or datetime.now(UTC)
            record.last_error_code = error_code
            record.last_error_message = error_message
            if category is not None:
                record.category = category.value
            if risk is not None:
                record.risk = risk
            return record.model_copy(deep=True)

    async def find_open_by_fingerprint(
        self,
        content_fingerprint: str,
        *,
        excluding_feedback_id: UUID | None = None,
    ) -> FeedbackRecord | None:
        async with self._lock:
            for record in self._ordered_records():
                if (
                    record.id != excluding_feedback_id
                    and record.content_fingerprint == content_fingerprint
                    and record.status in _OPEN_STATUSES
                ):
                    return record.model_copy(deep=True)
            return None

    def _ordered_records(self) -> list[FeedbackRecord]:
        return sorted(self._records.values(), key=lambda item: (item.created_at, str(item.id)))

    @staticmethod
    def _claim_record(record: FeedbackRecord, now: datetime) -> FeedbackRecord:
        # 整个选取和写入都在同一把锁内，对应 SQL 的 FOR UPDATE SKIP LOCKED。
        if record.status is FeedbackStatus.PENDING:
            ensure_feedback_transition(record.status, FeedbackStatus.CLAIMED)
        record.status = FeedbackStatus.CLAIMED
        record.attempt_count += 1
        record.claim_token = uuid4()
        record.claimed_at = now
        record.updated_at = now
        record.last_error_code = None
        record.last_error_message = None
        return record.model_copy(deep=True)

    @staticmethod
    def _is_claimable(
        record: FeedbackRecord,
        lease_cutoff: datetime,
        max_attempts: int,
    ) -> bool:
        if record.attempt_count >= max_attempts:
            return False
        if record.status is FeedbackStatus.PENDING:
            return True
        return (
            record.status is FeedbackStatus.CLAIMED
            and record.claimed_at is not None
            and record.claimed_at <= lease_cutoff
        )


class FakeAgentRunRepository:
    """进程内运行仓库，用于验证恢复顺序和幂等终态写入。"""

    def __init__(self, runs: list[AgentRunRecord] | None = None) -> None:
        self._records = {item.id: item.model_copy(deep=True) for item in runs or []}
        self._lock = asyncio.Lock()

    async def create(self, run: AgentRunRecord) -> AgentRunRecord:
        async with self._lock:
            if run.id in self._records:
                raise DuplicateAgentRunError(f"agent run {run.id} already exists")
            self._records[run.id] = run.model_copy(deep=True)
            return run.model_copy(deep=True)

    async def get(self, run_id: UUID) -> AgentRunRecord | None:
        async with self._lock:
            run = self._records.get(run_id)
            return run.model_copy(deep=True) if run is not None else None

    async def find_resumable(self) -> AgentRunRecord | None:
        async with self._lock:
            resumable = sorted(
                (
                    run
                    for run in self._records.values()
                    if run.status
                    in {
                        AgentRunStatus.CREATED,
                        AgentRunStatus.GATING,
                        AgentRunStatus.PREPARING_SOURCE,
                        AgentRunStatus.REPRODUCING,
                        AgentRunStatus.REPAIRING,
                        AgentRunStatus.VALIDATING,
                    }
                ),
                key=lambda run: (run.started_at, str(run.id)),
            )
            return resumable[0].model_copy(deep=True) if resumable else None

    async def mark_gating(self, run_id: UUID) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.CREATED:
                run.status = AgentRunStatus.GATING
            elif run.status is not AgentRunStatus.GATING:
                raise InvalidStatusTransitionError(
                    f"agent run {run_id} cannot enter gating from {run.status.value}"
                )
            return run.model_copy(deep=True)

    async def complete_gate(
        self,
        run_id: UUID,
        result: GateResult,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost: Decimal = Decimal("0"),
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.COMPLETED:
                return run.model_copy(deep=True)
            if run.status is not AgentRunStatus.GATING:
                raise InvalidStatusTransitionError(
                    f"agent run {run_id} cannot complete from {run.status.value}"
                )
            run.status = AgentRunStatus.COMPLETED
            run.route = result.route
            run.category = result.category
            run.classification = result
            run.model_calls = result.model_calls
            run.tool_calls = result.tool_calls
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.total_tokens = total_tokens
            run.estimated_cost = estimated_cost
            run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    async def mark_preparing_source(
        self,
        run_id: UUID,
        result: GateResult,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost: Decimal = Decimal("0"),
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.PREPARING_SOURCE:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, AgentRunStatus.PREPARING_SOURCE)
            run.status = AgentRunStatus.PREPARING_SOURCE
            run.route = result.route
            run.category = result.category
            run.classification = result
            run.model_calls = result.model_calls
            run.tool_calls = result.tool_calls
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.total_tokens = total_tokens
            run.estimated_cost = estimated_cost
            return run.model_copy(deep=True)

    async def mark_reproducing(
        self,
        run_id: UUID,
        *,
        base_sha: str,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.REPRODUCING:
                if run.base_sha != base_sha:
                    raise InvalidStatusTransitionError("reproducing run has a different base")
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, AgentRunStatus.REPRODUCING)
            run.status = AgentRunStatus.REPRODUCING
            run.base_sha = base_sha
            return run.model_copy(deep=True)

    async def complete_reproduction(
        self,
        run_id: UUID,
        report: ReproductionReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
        reproduction_confirmed: bool = False,
        security_rejected: bool = False,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            target = (
                AgentRunStatus.SECURITY_REJECTED
                if security_rejected
                else (
                    AgentRunStatus.REPAIRING
                    if reproduction_confirmed
                    else AgentRunStatus.COMPLETED
                )
            )
            if run.status is target:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, target)
            run.status = target
            run.reproduction = report.model_dump(mode="json")
            run.model_calls = model_calls
            run.tool_calls = tool_calls
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.total_tokens = total_tokens
            run.estimated_cost = estimated_cost
            if target is not AgentRunStatus.REPAIRING:
                run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    async def mark_validating(
        self,
        run_id: UUID,
        report: RepairReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.VALIDATING:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, AgentRunStatus.VALIDATING)
            run.status = AgentRunStatus.VALIDATING
            run.repair = report.model_dump(mode="json")
            _set_usage(
                run,
                model_calls=model_calls,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
            )
            return run.model_copy(deep=True)

    async def complete_repair_failure(
        self,
        run_id: UUID,
        report: RepairReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
        security_rejected: bool = False,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            target = (
                AgentRunStatus.SECURITY_REJECTED
                if security_rejected
                else AgentRunStatus.COMPLETED
            )
            if run.status is target:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, target)
            run.status = target
            if report.disposition is RepairDisposition.NEEDS_HUMAN:
                run.route = GateRoute.NEEDS_HUMAN
            run.repair = report.model_dump(mode="json")
            run.error_code = report.failure_code
            run.error_message = report.failure_summary
            _set_usage(
                run,
                model_calls=model_calls,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
            )
            run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    async def complete_validation(
        self,
        run_id: UUID,
        result: ValidationResult,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.COMPLETED:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, AgentRunStatus.COMPLETED)
            run.status = AgentRunStatus.COMPLETED
            run.validation = result.model_dump(mode="json")
            run.validated_patch_sha256 = (
                result.validated_patch_sha256 if result.passed else None
            )
            run.error_code = result.failure_code
            run.error_message = result.failure_summary
            _set_usage(
                run,
                model_calls=model_calls,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
            )
            run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    async def exhaust_budget(
        self,
        run_id: UUID,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            if run.status is AgentRunStatus.BUDGET_EXHAUSTED:
                return run.model_copy(deep=True)
            ensure_agent_run_transition(run.status, AgentRunStatus.BUDGET_EXHAUSTED)
            run.status = AgentRunStatus.BUDGET_EXHAUSTED
            run.error_code = "budget_exhausted"
            run.error_message = "run budget was exhausted"
            _set_usage(
                run,
                model_calls=model_calls,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
            )
            run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    async def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord:
        async with self._lock:
            run = self._require(run_id)
            run.status = AgentRunStatus.FAILED
            run.error_code = error_code
            run.error_message = error_message
            _set_usage(
                run,
                model_calls=model_calls,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
            )
            run.finished_at = datetime.now(UTC)
            return run.model_copy(deep=True)

    def _require(self, run_id: UUID) -> AgentRunRecord:
        run = self._records.get(run_id)
        if run is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        return run


def _set_usage(
    run: AgentRunRecord,
    *,
    model_calls: int,
    tool_calls: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    estimated_cost: Decimal,
) -> None:
    run.model_calls = model_calls
    run.tool_calls = tool_calls
    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.total_tokens = total_tokens
    run.estimated_cost = estimated_cost
