import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from agent.domain.errors import (
    SandboxAuthenticationError,
    SandboxInvalidResponseError,
    SandboxJobConflictError,
    SandboxRequestRejectedError,
    SandboxUnavailableError,
)
from agent.sandbox.client import HttpSandboxClient
from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)


def _submission() -> SandboxArtifacts:
    source = b"snapshot"
    patch = b"patch"
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="a" * 40,
        source_snapshot_sha256=hashlib.sha256(source).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector="feedback_ab12cd_table",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    return SandboxArtifacts(job=job, source_archive=source, test_patch=patch)


def test_http_sandbox_client_sends_auth_and_idempotency_without_commands():
    submission = _submission()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/jobs"
        assert request.headers["authorization"] == "Bearer worker-secret"
        assert request.headers["idempotency-key"] == str(submission.job.job_id)
        payload = request.content.decode("utf-8")
        assert "command" not in payload
        assert "environment" not in payload
        now = datetime.now(UTC)
        result = SandboxResult(
            job_id=submission.job.job_id,
            status=SandboxStatus.COMPLETED,
            exit_code=0,
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
            )
            return await sandbox.submit(submission)

    assert asyncio.run(scenario()).job_id == submission.job.job_id


def test_http_sandbox_client_never_echoes_worker_response_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="MODEL_API_KEY=do-not-print")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
            )
            with pytest.raises(SandboxUnavailableError) as exc_info:
                await sandbox.submit(_submission())
            return str(exc_info.value)

    assert "do-not-print" not in asyncio.run(scenario())


def test_http_sandbox_client_retries_transient_failure_with_same_job_id():
    submission = _submission()
    seen_keys: list[str] = []
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers["idempotency-key"])
        if len(seen_keys) == 1:
            return httpx.Response(503, text="temporary")
        now = datetime.now(UTC)
        result = SandboxResult(
            job_id=submission.job.job_id,
            status=SandboxStatus.COMPLETED,
            exit_code=0,
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
                sleep=record_sleep,
            )
            return await sandbox.submit(submission)

    result = asyncio.run(scenario())

    assert result.job_id == submission.job.job_id
    assert seen_keys == [str(submission.job.job_id)] * 2
    assert delays == [1.0]


def test_http_sandbox_client_stops_after_three_transient_attempts():
    submission = _submission()
    seen_keys: list[str] = []
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers["idempotency-key"])
        return httpx.Response(503, text="temporary")

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
                sleep=record_sleep,
            )
            with pytest.raises(SandboxUnavailableError) as exc_info:
                await sandbox.submit(submission)
            return exc_info.value

    error = asyncio.run(scenario())

    assert seen_keys == [str(submission.job.job_id)] * 3
    assert delays == [1.0, 2.0]
    assert error.attempt == 3
    assert error.max_attempts == 3


def test_http_sandbox_client_does_not_retry_invalid_success_body():
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        return httpx.Response(200, text="not-a-sandbox-result")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
            )
            with pytest.raises(SandboxInvalidResponseError):
                await sandbox.submit(_submission())

    asyncio.run(scenario())
    assert requests == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, SandboxAuthenticationError),
        (409, SandboxJobConflictError),
        (400, SandboxRequestRejectedError),
    ],
)
def test_http_sandbox_client_does_not_retry_permanent_statuses(
    status: int,
    error_type: type[Exception],
) -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        return httpx.Response(status, text="must-not-be-exposed")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
            )
            with pytest.raises(error_type):
                await sandbox.submit(_submission())

    asyncio.run(scenario())
    assert requests == 1


def test_http_sandbox_client_stops_when_total_deadline_cannot_fit_retry():
    submission = _submission()
    requests = 0
    delays: list[float] = []

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        del request
        requests += 1
        clock.value = submission.job.limits.wall_timeout_seconds + 59.5
        return httpx.Response(503, text="temporary")

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sandbox = HttpSandboxClient(
                "http://sandbox.internal",
                credential="worker-secret",
                client=client,
                sleep=record_sleep,
                monotonic=clock,
            )
            with pytest.raises(SandboxUnavailableError) as exc_info:
                await sandbox.submit(submission)
            return exc_info.value

    error = asyncio.run(scenario())

    assert requests == 1
    assert delays == []
    assert error.attempt == 1
    assert error.max_attempts == 3
