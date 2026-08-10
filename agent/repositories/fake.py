import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from agent.domain.enums import FeedbackStatus
from agent.domain.errors import (
    ClaimTokenMismatchError,
    DuplicateFeedbackError,
    FeedbackNotFoundError,
    InvalidStatusTransitionError,
)
from agent.domain.models import FeedbackRecord
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

            # 整个选取和写入都在同一把锁内，对应 SQL 的 FOR UPDATE SKIP LOCKED。
            if candidate.status is FeedbackStatus.PENDING:
                ensure_feedback_transition(candidate.status, FeedbackStatus.CLAIMED)
            candidate.status = FeedbackStatus.CLAIMED
            candidate.attempt_count += 1
            candidate.claim_token = uuid4()
            candidate.claimed_at = now
            candidate.updated_at = now
            candidate.last_error_code = None
            candidate.last_error_message = None
            return candidate.model_copy(deep=True)

    async def transition(
        self,
        feedback_id: UUID,
        *,
        claim_token: UUID,
        target: FeedbackStatus,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
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
