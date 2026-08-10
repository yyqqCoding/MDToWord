from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from agent.domain.enums import FeedbackStatus
from agent.domain.errors import (
    ClaimTokenMismatchError,
    ConcurrentFeedbackUpdateError,
    FeedbackNotFoundError,
    RepositoryError,
)
from agent.domain.models import FeedbackRecord
from agent.domain.transitions import ensure_feedback_transition


_OPEN_STATUS_VALUES = (
    "pending,claimed,gating,reproducing,repairing,validating,validated,publishing,pr_opened"
)


class SupabaseFeedbackRepository:
    """Supabase REST 适配器；凭证不进入错误信息或领域返回值。"""

    def __init__(
        self,
        supabase_url: str,
        agent_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = supabase_url.rstrip("/")
        self._headers = {
            "apikey": agent_key,
            "Authorization": f"Bearer {agent_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, feedback_id: UUID) -> FeedbackRecord | None:
        response = await self._request(
            "GET",
            "/rest/v1/feedback",
            params={"id": f"eq.{feedback_id}", "select": "*", "limit": "1"},
        )
        rows = _response_rows(response)
        return FeedbackRecord.model_validate(rows[0]) if rows else None

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None:
        # 租约必须以数据库时钟为准，避免多台 Controller 的系统时间漂移。
        del now  # PostgreSQL is the lease clock authority.
        claim_token = uuid4()
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_agent_feedback",
            json={
                "p_claim_token": str(claim_token),
                "p_lease_seconds": lease_seconds,
                "p_max_attempts": max_attempts,
            },
        )
        rows = _response_rows(response)
        return FeedbackRecord.model_validate(rows[0]) if rows else None

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
        current = await self.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError(f"feedback {feedback_id} does not exist")
        if current.claim_token != claim_token:
            raise ClaimTokenMismatchError(
                f"claim token does not own feedback {feedback_id}"
            )
        ensure_feedback_transition(current.status, target)

        payload: dict[str, object] = {
            "status": target.value,
            "updated_at": (now or datetime.now(UTC)).isoformat(),
            "last_error_code": error_code,
            "last_error_message": error_message,
        }
        if current.status is FeedbackStatus.STALE_BASE and target is FeedbackStatus.PENDING:
            if current.stale_requeue_count >= 1:
                raise ConcurrentFeedbackUpdateError(
                    "stale base feedback has already been requeued"
                )
            payload.update(
                {
                    "stale_requeue_count": 1,
                    "claim_token": None,
                    "claimed_at": None,
                }
            )

        response = await self._request(
            "PATCH",
            "/rest/v1/feedback",
            params={
                # id + token + 原状态组成 compare-and-set，防止旧 Worker 覆盖新租约。
                "id": f"eq.{feedback_id}",
                "claim_token": f"eq.{claim_token}",
                "status": f"eq.{current.status.value}",
                "select": "*",
            },
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        rows = _response_rows(response)
        if not rows:
            # 空结果代表读取后发生并发更新，不能把它误报成成功的幂等写入。
            raise ConcurrentFeedbackUpdateError(
                f"feedback {feedback_id} changed during transition"
            )
        return FeedbackRecord.model_validate(rows[0])

    async def find_open_by_fingerprint(
        self,
        content_fingerprint: str,
        *,
        excluding_feedback_id: UUID | None = None,
    ) -> FeedbackRecord | None:
        params = {
            "content_fingerprint": f"eq.{content_fingerprint}",
            "status": f"in.({_OPEN_STATUS_VALUES})",
            "select": "*",
            "order": "created_at.asc",
            "limit": "1",
        }
        if excluding_feedback_id is not None:
            params["id"] = f"neq.{excluding_feedback_id}"
        response = await self._request("GET", "/rest/v1/feedback", params=params)
        rows = _response_rows(response)
        return FeedbackRecord.model_validate(rows[0]) if rows else None

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: object,
    ) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})
        headers = {**self._headers, **dict(extra_headers)}
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            # 仅记录异常类型；URL、Header、响应体都可能含密钥或用户内容。
            raise RepositoryError(
                f"Supabase request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            # PostgREST 错误正文可能回显字段值，因此这里只暴露状态码。
            raise RepositoryError(
                f"Supabase request failed with status {response.status_code}"
            )
        return response


def _response_rows(response: httpx.Response) -> list[dict[str, object]]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RepositoryError("Supabase returned an invalid JSON response") from exc
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise RepositoryError("Supabase returned an unexpected response shape")
    return payload
