import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agent.domain.errors import SourceRevisionError
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
