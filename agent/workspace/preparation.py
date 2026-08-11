"""为一次 Agent 运行准备并恢复固定 GitHub 源码快照。"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import SourceSnapshotError
from agent.workspace.source_repository import GitHubSourceRepository, SourceSnapshot
from agent.workspace.versioning import GitHubMainRevisionReader


_SOURCE_REF = re.compile(
    r"^source://(?P<run>[0-9a-f-]{36})/(?P<sha>[0-9a-f]{40})$"
)


class SourceWorkspace(Protocol):
    async def prepare(self, run_id: UUID) -> tuple[str, SourceSnapshot]: ...

    def resolve(self, reference: str) -> SourceSnapshot: ...


class _SnapshotMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitHubSourceWorkspace:
    """把 revision 与 archive 组合成可由 checkpoint 安全引用的固定快照。"""

    def __init__(
        self,
        root: Path,
        revision_reader: GitHubMainRevisionReader,
        source_repository: GitHubSourceRepository,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._revision_reader = revision_reader
        self._source_repository = source_repository

    async def prepare(self, run_id: UUID) -> tuple[str, SourceSnapshot]:
        run_root = self._run_root(run_id)
        metadata_path = run_root / "source.json"
        if metadata_path.is_file():
            metadata = self._read_metadata(metadata_path)
            return self._reference(metadata), self._snapshot(metadata)

        recovered = self._recover_interrupted_fetch(run_id, run_root)
        if recovered is not None:
            self._write_metadata(metadata_path, recovered)
            return self._reference(recovered), self._snapshot(recovered)

        base_sha = await self._revision_reader.read_main_sha()
        destination = run_root / base_sha / "snapshot"
        snapshot = await self._source_repository.fetch_snapshot(base_sha, destination)
        metadata = _SnapshotMetadata(
            run_id=run_id,
            base_sha=base_sha,
            source_snapshot_sha256=snapshot.source_snapshot_sha256,
        )
        self._write_metadata(metadata_path, metadata)
        return self._reference(metadata), snapshot

    def resolve(self, reference: str) -> SourceSnapshot:
        match = _SOURCE_REF.fullmatch(reference)
        if match is None:
            raise SourceSnapshotError("source snapshot reference is invalid")
        run_id = UUID(match.group("run"))
        metadata = self._read_metadata(self._run_root(run_id) / "source.json")
        if metadata.base_sha != match.group("sha"):
            raise SourceSnapshotError("source snapshot reference does not match metadata")
        return self._snapshot(metadata)

    def _snapshot(self, metadata: _SnapshotMetadata) -> SourceSnapshot:
        base = self._run_root(metadata.run_id) / metadata.base_sha
        snapshot = SourceSnapshot(
            base_sha=metadata.base_sha,
            source_snapshot_sha256=metadata.source_snapshot_sha256,
            root=base / "snapshot",
            archive_path=base / "snapshot.tar.gz",
        )
        if not snapshot.root.is_dir() or not snapshot.archive_path.is_file():
            raise SourceSnapshotError("source snapshot files are missing")
        actual_hash = hashlib.sha256(snapshot.archive_path.read_bytes()).hexdigest()
        if actual_hash != snapshot.source_snapshot_sha256:
            raise SourceSnapshotError("source snapshot archive integrity check failed")
        return snapshot

    def _recover_interrupted_fetch(
        self,
        run_id: UUID,
        run_root: Path,
    ) -> _SnapshotMetadata | None:
        if not run_root.is_dir():
            return None
        candidates = [
            item
            for item in run_root.iterdir()
            if item.is_dir() and re.fullmatch(r"[0-9a-f]{40}", item.name)
        ]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise SourceSnapshotError("source workspace has ambiguous snapshots")
        base = candidates[0]
        archive = base / "snapshot.tar.gz"
        snapshot = base / "snapshot"
        if not archive.is_file() or not snapshot.is_dir():
            if not archive.exists() and not snapshot.exists() and not any(base.iterdir()):
                # 下载在写入 archive 前失败只会留下空 SHA 目录，可安全清理后重试。
                base.rmdir()
                return None
            raise SourceSnapshotError("source workspace contains an incomplete snapshot")
        return _SnapshotMetadata(
            run_id=run_id,
            base_sha=base.name,
            source_snapshot_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )

    def _run_root(self, run_id: UUID) -> Path:
        path = (self._root / str(run_id)).resolve()
        if self._root not in path.parents:
            raise SourceSnapshotError("source workspace path is invalid")
        return path

    @staticmethod
    def _reference(metadata: _SnapshotMetadata) -> str:
        return f"source://{metadata.run_id}/{metadata.base_sha}"

    @staticmethod
    def _read_metadata(path: Path) -> _SnapshotMetadata:
        try:
            return _SnapshotMetadata.model_validate_json(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise SourceSnapshotError("source snapshot metadata is invalid") from exc

    @staticmethod
    def _write_metadata(path: Path, metadata: _SnapshotMetadata) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".source.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                json.dump(metadata.model_dump(mode="json"), temporary, sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
            path.chmod(0o600)
        except OSError as exc:
            raise SourceSnapshotError("source snapshot metadata could not be stored") from exc
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
