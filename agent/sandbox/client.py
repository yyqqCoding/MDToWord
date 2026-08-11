"""Controller 侧 Sandbox Client，只提交严格契约并校验对应 Job 的结果。"""

from typing import Protocol

import httpx
from pydantic import SecretStr

from agent.domain.errors import SandboxUnavailableError
from agent.sandbox.contracts import SandboxArtifacts, SandboxResult


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
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise SandboxUnavailableError("SANDBOX_WORKER_URL must use HTTP(S)")
        if not credential:
            raise SandboxUnavailableError("sandbox worker credential is empty")
        self._base_url = base_url.rstrip("/")
        self._credential = SecretStr(credential)
        self._client = client

    async def submit(self, artifacts: SandboxArtifacts) -> SandboxResult:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/jobs",
                headers={
                    "Authorization": (
                        "Bearer " + self._credential.get_secret_value()
                    ),
                    "Idempotency-Key": str(artifacts.job.job_id),
                },
                json=artifacts.to_wire(),
                timeout=artifacts.job.limits.wall_timeout_seconds + 30,
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailableError(
                f"sandbox request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            # 不回显 Worker body，避免把不可信错误内容带入 Controller 日志。
            raise SandboxUnavailableError(
                f"sandbox request failed with status {response.status_code}"
            )
        try:
            result = SandboxResult.model_validate_json(response.content)
        except ValueError as exc:
            raise SandboxUnavailableError("sandbox returned an invalid result") from exc
        if result.job_id != artifacts.job.job_id:
            raise SandboxUnavailableError("sandbox returned a mismatched job id")
        return result
