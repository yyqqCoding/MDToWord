import asyncio
import hashlib
import io
import os
import tarfile
from pathlib import Path

import httpx
import pytest

from agent.domain.errors import SourceSnapshotError
from agent.workspace.source_repository import (
    GitHubSourceRepository,
    materialize_snapshot_archive,
)


def _archive(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if symlink is not None:
            info = tarfile.TarInfo("repo-root/backend/tests/test_link.py")
            info.type = tarfile.SYMTYPE
            info.linkname = symlink
            archive.addfile(info)
    return output.getvalue()


def test_github_source_repository_materializes_validated_snapshot(tmp_path: Path):
    sha = "a" * 40
    content = _archive(
        {
            "repo-root/backend/app/normalizer.py": b"def normalize():\n    pass\n",
            "repo-root/README.md": b"summary\n",
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/example/md-to-word/tarball/{sha}"
        return httpx.Response(200, content=content)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = GitHubSourceRepository("example/md-to-word", client=client)
            return await repository.fetch_snapshot(sha, tmp_path / "snapshot")

    snapshot = asyncio.run(scenario())

    assert snapshot.base_sha == sha
    assert snapshot.source_snapshot_sha256 == hashlib.sha256(content).hexdigest()
    assert snapshot.root == (tmp_path / "snapshot").resolve()
    assert (snapshot.root / "backend/app/normalizer.py").is_file()
    assert snapshot.archive_path.is_file()


def test_github_source_repository_follows_archive_redirect(tmp_path: Path):
    sha = "c" * 40
    content = _archive({"repo-root/README.md": b"summary\n"})
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == f"/repos/example/md-to-word/tarball/{sha}":
            return httpx.Response(302, headers={"Location": "/archive.tar.gz"})
        assert request.url.path == "/archive.tar.gz"
        return httpx.Response(200, content=content)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = GitHubSourceRepository("example/md-to-word", client=client)
            return await repository.fetch_snapshot(sha, tmp_path / "snapshot")

    snapshot = asyncio.run(scenario())

    assert requests == [
        f"/repos/example/md-to-word/tarball/{sha}",
        "/archive.tar.gz",
    ]
    assert (snapshot.root / "README.md").read_text("utf-8") == "summary\n"


def test_materialize_snapshot_archive_normalizes_directory_permissions(
    tmp_path: Path,
):
    archive_path = tmp_path / "source.tar.gz"
    archive_path.write_bytes(
        _archive({"repo-root/backend/pytest.toml": b"[pytest]\n"})
    )

    previous_umask = os.umask(0o077)
    try:
        materialize_snapshot_archive(archive_path, tmp_path / "snapshot")
    finally:
        os.umask(previous_umask)

    assert ((tmp_path / "snapshot/backend").stat().st_mode & 0o777) == 0o755
    assert ((tmp_path / "snapshot/backend/pytest.toml").stat().st_mode & 0o777) == 0o644


@pytest.mark.parametrize(
    "content",
    (
        _archive({"repo-root/../../outside.txt": b"escape"}),
        _archive(
            {"repo-root/README.md": b"summary"},
            symlink="../../outside.txt",
        ),
    ),
)
def test_github_source_repository_rejects_unsafe_archive(
    tmp_path: Path,
    content: bytes,
):
    sha = "b" * 40

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = GitHubSourceRepository("example/md-to-word", client=client)
            with pytest.raises(SourceSnapshotError):
                await repository.fetch_snapshot(sha, tmp_path / "snapshot")

    asyncio.run(scenario())

    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "snapshot").exists()
