"""Controller 侧 Sandbox Client，只提交严格契约并校验对应 Job 的结果。"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx
from pydantic import SecretStr

from agent.domain.errors import (
    SandboxAuthenticationError,
    SandboxInvalidResponseError,
    SandboxJobConflictError,
    SandboxRequestRejectedError,
    SandboxUnavailableError,
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
from agent.sandbox.contracts import SandboxArtifacts, SandboxResult


_Sleep = Callable[[float], Awaitable[None]]
_Monotonic = Callable[[], float]
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RECONCILIATION_GRACE_SECONDS = 60.0


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
        max_transport_retries: int = 2,
        sleep: _Sleep = asyncio.sleep,
        monotonic: _Monotonic = time.monotonic,
        retry_policy: RetryPolicy | None = None,
        failure_recorder: FailureRecorder | None = None,
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
        self._monotonic = monotonic
        self._retry_policy = retry_policy or RetryPolicy()
        self._failure_recorder = failure_recorder or FailureRecorder()

    async def submit(self, artifacts: SandboxArtifacts) -> SandboxResult:
        payload = artifacts.to_wire()
        fingerprint = artifacts.request_fingerprint()
        started = self._monotonic()
        deadline = (
            started
            + artifacts.job.limits.wall_timeout_seconds
            + _RECONCILIATION_GRACE_SECONDS
        )
        for attempt in range(self._max_transport_retries + 1):
            attempt_number = attempt + 1
            max_attempts = self._max_transport_retries + 1
            remaining = max(0.0, deadline - self._monotonic())
            if remaining <= 0:
                error = SandboxUnavailableError(
                    "sandbox submit deadline was exhausted",
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    operation="submit_job",
                    safe_details={"deadline_exhausted": True},
                )
                self._record_failure(error, handling=FailureHandling.STOP)
                raise error
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
                    timeout=min(
                        artifacts.job.limits.wall_timeout_seconds + 30,
                        remaining,
                    ),
                )
            except httpx.HTTPError as exc:
                error = SandboxUnavailableError(
                    f"sandbox request failed: {type(exc).__name__}",
                    safe_details={"error_type": type(exc).__name__[:120]},
                )
                response = None
            else:
                if response.status_code < 400:
                    try:
                        result = SandboxResult.model_validate_json(response.content)
                    except ValueError:
                        error = SandboxInvalidResponseError(
                            "sandbox returned an invalid result"
                        )
                    else:
                        if result.job_id != artifacts.job.job_id:
                            error = SandboxInvalidResponseError(
                                "sandbox returned a mismatched job id"
                            )
                        else:
                            return result
                else:
                    error = _status_error(response.status_code)

            error.attempt = attempt_number
            error.max_attempts = max_attempts
            error.operation = "submit_job"
            delay = retry_delay(
                attempt_number,
                _retry_after_seconds(response),
            )
            remaining = max(0.0, deadline - self._monotonic())
            decision = self._retry_policy.decide(
                failure_cause_from_exception(error),
                RetryContext(
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    deadline_remaining_seconds=remaining,
                    operation_id=f"{artifacts.job.job_id}:{fingerprint}",
                    idempotent=True,
                ),
                delay_seconds=delay,
            )
            if decision is RetryDecision.STOP:
                self._record_failure(
                    error,
                    handling=FailureHandling.STOP,
                    deadline_remaining_seconds=remaining,
                )
                raise error
            self._record_failure(
                error,
                handling=FailureHandling.TRANSPORT_RETRY,
                delay_seconds=delay,
                deadline_remaining_seconds=remaining,
            )
            await self._sleep(delay)

        raise AssertionError("sandbox retry loop must return or raise")

    def _record_failure(
        self,
        error: Exception,
        *,
        handling: FailureHandling,
        delay_seconds: float | None = None,
        deadline_remaining_seconds: float | None = None,
    ) -> None:
        cause = failure_cause_from_exception(error)
        self._failure_recorder.record(
            FailureEvent(
                failure=LocatedFailure(
                    cause=cause,
                    phase="sandbox",
                    node="submit_job",
                ),
                attempt=getattr(error, "attempt", 1),
                max_attempts=getattr(error, "max_attempts", 1),
                handling=handling,
                delay_seconds=delay_seconds,
                deadline_remaining_seconds=deadline_remaining_seconds,
            )
        )


def _status_error(status: int):
    details = {"http_status": status}
    if status in _RETRYABLE_STATUS_CODES:
        return SandboxUnavailableError(
            f"sandbox request failed with status {status}",
            safe_details=details,
        )
    if status in {401, 403}:
        return SandboxAuthenticationError(
            "sandbox worker authentication failed",
            safe_details=details,
        )
    if status == 409:
        return SandboxJobConflictError(
            "sandbox job id conflicts with another request",
            safe_details=details,
        )
    return SandboxRequestRejectedError(
        f"sandbox request was rejected with status {status}",
        safe_details=details,
    )


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None or response.status_code != 429:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None
