import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from agent.domain.errors import SandboxUnavailableError
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
