"""统一校验并解析不可信的仓库相对路径。"""

from pathlib import Path, PurePosixPath

from agent.domain.errors import SourceAccessError


def normalize_repository_path(raw_path: str) -> str:
    """只接受规范的 POSIX 仓库相对路径，不对危险输入做自动修复。"""

    if not raw_path or "\x00" in raw_path or "\\" in raw_path:
        raise SourceAccessError(
            "repository path is invalid",
            safe_details={"reason": "unsafe_path"},
        )
    if raw_path.startswith("/") or ":" in raw_path:
        raise SourceAccessError(
            "repository path must be relative",
            safe_details={"reason": "unsafe_path"},
        )
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise SourceAccessError(
            "repository path is not normalized",
            safe_details={"reason": "unsafe_path"},
        )
    path = PurePosixPath(raw_path)
    if path.is_absolute():
        raise SourceAccessError(
            "repository path must be relative",
            safe_details={"reason": "unsafe_path"},
        )
    return path.as_posix()


def resolve_snapshot_path(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool,
) -> Path:
    normalized = normalize_repository_path(relative_path)
    resolved_root = root.resolve(strict=True)
    candidate = resolved_root.joinpath(*normalized.split("/"))

    # 逐段检查现有路径，避免最终 resolve 掩盖中间符号链接。
    current = resolved_root
    for segment in normalized.split("/"):
        current = current / segment
        if current.is_symlink():
            raise SourceAccessError(
                "symbolic links are not allowed",
                safe_details={"reason": "symlink_rejected"},
            )
        if not current.exists():
            break

    if must_exist:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SourceAccessError(
                "source path does not exist",
                safe_details={"reason": "path_not_found"},
            ) from exc
    else:
        resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise SourceAccessError(
            "source path escapes snapshot root",
            safe_details={"reason": "unsafe_path"},
        ) from exc
    return resolved
