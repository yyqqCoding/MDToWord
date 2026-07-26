"""Feedback Repository:封装全部 Supabase 访问。

要求(阶段 02 spec §3):httpx.Client、每请求超时、认证 Header 只在本模块构建、
错误响应截断记录、429/5xx 有限重试、401/403 不重试。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

import httpx

from agent.domain import Feedback
from agent.exceptions import SupabaseError

ERROR_BODY_LIMIT = 300
RETRYABLE_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)


class FeedbackRepository(Protocol):
    def get_feedback(self, feedback_id: UUID) -> Feedback | None: ...

    def claim_feedback(self, feedback_id: UUID, claim_token: UUID,
                       max_attempts: int = 3) -> Feedback | None: ...

    def create_run(self, feedback_id: UUID, *, provider: str, model: str,
                   status: str = "created",
                   workflow_run_id: str | None = None) -> UUID: ...

    def update_feedback(self, feedback_id: UUID, **fields: Any) -> None: ...

    def update_run(self, run_id: UUID, **fields: Any) -> None: ...

    def find_open_resolution(self, fingerprint: str) -> Feedback | None: ...


class SupabaseFeedbackRepository:
    """基于 PostgREST 的实现。Service Role Key 只在这里进 Header,不外泄。"""

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=supabase_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
            },
        )

    # -- HTTP 基础设施 --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: SupabaseError | None = None
        for attempt in range(RETRYABLE_ATTEMPTS):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = SupabaseError(f"Supabase 请求失败: {exc}", status_code=None)
            else:
                if response.status_code < 400:
                    return response
                body = response.text[:ERROR_BODY_LIMIT]
                error = SupabaseError(
                    f"Supabase {method} {path} 返回 {response.status_code}: {body}",
                    status_code=response.status_code,
                )
                if response.status_code in (401, 403):
                    raise error  # 不重试
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = error
                else:
                    raise error
            if attempt < RETRYABLE_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
        assert last_error is not None
        raise last_error

    # -- Repository 接口 ------------------------------------------------------

    def get_feedback(self, feedback_id: UUID) -> Feedback | None:
        response = self._request(
            "GET", "/rest/v1/feedback",
            params={"id": f"eq.{feedback_id}", "select": "*", "limit": "1"},
        )
        rows = response.json()
        return Feedback.model_validate(rows[0]) if rows else None

    def claim_feedback(self, feedback_id: UUID, claim_token: UUID,
                       max_attempts: int = 3) -> Feedback | None:
        response = self._request(
            "POST", "/rest/v1/rpc/claim_feedback",
            json={
                "p_feedback_id": str(feedback_id),
                "p_claim_token": str(claim_token),
                "p_max_attempts": max_attempts,
            },
        )
        rows = response.json()
        return Feedback.model_validate(rows[0]) if rows else None

    def create_run(self, feedback_id: UUID, *, provider: str, model: str,
                   status: str = "created",
                   workflow_run_id: str | None = None) -> UUID:
        response = self._request(
            "POST", "/rest/v1/agent_runs",
            headers={"Prefer": "return=representation"},
            json={
                "feedback_id": str(feedback_id),
                "provider": provider,
                "model": model,
                "status": status,
                "workflow_run_id": workflow_run_id,
            },
        )
        return UUID(response.json()[0]["id"])

    def update_feedback(self, feedback_id: UUID, **fields: Any) -> None:
        fields.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        self._request(
            "PATCH", "/rest/v1/feedback",
            params={"id": f"eq.{feedback_id}"},
            json=_jsonable(fields),
        )

    def update_run(self, run_id: UUID, **fields: Any) -> None:
        self._request(
            "PATCH", "/rest/v1/agent_runs",
            params={"id": f"eq.{run_id}"},
            json=_jsonable(fields),
        )

    def find_open_resolution(self, fingerprint: str) -> Feedback | None:
        """同指纹且已有处理结果(PR 已建/已解决/待发布)的反馈 → 判重复。"""
        response = self._request(
            "GET", "/rest/v1/feedback",
            params={
                "content_fingerprint": f"eq.{fingerprint}",
                "status": "in.(pr_opened,resolved,validated_but_unpublished)",
                "select": "*",
                "limit": "1",
            },
        )
        rows = response.json()
        return Feedback.model_validate(rows[0]) if rows else None

    def close(self) -> None:
        self._client.close()


def _jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items()}
