"""从固定 GitHub commit 获取并安全展开只读源码快照。"""

import hashlib
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import SourceSnapshotError


_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_EXPANDED_BYTES = 100 * 1024 * 1024
_MAX_MEMBER_BYTES = 10 * 1024 * 1024


class SourceSnapshot(BaseModel):
    """一次运行固定使用的源码内容与完整性摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    root: Path
    archive_path: Path


class GitHubSourceRepository:
    """按完整 commit SHA 下载 GitHub archive，并安全展开成只读基线快照。"""

    def __init__(
        self,
        repository: str,
        *,
        client: httpx.AsyncClient,
        api_url: str = "https://api.github.com",
    ) -> None:
        if not _GITHUB_REPOSITORY.fullmatch(repository):
            raise SourceSnapshotError("GITHUB_REPOSITORY must use owner/name format")
        self._repository = repository
        self._client = client
        self._api_url = api_url.rstrip("/")

    async def fetch_snapshot(self, base_sha: str, destination: Path) -> SourceSnapshot:
        if not _COMMIT_SHA.fullmatch(base_sha):
            raise SourceSnapshotError("base_sha must be a full commit SHA")
        destination = destination.resolve()
        if destination.exists():
            raise SourceSnapshotError("snapshot destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive_path = destination.with_name(f"{destination.name}.tar.gz")
        if archive_path.exists():
            raise SourceSnapshotError("snapshot archive destination already exists")

        try:
            async with self._client.stream(
                "GET",
                f"{self._api_url}/repos/{self._repository}/tarball/{base_sha}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ) as response:
                if response.status_code >= 400:
                    raise SourceSnapshotError(
                        "GitHub snapshot request failed with status "
                        f"{response.status_code}"
                    )
                chunks: list[bytes] = []
                archive_size = 0
                # 不信任 Content-Length；流式累计才能覆盖缺失或伪造长度的响应。
                async for chunk in response.aiter_bytes():
                    archive_size += len(chunk)
                    if archive_size > _MAX_ARCHIVE_BYTES:
                        raise SourceSnapshotError(
                            "GitHub snapshot archive size is invalid"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
        except SourceSnapshotError:
            raise
        except httpx.HTTPError as exc:
            raise SourceSnapshotError(
                f"GitHub snapshot request failed: {type(exc).__name__}"
            ) from exc
        if not content:
            raise SourceSnapshotError("GitHub snapshot archive size is invalid")

        _atomic_write(archive_path, content)
        try:
            materialize_snapshot_archive(archive_path, destination)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        return SourceSnapshot(
            base_sha=base_sha,
            source_snapshot_sha256=hashlib.sha256(content).hexdigest(),
            root=destination,
            archive_path=archive_path,
        )


def materialize_snapshot_archive(archive_path: Path, destination: Path) -> None:
    """在临时目录校验并展开 tar，全部成功后再原子发布快照。"""

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            total_size = 0
            archive_root: str | None = None
            for member in archive.getmembers():
                # GitHub tarball 应只有一个顶层目录，内部路径必须保持仓库相对路径。
                parts = PurePosixPath(member.name).parts
                if any(part in {"", ".", "..", ".git"} for part in parts):
                    raise SourceSnapshotError("snapshot archive contains an unsafe path")
                if len(parts) == 1 and member.isdir():
                    continue
                if len(parts) < 2:
                    raise SourceSnapshotError("snapshot archive root is invalid")
                if archive_root is None:
                    archive_root = parts[0]
                elif parts[0] != archive_root:
                    raise SourceSnapshotError("snapshot archive has multiple roots")
                if member.issym() or member.islnk() or member.isdev():
                    # 链接和设备文件可能在解压时跨越目标目录或访问主机资源。
                    raise SourceSnapshotError("snapshot archive contains an unsafe entry")
                relative = Path(*parts[1:])
                target = temporary / relative
                resolved = target.resolve(strict=False)
                try:
                    resolved.relative_to(temporary.resolve())
                except ValueError as exc:
                    raise SourceSnapshotError("snapshot entry escapes destination") from exc
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.size > _MAX_MEMBER_BYTES:
                    raise SourceSnapshotError("snapshot archive member is invalid")
                total_size += member.size
                if total_size > _MAX_EXPANDED_BYTES:
                    raise SourceSnapshotError("snapshot expanded size exceeds limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise SourceSnapshotError("snapshot member could not be read")
                with target.open("xb") as output:
                    shutil.copyfileobj(extracted, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        os.replace(temporary, destination)
    except (OSError, tarfile.TarError) as exc:
        raise SourceSnapshotError("snapshot archive could not be materialized") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _atomic_write(path: Path, content: bytes) -> None:
    """先落盘并 fsync，再用同目录替换避免留下半写入的 archive。"""

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
        path.chmod(0o600)
    except OSError as exc:
        raise SourceSnapshotError("snapshot archive could not be stored") from exc
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
