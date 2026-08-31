"""提供受 Patch Policy 约束的源码读取和字面量搜索工具。"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import SourceAccessError, SourceRequestError
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.paths import normalize_repository_path, resolve_snapshot_path


class PathScope(StrEnum):
    BACKEND = "backend"
    BACKEND_APP = "backend_app"
    BACKEND_TESTS = "backend_tests"
    PROJECT_DOCS = "project_docs"


class SourceFileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    total_lines: int = Field(ge=0)
    content: str


class SourceSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    line: int = Field(ge=1)
    snippet: str


class SourceReader:
    """只在固定快照和读取白名单内返回有界 UTF-8 文本。"""

    def __init__(self, snapshot_root: Path, policy: PatchPolicy | None = None) -> None:
        self._root = snapshot_root.resolve(strict=True)
        self._policy = policy or PatchPolicy.load_default()

    def read_source_file(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
    ) -> SourceFileResult:
        normalized = normalize_repository_path(path)
        if not self._policy.can_read(normalized):
            raise SourceAccessError(
                "source path is outside the read allowlist",
                safe_details={"reason": "outside_allowlist"},
            )
        if start_line < 1 or end_line < start_line or end_line - start_line > 999:
            raise SourceRequestError(
                "source line range must start at 1, end after start, and contain at most 1000 lines",
                safe_details={
                    "reason": "invalid_line_range",
                    "path": normalized,
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )
        # 即使目标不存在也先走受信解析，以免把断链符号链接误报成可恢复的不存在。
        candidate = resolve_snapshot_path(self._root, normalized, must_exist=False)
        if not candidate.exists():
            raise SourceRequestError(
                "source path does not exist; use search_source to select an existing path",
                safe_details={"reason": "path_not_found", "path": normalized},
            )
        target = resolve_snapshot_path(self._root, normalized, must_exist=True)
        if not target.is_file():
            raise SourceRequestError(
                "source path is not a regular file",
                safe_details={"reason": "not_regular_file", "path": normalized},
            )
        content = _read_limited(
            target,
            self._policy.limits.max_file_bytes,
            safe_path=normalized,
        )
        lines = content.splitlines()
        if start_line > len(lines) and lines:
            raise SourceRequestError(
                "start_line is after the end of the file; choose a smaller line number",
                safe_details={
                    "reason": "line_after_eof",
                    "path": normalized,
                    "start_line": start_line,
                    "total_lines": len(lines),
                },
            )
        selected = lines[start_line - 1 : end_line]
        rendered = "\n".join(selected)
        if len(rendered.encode("utf-8")) > self._policy.limits.max_tool_output_bytes:
            raise SourceRequestError(
                "source output exceeds the tool limit; request a smaller line range",
                safe_details={
                    "reason": "output_limit_exceeded",
                    "path": normalized,
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )
        actual_end = start_line + len(selected) - 1 if selected else start_line
        return SourceFileResult(
            path=normalized,
            start_line=start_line,
            end_line=actual_end,
            total_lines=len(lines),
            content=rendered,
        )

    def list_readable_paths(self) -> tuple[str, ...]:
        """只列出当前固定快照中实际存在且通过读取 Policy 的文件。"""

        return tuple(
            sorted(
                set(self._candidate_paths(PathScope.BACKEND))
                | set(self._candidate_paths(PathScope.PROJECT_DOCS))
            )
        )

    def search_source(
        self,
        query: str,
        *,
        path_scope: PathScope,
        max_results: int,
    ) -> tuple[SourceSearchResult, ...]:
        if not query or len(query) > 100 or "\x00" in query or "\n" in query:
            raise SourceRequestError(
                "search query must contain 1..100 characters on one line",
                safe_details={"reason": "invalid_search_query"},
            )
        if max_results < 1 or max_results > 20:
            raise SourceRequestError(
                "max_results must be between 1 and 20",
                safe_details={
                    "reason": "invalid_result_limit",
                    "max_results": max_results,
                },
            )

        results: list[SourceSearchResult] = []
        output_bytes = 0
        # 使用字面量包含匹配，不把模型输入解释为正则表达式或 Shell 模式。
        for relative in self._candidate_paths(path_scope):
            target = resolve_snapshot_path(self._root, relative, must_exist=True)
            if not target.is_file():
                continue
            content = _read_limited(
                target,
                self._policy.limits.max_file_bytes,
                safe_path=relative,
            )
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query not in line:
                    continue
                snippet = line.strip()[:240]
                item = SourceSearchResult(
                    path=relative,
                    line=line_number,
                    snippet=snippet,
                )
                item_size = len(item.model_dump_json().encode("utf-8"))
                # 在序列化后计算大小，确保真实工具响应不会超过上下文预算。
                if output_bytes + item_size > self._policy.limits.max_tool_output_bytes:
                    return tuple(results)
                results.append(item)
                output_bytes += item_size
                if len(results) >= max_results:
                    return tuple(results)
        return tuple(results)

    def _candidate_paths(self, scope: PathScope) -> tuple[str, ...]:
        candidates: set[str] = set()
        for exact in self._policy.read_exact:
            if _in_scope(exact, scope) and (self._root / exact).is_file():
                candidates.add(exact)
        tests_root = self._root / "backend/tests"
        if scope in {PathScope.BACKEND, PathScope.BACKEND_TESTS} and tests_root.is_dir():
            for candidate in tests_root.rglob("*.py"):
                relative = candidate.relative_to(self._root).as_posix()
                if self._policy.can_read(relative):
                    candidates.add(relative)
        return tuple(sorted(candidates))


def _in_scope(path: str, scope: PathScope) -> bool:
    if scope is PathScope.BACKEND:
        return path.startswith("backend/")
    if scope is PathScope.BACKEND_APP:
        return path.startswith("backend/app/")
    if scope is PathScope.BACKEND_TESTS:
        return path.startswith("backend/tests/")
    return path in {"AGENTS.md", "README.md"}


def _read_limited(path: Path, max_bytes: int, *, safe_path: str) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceRequestError(
            "source file could not be read; select another search result",
            safe_details={"reason": "source_unreadable", "path": safe_path},
        ) from exc
    if len(raw) > max_bytes or b"\x00" in raw:
        raise SourceRequestError(
            "source file is binary or exceeds the readable file limit",
            safe_details={"reason": "file_limit_exceeded", "path": safe_path},
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceRequestError(
            "source file is not UTF-8 text",
            safe_details={"reason": "invalid_text_encoding", "path": safe_path},
        ) from exc
