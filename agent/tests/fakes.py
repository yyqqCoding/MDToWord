"""测试替身:内存版 FeedbackRepository,领取语义与阶段 01 RPC 对齐。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from agent.domain import Feedback

CLAIM_STALE_AFTER = timedelta(hours=2)
CLAIMABLE_STATUSES = {"pending", "approved", "failed"}


class FakeFeedbackRepository:
    def __init__(self) -> None:
        self.feedback: dict[UUID, dict[str, Any]] = {}
        self.runs: dict[UUID, dict[str, Any]] = {}
        self.now: datetime = datetime.now(timezone.utc)

    def add_feedback(self, **fields: Any) -> UUID:
        feedback_id = fields.pop("id", uuid4())
        row = {
            "id": feedback_id, "feedback_type": "bug",
            "markdown_content": "", "description": "", "contact": None,
            "status": "pending", "attempt_count": 0,
            "claimed_at": None, "claim_token": None,
            "content_fingerprint": None, "pr_url": None,
            "resolution_type": None,
            **fields,
        }
        self.feedback[feedback_id] = row
        return feedback_id

    # -- FeedbackRepository 接口 ----------------------------------------------

    def get_feedback(self, feedback_id: UUID) -> Feedback | None:
        row = self.feedback.get(feedback_id)
        return Feedback.model_validate(row) if row else None

    def claim_feedback(self, feedback_id: UUID, claim_token: UUID,
                       max_attempts: int = 3) -> Feedback | None:
        row = self.feedback.get(feedback_id)
        if row is None or row["attempt_count"] >= max_attempts:
            return None
        stale = (
            row["status"] == "claimed"
            and row["claimed_at"] is not None
            and row["claimed_at"] < self.now - CLAIM_STALE_AFTER
        )
        if row["status"] not in CLAIMABLE_STATUSES and not stale:
            return None
        row.update(status="claimed", claim_token=claim_token,
                   claimed_at=self.now, attempt_count=row["attempt_count"] + 1)
        return Feedback.model_validate(row)

    def create_run(self, feedback_id: UUID, *, provider: str, model: str,
                   status: str = "created",
                   workflow_run_id: str | None = None) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = {
            "id": run_id, "feedback_id": feedback_id, "provider": provider,
            "model": model, "status": status, "workflow_run_id": workflow_run_id,
        }
        return run_id

    def update_feedback(self, feedback_id: UUID, **fields: Any) -> None:
        self.feedback[feedback_id].update(fields)

    def update_run(self, run_id: UUID, **fields: Any) -> None:
        self.runs[run_id].update(fields)

    def find_open_resolution(self, fingerprint: str) -> Feedback | None:
        for row in self.feedback.values():
            if (row.get("content_fingerprint") == fingerprint
                    and row["status"] in ("pr_opened", "resolved",
                                          "validated_but_unpublished")):
                return Feedback.model_validate(row)
        return None
