import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agent.domain.errors import (
    RepositoryUnavailableError,
    SourceAuthenticationError,
    SourceRevisionError,
)
from agent.domain.failures import FailureHandling, FailureRecorder
from agent.workspace.versioning import GitHubMainRevisionReader, read_extension_version


def test_missing_extension_manifest_returns_unknown(tmp_path: Path):
    assert read_extension_version(tmp_path / "missing.json") == "unknown"


def test_extension_version_is_read_from_built_manifest(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")

    assert read_extension_version(manifest) == "0.3.0"


def test_malformed_extension_manifest_does_not_block_backend_work(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("not json", encoding="utf-8")

    assert read_extension_version(manifest) == "unknown"


def test_github_reader_returns_validated_main_sha():
    expected_sha = "a" * 40

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/example/md-to-word/commits/main"
        return httpx.Response(200, json={"sha": expected_sha})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = GitHubMainRevisionReader("example/md-to-word", client=client)
            return await reader.read_main_sha()

    assert asyncio.run(scenario()) == expected_sha


def test_github_reader_rejects_invalid_sha_without_echoing_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sha": "secret response body"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = GitHubMainRevisionReader("example/md-to-word", client=client)
            with pytest.raises(SourceRevisionError) as exc_info:
                await reader.read_main_sha()
            return str(exc_info.value)

    message = asyncio.run(scenario())

    assert "invalid main commit SHA" in message
    assert "secret response body" not in message


def test_github_reader_reports_auth_failure_without_retrying():
    requests = 0
    events = []

    class Sink:
        def record_failure(self, event):
            events.append(event)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401, text="token=must-not-appear")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = GitHubMainRevisionReader(
                "example/md-to-word",
                client=client,
                failure_recorder=FailureRecorder(Sink()),
            )
            with pytest.raises(SourceAuthenticationError) as exc_info:
                await reader.read_main_sha()
            return exc_info.value

    error = asyncio.run(scenario())

    assert requests == 1
    assert error.attempt == 1
    assert error.max_attempts == 3
    assert error.safe_details == {"http_status": 401}
    assert "must-not-appear" not in str(error)
    assert len(events) == 1
    assert events[0].handling is FailureHandling.STOP
    assert events[0].failure.cause.code == "source_auth_error"


def test_github_reader_retries_rate_limit_and_upstream_failure():
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(503),
        httpx.Response(200, json={"sha": "b" * 40}),
    ]
    delays = []
    events = []

    class Sink:
        def record_failure(self, event):
            events.append(event)

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = GitHubMainRevisionReader(
                "example/md-to-word",
                client=client,
                sleep=sleep,
                failure_recorder=FailureRecorder(Sink()),
            )
            return await reader.read_main_sha()

    assert asyncio.run(scenario()) == "b" * 40
    assert delays == [1, 2]
    assert [event.handling for event in events] == [
        FailureHandling.TRANSPORT_RETRY,
        FailureHandling.TRANSPORT_RETRY,
    ]
    assert events[0].failure.cause.safe_details == {
        "http_status": 429,
        "rate_limited": True,
    }


def test_github_reader_stops_after_three_transient_failures():
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503)

    async def sleep(delay: float) -> None:
        del delay

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = GitHubMainRevisionReader(
                "example/md-to-word",
                client=client,
                sleep=sleep,
            )
            with pytest.raises(RepositoryUnavailableError) as exc_info:
                await reader.read_main_sha()
            return exc_info.value

    error = asyncio.run(scenario())

    assert requests == 3
    assert error.attempt == 3
    assert error.max_attempts == 3
    assert error.safe_details == {"http_status": 503}
