import hashlib
from pathlib import Path
from uuid import uuid4

from agent.workspace.preparation import GitHubSourceWorkspace
from agent.workspace.source_repository import SourceSnapshot


BASE_SHA = "a" * 40


class _RevisionReader:
    def __init__(self) -> None:
        self.calls = 0

    async def read_main_sha(self) -> str:
        self.calls += 1
        return BASE_SHA


class _SourceRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_snapshot(self, base_sha: str, destination: Path) -> SourceSnapshot:
        self.calls += 1
        destination.mkdir(parents=True)
        (destination / "README.md").write_text("snapshot", encoding="utf-8")
        archive = destination.with_name("snapshot.tar.gz")
        archive.write_bytes(b"archive")
        return SourceSnapshot(
            base_sha=base_sha,
            source_snapshot_sha256=hashlib.sha256(b"archive").hexdigest(),
            root=destination,
            archive_path=archive,
        )


def test_source_workspace_reuses_pinned_snapshot_on_resume(tmp_path: Path) -> None:
    import asyncio

    revision = _RevisionReader()
    repository = _SourceRepository()
    workspace = GitHubSourceWorkspace(tmp_path, revision, repository)
    run_id = uuid4()

    first_ref, first = asyncio.run(workspace.prepare(run_id))
    second_ref, second = asyncio.run(workspace.prepare(run_id))

    assert first_ref == second_ref
    assert first.base_sha == second.base_sha == BASE_SHA
    assert revision.calls == 1
    assert repository.calls == 1
    assert workspace.resolve(first_ref).source_snapshot_sha256 == first.source_snapshot_sha256


def test_source_workspace_retries_after_empty_interrupted_fetch(tmp_path: Path) -> None:
    import asyncio

    revision = _RevisionReader()
    repository = _SourceRepository()
    workspace = GitHubSourceWorkspace(tmp_path, revision, repository)
    run_id = uuid4()
    # GitHub archive 下载失败时可能只留下按 SHA 创建的空父目录。
    (tmp_path / str(run_id) / BASE_SHA).mkdir(parents=True)

    reference, snapshot = asyncio.run(workspace.prepare(run_id))

    assert reference == f"source://{run_id}/{BASE_SHA}"
    assert snapshot.root.is_dir()
    assert repository.calls == 1
