"""Controller 侧 Sandbox Client，只提交严格契约并校验对应 Job 的结果。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx
from pydantic import SecretStr

from agent.domain.errors import SandboxUnavailableError
from agent.sandbox.contracts import SandboxArtifacts, SandboxResult


_Sleep = Callable[[float], Awaitable[None]]
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class SandboxClient(Protocol):
    async def submit(self, artifacts: SandboxArtifacts) -> SandboxResult: ...


class HttpSandboxClient:
    """通过 Bearer 认证与 Job ID 幂等键调用独立 Worker。"""

    def __init__(
        self,
        base_url: str,
        *,
        credential: str,
        client: httpx.AsyncClient,
        max_transport_retries: int = 1,
        sleep: _Sleep = asyncio.sleep,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise SandboxUnavailableError("SANDBOX_WORKER_URL must use HTTP(S)")
        if not credential:
            raise SandboxUnavailableError("sandbox worker credential is empty")
        if max_transport_retries not in {0, 1, 2}:
            raise ValueError("max_transport_retries must be between 0 and 2")
        self._base_url = base_url.rstrip("/")
        self._credential = SecretStr(credential)
        self._client = client
        self._max_transport_retries = max_transport_retries
        self._sleep = sleep

    async def submit(self, artifacts: SandboxArtifacts) -> SandboxResult:
        payload = artifacts.to_wire()
        for attempt in range(self._max_transport_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/v1/jobs",
                    headers={
                        "Authorization": (
                            "Bearer " + self._credential.get_secret_value()
                        ),
                        "Idempotency-Key": str(artifacts.job.job_id),
                    },
                    json=payload,
                    timeout=artifacts.job.limits.wall_timeout_seconds + 30,
                )
            except httpx.HTTPError as exc:
                if attempt < self._max_transport_retries:
                    await self._sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                raise SandboxUnavailableError(
                    f"sandbox request failed: {type(exc).__name__}"
                ) from None

            if response.status_code >= 400:
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < self._max_transport_retries
                ):
                    await self._sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                # 不回显 Worker body，避免把不可信错误内容带入 Controller 日志。
                raise SandboxUnavailableError(
                    f"sandbox request failed with status {response.status_code}"
                )

            try:
                result = SandboxResult.model_validate_json(response.content)
            except ValueError:
                raise SandboxUnavailableError(
                    "sandbox returned an invalid result"
                ) from None
            if result.job_id != artifacts.job.job_id:
                raise SandboxUnavailableError("sandbox returned a mismatched job id")
            return result

        raise AssertionError("sandbox retry loop must return or raise")
