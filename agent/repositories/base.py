from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent.domain.enums import FeedbackStatus
from agent.domain.models import FeedbackRecord


class FeedbackRepository(Protocol):
    """Controller 依赖的持久化端口；Fake 与 Supabase 必须保持相同语义。"""

    async def get(self, feedback_id: UUID) -> FeedbackRecord | None: ...

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None: ...

    async def transition(
        self,
        feedback_id: UUID,
        *,
        claim_token: UUID,
        target: FeedbackStatus,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> FeedbackRecord: ...

    async def find_open_by_fingerprint(
        self,
        content_fingerprint: str,
        *,
        excluding_feedback_id: UUID | None = None,
    ) -> FeedbackRecord | None: ...
