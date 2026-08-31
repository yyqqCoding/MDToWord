"""加载机器可读 Patch Policy，并统一执行源码读写授权。"""

import fnmatch
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import PatchPolicyError, SourceAccessError
from agent.workspace.paths import normalize_repository_path


class _ReadRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact: tuple[str, ...]
    patterns: tuple[str, ...]


class _WriteRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_exact: tuple[str, ...]
    test_prefixes: tuple[str, ...]
    fix_exact: tuple[str, ...]
    full_file_exact: tuple[str, ...]
    full_file_prefixes: tuple[str, ...]


class PatchLimits(BaseModel):
    """限制模型工具输出和最终补丁规模，防止无界资源消耗。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(gt=0)
    max_tool_output_bytes: int = Field(gt=0)
    max_changed_files: int = Field(gt=0)
    max_added_lines: int = Field(ge=0)
    max_deleted_lines: int = Field(ge=0)
    max_patch_bytes: int = Field(gt=0)


class _PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    read: _ReadRules
    write: _WriteRules
    limits: PatchLimits


class PatchPolicy:
    """加载安全文档的机器可读镜像，并集中执行读写路径授权。"""

    def __init__(self, document: _PolicyDocument) -> None:
        self.version = document.version
        self.read_exact = frozenset(document.read.exact)
        self.read_patterns = tuple(document.read.patterns)
        self._test_exact = frozenset(document.write.test_exact)
        self._test_prefixes = tuple(document.write.test_prefixes)
        self._fix_exact = frozenset(document.write.fix_exact)
        self._full_file_exact = frozenset(document.write.full_file_exact)
        self._full_file_prefixes = tuple(document.write.full_file_prefixes)
        self.limits = document.limits

    @classmethod
    def load_default(cls) -> "PatchPolicy":
        path = Path(__file__).resolve().parents[1] / "policies" / "patch_policy.json"
        try:
            document = _PolicyDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PatchPolicyError("patch policy configuration is invalid") from exc
        return cls(document)

    def can_read(self, path: str) -> bool:
        # 路径先规范化再匹配，避免 allowlist 被不同分隔符或 .. 绕过。
        normalized = normalize_repository_path(path)
        return normalized in self.read_exact or any(
            fnmatch.fnmatchcase(normalized, pattern)
            for pattern in self.read_patterns
        )

    def authorize_write(self, path: str, phase: str) -> str:
        # 读写授权统一收口在这里；工具层只能传入候选路径，不能自行扩大白名单。
        try:
            normalized = normalize_repository_path(path)
        except SourceAccessError as exc:
            raise PatchPolicyError("edit path is not a safe repository path") from exc
        if phase == "test":
            allowed = normalized in self._test_exact or _has_prefix(
                normalized,
                self._test_prefixes,
            )
        elif phase == "fix":
            allowed = normalized in self._fix_exact
        else:
            raise PatchPolicyError("edit phase is not registered")
        if not allowed:
            raise PatchPolicyError("edit path is not allowed for this phase")
        # fixture 目录只允许可审查的文本格式，拒绝脚本和二进制载荷。
        if _has_prefix(normalized, self._test_prefixes) and not normalized.endswith(
            (".json", ".md", ".txt")
        ):
            raise PatchPolicyError("feedback fixture type is not allowed")
        return normalized

    def authorize_full_file(self, path: str) -> None:
        if path not in self._full_file_exact and not _has_prefix(
            path,
            self._full_file_prefixes,
        ):
            raise PatchPolicyError("full-file replacement is not allowed for this path")


def _has_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in prefixes)
