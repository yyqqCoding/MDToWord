from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx

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
    ConcurrentFeedbackUpdateError,
    FeedbackNotFoundError,
    RepositoryError,
)
from agent.domain.gate import GateResult
from agent.domain.models import AgentRunRecord, FeedbackRecord
from agent.domain.reproduction import ReproductionReport
from agent.domain.repair import RepairDisposition, RepairReport, ValidationResult
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
        return _feedback_from_row(rows[0]) if rows else None

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
        return _feedback_from_row(rows[0]) if rows else None

    async def claim_by_id(
        self,
        feedback_id: UUID,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None:
        del now  # PostgreSQL is the lease clock authority.
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_agent_feedback",
            json={
                "p_feedback_id": str(feedback_id),
                "p_claim_token": str(uuid4()),
                "p_lease_seconds": lease_seconds,
                "p_max_attempts": max_attempts,
            },
        )
        rows = _response_rows(response)
        return _feedback_from_row(rows[0]) if rows else None

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
        pr_url: str | None = None,
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
        if category is not None:
            payload["category"] = category.value
        if risk is not None:
            payload["risk"] = risk.value
        if pr_url is not None:
            payload["pr_url"] = pr_url
        if target is FeedbackStatus.PR_OPENED:
            payload["resolved_at"] = (now or datetime.now(UTC)).isoformat()

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
        return _feedback_from_row(rows[0])

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
        return _feedback_from_row(rows[0]) if rows else None

    async def retry_publication(
        self,
        feedback_id: UUID,
        *,
        claim_token: UUID,
    ) -> FeedbackRecord:
        current = await self.get(feedback_id)
        if current is None:
            raise FeedbackNotFoundError(f"feedback {feedback_id} does not exist")
        if current.claim_token != claim_token:
            raise ClaimTokenMismatchError(
                f"claim token does not own feedback {feedback_id}"
            )
        if current.status is FeedbackStatus.PUBLISHING:
            return current
        if (
            current.status is not FeedbackStatus.FAILED
            or not _is_publication_error(current.last_error_code)
        ):
            raise ConcurrentFeedbackUpdateError(
                "only a failed publication can be retried"
            )
        response = await self._request(
            "PATCH",
            "/rest/v1/feedback",
            params={
                "id": f"eq.{feedback_id}",
                "claim_token": f"eq.{claim_token}",
                "status": f"eq.{FeedbackStatus.FAILED.value}",
                "last_error_code": f"eq.{current.last_error_code}",
                "select": "*",
            },
            headers={"Prefer": "return=representation"},
            json={
                "status": FeedbackStatus.PUBLISHING.value,
                "last_error_code": None,
                "last_error_message": None,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        rows = _response_rows(response)
        if not rows:
            raise ConcurrentFeedbackUpdateError(
                f"feedback {feedback_id} changed during publication retry"
            )
        return _feedback_from_row(rows[0])

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


def _feedback_from_row(row: dict[str, object]) -> FeedbackRecord:
    # 线上 feedback 表仍可能保留历史 Agent 列。适配层只投影当前领域字段，领域模型继续
    # 使用 extra=forbid，避免数据库布局细节泄漏到 Graph State 或模型上下文。
    known = {
        name: value
        for name, value in row.items()
        if name in FeedbackRecord.model_fields
    }
    return FeedbackRecord.model_validate(known)


class SupabaseAgentRunRepository:
    """通过 PostgREST 持久化运行摘要；checkpoint 使用独立私有连接。"""

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

    async def create(self, run: AgentRunRecord) -> AgentRunRecord:
        response = await self._request(
            "POST",
            "/rest/v1/agent_runs",
            headers={"Prefer": "return=representation"},
            json=run.model_dump(mode="json"),
        )
        return _run_from_response(response)

    async def get(self, run_id: UUID) -> AgentRunRecord | None:
        response = await self._request(
            "GET",
            "/rest/v1/agent_runs",
            params={"id": f"eq.{run_id}", "select": "*", "limit": "1"},
        )
        rows = _response_rows(response)
        return AgentRunRecord.model_validate(rows[0]) if rows else None

    async def find_resumable(self) -> AgentRunRecord | None:
        response = await self._request(
            "GET",
            "/rest/v1/agent_runs",
            params={
                "status": "in.(created,gating,preparing_source,reproducing,repairing,validating,publishing)",
                "select": "*",
                "order": "started_at.asc",
                "limit": "1",
            },
        )
        rows = _response_rows(response)
        return AgentRunRecord.model_validate(rows[0]) if rows else None

    async def mark_gating(self, run_id: UUID) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.GATING:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.CREATED,
            payload={"status": AgentRunStatus.GATING.value},
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.COMPLETED:
            if existing.route is not result.route:
                raise RepositoryError("completed agent run has a different route")
            return existing
        if existing.status is not AgentRunStatus.GATING:
            raise RepositoryError(
                f"agent run {run_id} cannot complete from {existing.status.value}"
            )
        return await self._patch(
            run_id,
            current=AgentRunStatus.GATING,
            payload={
                "status": AgentRunStatus.COMPLETED.value,
                "route": result.route.value,
                "category": result.category.value,
                "classification": result.model_dump(mode="json"),
                "model_calls": result.model_calls,
                "tool_calls": result.tool_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost": str(estimated_cost),
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.PREPARING_SOURCE:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.GATING,
            payload={
                "status": AgentRunStatus.PREPARING_SOURCE.value,
                "route": result.route.value,
                "category": result.category.value,
                "classification": result.model_dump(mode="json"),
                "model_calls": result.model_calls,
                "tool_calls": result.tool_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost": str(estimated_cost),
            },
        )

    async def mark_reproducing(
        self,
        run_id: UUID,
        *,
        base_sha: str,
    ) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.REPRODUCING:
            if existing.base_sha != base_sha:
                raise RepositoryError("reproducing run has a different base")
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.PREPARING_SOURCE,
            payload={
                "status": AgentRunStatus.REPRODUCING.value,
                "base_sha": base_sha,
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        target = (
            AgentRunStatus.SECURITY_REJECTED
            if security_rejected
            else (
                AgentRunStatus.REPAIRING
                if reproduction_confirmed
                else AgentRunStatus.COMPLETED
            )
        )
        if existing.status is target:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.REPRODUCING,
            payload={
                "status": target.value,
                "reproduction": report.model_dump(mode="json"),
                "model_calls": model_calls,
                "tool_calls": tool_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost": str(estimated_cost),
                "finished_at": (
                    None
                    if target is AgentRunStatus.REPAIRING
                    else datetime.now(UTC).isoformat()
                ),
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.VALIDATING:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.REPAIRING,
            payload={
                "status": AgentRunStatus.VALIDATING.value,
                "repair": report.model_dump(mode="json"),
                **_usage_payload(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                ),
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        target = (
            AgentRunStatus.SECURITY_REJECTED
            if security_rejected
            else AgentRunStatus.COMPLETED
        )
        if existing.status is target:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.REPAIRING,
            payload={
                "status": target.value,
                **(
                    {"route": GateRoute.NEEDS_HUMAN.value}
                    if report.disposition is RepairDisposition.NEEDS_HUMAN
                    else {}
                ),
                "repair": report.model_dump(mode="json"),
                "error_code": report.failure_code,
                "error_message": report.failure_summary,
                "finished_at": datetime.now(UTC).isoformat(),
                **_usage_payload(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                ),
            },
        )

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
        publish_pending: bool = False,
    ) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        target = (
            AgentRunStatus.PUBLISHING
            if result.passed and publish_pending
            else AgentRunStatus.COMPLETED
        )
        if existing.status is target:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.VALIDATING,
            payload={
                "status": target.value,
                "validation": result.model_dump(mode="json"),
                "validated_patch_sha256": (
                    result.validated_patch_sha256 if result.passed else None
                ),
                "error_code": result.failure_code,
                "error_message": result.failure_summary,
                "finished_at": (
                    None
                    if target is AgentRunStatus.PUBLISHING
                    else datetime.now(UTC).isoformat()
                ),
                **_usage_payload(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                ),
            },
        )

    async def complete_publication(
        self,
        run_id: UUID,
        *,
        pr_url: str,
        tool_calls: int,
    ) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.COMPLETED:
            if existing.pr_url != pr_url:
                raise RepositoryError(
                    "completed publication has a different pull request"
                )
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.PUBLISHING,
            payload={
                "status": AgentRunStatus.COMPLETED.value,
                "pr_url": pr_url,
                "tool_calls": tool_calls,
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )

    async def complete_stale_base(
        self,
        run_id: UUID,
        *,
        tool_calls: int,
    ) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.STALE_BASE:
            return existing
        return await self._patch(
            run_id,
            current=AgentRunStatus.PUBLISHING,
            payload={
                "status": AgentRunStatus.STALE_BASE.value,
                "tool_calls": tool_calls,
                "error_code": "stale_base",
                "error_message": "repository main changed before publication",
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )

    async def retry_publication(self, run_id: UUID) -> AgentRunRecord:
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.PUBLISHING:
            return existing
        if (
            existing.status is not AgentRunStatus.FAILED
            or not _is_publication_error(existing.error_code)
            or existing.validation is None
            or not bool(existing.validation.get("passed"))
        ):
            raise RepositoryError(
                "only a validated publication failure can be retried"
            )
        return await self._patch(
            run_id,
            current=AgentRunStatus.FAILED,
            payload={
                "status": AgentRunStatus.PUBLISHING.value,
                "error_code": None,
                "error_message": None,
                "finished_at": None,
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.BUDGET_EXHAUSTED:
            return existing
        return await self._patch(
            run_id,
            current=existing.status,
            payload={
                "status": AgentRunStatus.BUDGET_EXHAUSTED.value,
                "error_code": "budget_exhausted",
                "error_message": "run budget was exhausted",
                "finished_at": datetime.now(UTC).isoformat(),
                **_usage_payload(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                ),
            },
        )

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
        existing = await self.get(run_id)
        if existing is None:
            raise AgentRunNotFoundError(f"agent run {run_id} does not exist")
        if existing.status is AgentRunStatus.FAILED:
            return existing
        return await self._patch(
            run_id,
            current=existing.status,
            payload={
                "status": AgentRunStatus.FAILED.value,
                "error_code": error_code,
                "error_message": error_message,
                "finished_at": datetime.now(UTC).isoformat(),
                **_usage_payload(
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                ),
            },
        )

    async def _patch(
        self,
        run_id: UUID,
        *,
        current: AgentRunStatus,
        payload: dict[str, object],
    ) -> AgentRunRecord:
        response = await self._request(
            "PATCH",
            "/rest/v1/agent_runs",
            params={
                "id": f"eq.{run_id}",
                "status": f"eq.{current.value}",
                "select": "*",
            },
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        rows = _response_rows(response)
        if not rows:
            raise RepositoryError(f"agent run {run_id} changed during update")
        return AgentRunRecord.model_validate(rows[0])

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
            raise RepositoryError(
                f"Supabase request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise RepositoryError(
                f"Supabase request failed with status {response.status_code}"
            )
        return response


def _usage_payload(
    *,
    model_calls: int,
    tool_calls: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    estimated_cost: Decimal,
) -> dict[str, object]:
    return {
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": str(estimated_cost),
    }


def _is_publication_error(error_code: str | None) -> bool:
    return error_code in {
        "publication_failed",
        "publication_auth_error",
        "publication_conflict",
    }


def _run_from_response(response: httpx.Response) -> AgentRunRecord:
    rows = _response_rows(response)
    if not rows:
        raise RepositoryError("Supabase did not return the agent run")
    return AgentRunRecord.model_validate(rows[0])
