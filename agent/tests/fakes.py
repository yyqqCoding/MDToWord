"""测试替身:内存版 FeedbackRepository(领取语义与阶段 01 RPC 对齐)
与 FakeModelProvider(预制结构化响应,供集成测试与阶段 10 演练)。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from agent.domain import Feedback
from agent.providers.base import ModelUsage

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


class FakeModelProvider:
    """按顺序返回预制响应的 ModelProvider。

    响应可以是 response_model 实例或 dict(dict 会经 Pydantic 校验,
    与真实 Provider 行为一致)。记录每次调用供断言。
    """

    def __init__(self, responses: list[BaseModel | dict] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def generate_structured(self, *, system_prompt: str, user_payload: dict,
                            response_model: type[BaseModel]):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_payload": user_payload,
            "response_model": response_model.__name__,
        })
        if not self.responses:
            raise AssertionError("FakeModelProvider 没有更多预制响应")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            response = response_model.model_validate(response)
        assert isinstance(response, response_model)
        return response, ModelUsage(input_tokens=100, output_tokens=50)
