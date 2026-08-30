import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from agent.domain.errors import (
    AgentError,
    RepositoryUnavailableError,
    SourceAuthenticationError,
    SourceRevisionError,
)
from agent.domain.failures import (
    FailureEvent,
    FailureHandling,
    FailureRecorder,
    LocatedFailure,
    RetryContext,
    RetryDecision,
    RetryPolicy,
    failure_cause_from_exception,
    retry_delay,
)


_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_Sleep = Callable[[float], Awaitable[None]]


def read_extension_version(manifest_path: Path) -> str:
    """读取可选构建元数据；缺失或损坏不能阻断后端修复。"""

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.strip():
        return "unknown"
    return version.strip()


class GitHubMainRevisionReader:
    """通过已认证 Client 读取并校验任务固定使用的 GitHub main SHA。"""

    def __init__(
        self,
        repository: str,
        *,
        client: httpx.AsyncClient,
        api_url: str = "https://api.github.com",
        max_transport_retries: int = 2,
        sleep: _Sleep = asyncio.sleep,
        retry_policy: RetryPolicy | None = None,
        failure_recorder: FailureRecorder | None = None,
    ) -> None:
        if not _GITHUB_REPOSITORY.fullmatch(repository):
            raise SourceRevisionError("GITHUB_REPOSITORY must use owner/name format")
        if max_transport_retries not in {0, 1, 2}:
            raise ValueError("max_transport_retries must be between 0 and 2")
        self._repository = repository
        self._client = client
        self._api_url = api_url.rstrip("/")
        self._max_transport_retries = max_transport_retries
        self._sleep = sleep
        self._retry_policy = retry_policy or RetryPolicy()
        self._failure_recorder = failure_recorder or FailureRecorder()

    async def read_main_sha(self) -> str:
        max_attempts = self._max_transport_retries + 1
        for attempt in range(1, max_attempts + 1):
            response: httpx.Response | None = None
            try:
                response = await self._client.get(
                    f"{self._api_url}/repos/{self._repository}/commits/main",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            except httpx.HTTPError as exc:
                error: AgentError = RepositoryUnavailableError(
                    "GitHub revision transport failed",
                    safe_details={"error_type": type(exc).__name__[:120]},
                )
            else:
                error, sha = _revision_result(response)
                if error is None:
                    assert sha is not None
                    return sha

            error.attempt = attempt
            error.max_attempts = max_attempts
            error.operation = "read_main_revision"
            delay = retry_delay(attempt, _retry_after_seconds(response))
            cause = failure_cause_from_exception(error)
            decision = self._retry_policy.decide(
                cause,
                RetryContext(
                    attempt=attempt,
                    max_attempts=max_attempts,
                    operation_id=f"{self._repository}:main",
                    idempotent=True,
                ),
                delay_seconds=delay,
            )
            if decision is RetryDecision.STOP:
                self._record_failure(error, handling=FailureHandling.STOP)
                raise error
            self._record_failure(
                error,
                handling=FailureHandling.TRANSPORT_RETRY,
                delay_seconds=delay,
            )
            await self._sleep(delay)

        raise AssertionError("GitHub revision retry loop must return or raise")

    def _record_failure(
        self,
        error: AgentError,
        *,
        handling: FailureHandling,
        delay_seconds: float | None = None,
    ) -> None:
        cause = failure_cause_from_exception(error)
        self._failure_recorder.record(
            FailureEvent(
                failure=LocatedFailure(
                    cause=cause,
                    phase="repository",
                    node="read_main_revision",
                ),
                attempt=getattr(error, "attempt", 1),
                max_attempts=getattr(error, "max_attempts", 1),
                handling=handling,
                delay_seconds=delay_seconds,
            )
        )


def _revision_result(
    response: httpx.Response,
) -> tuple[AgentError | None, str | None]:
    if response.status_code >= 400:
        return _revision_http_error(response), None
    try:
        payload = response.json()
    except ValueError:
        return (
            SourceRevisionError(
                "GitHub returned invalid revision JSON",
                safe_details={"reason": "invalid_json"},
            ),
            None,
        )
    sha = payload.get("sha") if isinstance(payload, dict) else None
    # 只接受完整 commit SHA，拒绝分支名、短 SHA 和响应正文中的任意字符串。
    if not isinstance(sha, str) or not _COMMIT_SHA.fullmatch(sha):
        return (
            SourceRevisionError(
                "GitHub returned an invalid main commit SHA",
                safe_details={"reason": "invalid_sha"},
            ),
            None,
        )
    return None, sha


def _revision_http_error(response: httpx.Response) -> AgentError:
    status = response.status_code
    details: dict[str, str | int | bool | None] = {"http_status": status}
    if _is_rate_limited(response):
        details["rate_limited"] = True
        return RepositoryUnavailableError(
            "GitHub revision request was rate limited",
            safe_details=details,
        )
    if status == 408 or status >= 500:
        return RepositoryUnavailableError(
            f"GitHub revision request failed with status {status}",
            safe_details=details,
        )
    if status in {401, 403}:
        return SourceAuthenticationError(
            "GitHub source authentication failed",
            safe_details=details,
        )
    details["reason"] = "revision_request_rejected"
    return SourceRevisionError(
        f"GitHub revision request failed with status {status}",
        safe_details=details,
    )


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    return response.status_code == 403 and (
        response.headers.get("x-ratelimit-remaining") == "0"
        or response.headers.get("Retry-After") is not None
    )


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None or not _is_rate_limited(response):
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None
