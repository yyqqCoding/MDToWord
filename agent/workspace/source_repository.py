"""从固定 GitHub commit 获取并安全展开只读源码快照。"""

import asyncio
import hashlib
import math
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import (
    AgentError,
    RepositoryUnavailableError,
    SourceAuthenticationError,
    SourceSnapshotError,
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
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_EXPANDED_BYTES = 100 * 1024 * 1024
_MAX_MEMBER_BYTES = 10 * 1024 * 1024
_Sleep = Callable[[float], Awaitable[None]]


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
        max_transport_retries: int = 2,
        sleep: _Sleep = asyncio.sleep,
        retry_policy: RetryPolicy | None = None,
        failure_recorder: FailureRecorder | None = None,
    ) -> None:
        if not _GITHUB_REPOSITORY.fullmatch(repository):
            raise SourceSnapshotError("GITHUB_REPOSITORY must use owner/name format")
        if max_transport_retries not in {0, 1, 2}:
            raise ValueError("max_transport_retries must be between 0 and 2")
        self._repository = repository
        self._client = client
        self._api_url = api_url.rstrip("/")
        self._max_transport_retries = max_transport_retries
        self._sleep = sleep
        self._retry_policy = retry_policy or RetryPolicy()
        self._failure_recorder = failure_recorder or FailureRecorder()

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

        content = await self._download_archive(base_sha)

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

    async def _download_archive(self, base_sha: str) -> bytes:
        max_attempts = self._max_transport_retries + 1
        for attempt in range(1, max_attempts + 1):
            response: httpx.Response | None = None
            try:
                async with self._client.stream(
                    "GET",
                    f"{self._api_url}/repos/{self._repository}/tarball/{base_sha}",
                    # GitHub archive API 正常以 302 跳转到短期下载地址。
                    follow_redirects=True,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                ) as response:
                    if response.status_code >= 400:
                        error: AgentError = _snapshot_http_error(response)
                    else:
                        chunks: list[bytes] = []
                        archive_size = 0
                        # 不信任 Content-Length；流式累计才能覆盖缺失或伪造长度的响应。
                        async for chunk in response.aiter_bytes():
                            archive_size += len(chunk)
                            if archive_size > _MAX_ARCHIVE_BYTES:
                                raise SourceSnapshotError(
                                    "GitHub snapshot archive size is invalid",
                                    safe_details={"reason": "archive_too_large"},
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if content:
                            return content
                        error = SourceSnapshotError(
                            "GitHub snapshot archive size is invalid",
                            safe_details={"reason": "archive_empty"},
                        )
            except SourceSnapshotError as exc:
                error = exc
            except httpx.HTTPError as exc:
                error = RepositoryUnavailableError(
                    "GitHub snapshot transport failed",
                    safe_details={"error_type": type(exc).__name__[:120]},
                )

            error.attempt = attempt
            error.max_attempts = max_attempts
            error.operation = "fetch_source_snapshot"
            delay = retry_delay(attempt, _retry_after_seconds(response))
            cause = failure_cause_from_exception(error)
            decision = self._retry_policy.decide(
                cause,
                RetryContext(
                    attempt=attempt,
                    max_attempts=max_attempts,
                    operation_id=f"{self._repository}:{base_sha}",
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

        raise AssertionError("GitHub snapshot retry loop must return or raise")

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
                    node="fetch_source_snapshot",
                ),
                attempt=error.attempt,
                max_attempts=error.max_attempts,
                handling=handling,
                delay_seconds=delay_seconds,
            )
        )


def _snapshot_http_error(response: httpx.Response) -> AgentError:
    status = response.status_code
    details: dict[str, str | int | bool | None] = {"http_status": status}
    if _is_rate_limited(response):
        details["rate_limited"] = True
        return RepositoryUnavailableError(
            "GitHub snapshot request was rate limited",
            safe_details=details,
        )
    if status == 408 or status >= 500:
        return RepositoryUnavailableError(
            f"GitHub snapshot request failed with status {status}",
            safe_details=details,
        )
    if status in {401, 403}:
        return SourceAuthenticationError(
            "GitHub source authentication failed",
            safe_details=details,
        )
    details["reason"] = "snapshot_request_rejected"
    return SourceSnapshotError(
        f"GitHub snapshot request failed with status {status}",
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
                    _make_snapshot_directory(target, temporary)
                    continue
                if not member.isfile() or member.size > _MAX_MEMBER_BYTES:
                    raise SourceSnapshotError("snapshot archive member is invalid")
                total_size += member.size
                if total_size > _MAX_EXPANDED_BYTES:
                    raise SourceSnapshotError("snapshot expanded size exceeds limit")
                _make_snapshot_directory(target.parent, temporary)
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


def _make_snapshot_directory(path: Path, root: Path) -> None:
    """创建容器可遍历的目录，不继承 Worker 的严格 umask。"""

    path.mkdir(parents=True, exist_ok=True)
    current = path
    while current != root:
        current.chmod(0o755)
        current = current.parent


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
