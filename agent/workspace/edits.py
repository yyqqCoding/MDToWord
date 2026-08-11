"""把模型提交的结构化编辑转换为经过安全校验的确定性 Git patch。"""

import ast
from collections import Counter
import hashlib
import os
import shutil
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.domain.errors import InvalidEditError, PatchPolicyError, SourceAccessError
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.paths import resolve_snapshot_path


class EditMode(StrEnum):
    SEARCH_REPLACE = "search_replace"
    FULL_FILE = "full_file"


class EditPhase(StrEnum):
    TEST = "test"
    FIX = "fix"


class Edit(BaseModel):
    """模型唯一可提交的编辑格式，不接受命令、环境变量或任意工具参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=240)
    mode: EditMode
    search: str | None = None
    replace: str | None = None
    content: str | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "Edit":
        if self.mode is EditMode.SEARCH_REPLACE:
            if not self.search or self.replace is None or self.content is not None:
                raise ValueError("search_replace requires search and replace only")
        elif self.content is None or self.search is not None or self.replace is not None:
            raise ValueError("full_file requires content only")
        for value in (self.search, self.replace, self.content):
            if value is not None and "\x00" in value:
                raise ValueError("edit text must not contain NUL")
        return self


class PatchArtifact(BaseModel):
    """供 Sandbox 使用的不可变补丁及其审计摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_files: tuple[str, ...]
    added_lines: int = Field(ge=0)
    deleted_lines: int = Field(ge=0)


class PatchBuilder:
    """在隔离副本中应用编辑并执行路径、语法、能力和规模校验。"""

    def __init__(self, policy: PatchPolicy) -> None:
        self._policy = policy

    def build(
        self,
        snapshot_root: Path,
        edits: tuple[Edit, ...],
        phase: EditPhase,
    ) -> PatchArtifact:
        if not edits:
            raise InvalidEditError("at least one edit is required")
        paths = [self._policy.authorize_write(edit.path, phase.value) for edit in edits]
        if len(set(paths)) != len(paths):
            raise InvalidEditError("multiple edits for the same file are not allowed")

        root = snapshot_root.resolve(strict=True)
        _reject_symlinks(root)
        with tempfile.TemporaryDirectory(prefix="mdtoword-patch-") as temporary:
            # 在临时副本中建立固定 Git 基线，绝不直接修改下载的源码快照。
            worktree = Path(temporary) / "worktree"
            shutil.copytree(root, worktree)
            git_environment = _git_environment(Path(temporary))
            _run_git(worktree, git_environment, "init", "-q", "--template=")
            _run_git(worktree, git_environment, "add", "--all")
            _run_git(
                worktree,
                git_environment,
                "-c",
                "user.name=MD To Word Agent",
                "-c",
                "user.email=agent.invalid@example.invalid",
                "commit",
                "-q",
                "-m",
                "baseline",
            )

            for edit, normalized in zip(edits, paths, strict=True):
                self._apply_edit(worktree, normalized, edit, phase)
            _validate_python_policy(root, worktree, paths)
            # intent-to-add 让新增文件也进入普通文本 diff，但不会生成提交。
            _run_git(worktree, git_environment, "add", "--intent-to-add", "--all")
            check = _run_git(
                worktree,
                git_environment,
                "diff",
                "--check",
                check=False,
            )
            if check.returncode != 0:
                raise PatchPolicyError("patch fails git diff --check")
            patch = _run_git(
                worktree,
                git_environment,
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-renames",
                "--",
            ).stdout
            if not patch:
                raise InvalidEditError("edits do not change the snapshot")
            return self._validate_patch(worktree, git_environment, patch, phase)

    def _apply_edit(
        self,
        root: Path,
        path: str,
        edit: Edit,
        phase: EditPhase,
    ) -> None:
        target = resolve_snapshot_path(
            root,
            path,
            must_exist=edit.mode is EditMode.SEARCH_REPLACE,
        )
        target_existed = target.exists()
        original = ""
        if target_existed:
            try:
                original = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise InvalidEditError("edit target is not readable UTF-8 text") from exc
        if edit.mode is EditMode.FULL_FILE:
            self._policy.authorize_full_file(path)
            assert edit.content is not None
            content = edit.content
        else:
            if not target.is_file():
                raise InvalidEditError("search_replace target is not a regular file")
            assert edit.search is not None
            assert edit.replace is not None
            if original.count(edit.search) != 1:
                raise InvalidEditError("search text must match exactly once")
            content = original.replace(edit.search, edit.replace, 1)
        if phase is EditPhase.TEST:
            if path == "backend/tests/test_feedback_regressions.py":
                # 回归测试文件只允许尾部追加，避免模型改写或弱化既有断言。
                if not content.startswith(original):
                    raise PatchPolicyError("test edits must preserve existing regressions")
            elif target_existed:
                raise PatchPolicyError("feedback fixture already exists")
        encoded = content.encode("utf-8")
        if len(encoded) > self._policy.limits.max_file_bytes:
            raise PatchPolicyError("edited file exceeds size limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)

    def _validate_patch(
        self,
        worktree: Path,
        environment: dict[str, str],
        patch: bytes,
        phase: EditPhase,
    ) -> PatchArtifact:
        limits = self._policy.limits
        if len(patch) > limits.max_patch_bytes or b"GIT binary patch" in patch:
            raise PatchPolicyError("patch is binary or exceeds size limit")
        name_status = _run_git(
            worktree,
            environment,
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-status",
            "--no-renames",
            "--",
        ).stdout.decode("utf-8")
        changed_files: list[str] = []
        for line in name_status.splitlines():
            status, separator, path = line.partition("\t")
            if separator != "\t" or status not in {"A", "M"}:
                raise PatchPolicyError("patch contains an unsupported file operation")
            changed_files.append(self._policy.authorize_write(path, phase.value))
        if not changed_files or len(changed_files) > limits.max_changed_files:
            raise PatchPolicyError("patch changed file count exceeds limit")

        numstat = _run_git(
            worktree,
            environment,
            "-c",
            "core.quotePath=false",
            "diff",
            "--numstat",
            "--no-renames",
            "--",
        ).stdout.decode("utf-8")
        added = 0
        deleted = 0
        for line in numstat.splitlines():
            added_text, deleted_text, _ = line.split("\t", 2)
            if added_text == "-" or deleted_text == "-":
                raise PatchPolicyError("binary patches are not allowed")
            added += int(added_text)
            deleted += int(deleted_text)
        if added > limits.max_added_lines or deleted > limits.max_deleted_lines:
            raise PatchPolicyError("patch line count exceeds limit")
        summary = _run_git(worktree, environment, "diff", "--summary", "--").stdout
        if b"mode change" in summary or b"rename " in summary or b"delete mode" in summary:
            raise PatchPolicyError("patch contains a forbidden metadata change")
        return PatchArtifact(
            content=patch,
            sha256=hashlib.sha256(patch).hexdigest(),
            changed_files=tuple(sorted(changed_files)),
            added_lines=added,
            deleted_lines=deleted,
        )


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PatchPolicyError("snapshot contains a symbolic link")


_BLOCKED_IMPORTS = frozenset(
    {
        "ctypes",
        "ftplib",
        "httpx",
        "importlib",
        "multiprocessing",
        "os",
        "paramiko",
        "requests",
        "socket",
        "subprocess",
        "telnetlib",
        "urllib",
    }
)
_BLOCKED_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "open",
        "os.getenv",
        "os.popen",
        "os.system",
    }
)
_BLOCKED_CALL_PREFIXES = (
    "httpx.",
    "requests.",
    "socket.",
    "subprocess.",
    "urllib.",
)


def _validate_python_policy(baseline: Path, worktree: Path, paths: list[str]) -> None:
    """只拒绝编辑新增的危险能力，兼容基线中已有的受信实现。"""

    for relative in paths:
        if not relative.endswith(".py"):
            continue
        baseline_path = baseline / relative
        original = baseline_path.read_text(encoding="utf-8") if baseline_path.exists() else ""
        modified = (worktree / relative).read_text(encoding="utf-8")
        original_findings = _python_security_findings(original)
        modified_findings = _python_security_findings(modified)
        if modified_findings - original_findings:
            raise PatchPolicyError("edit introduces a forbidden Python capability")


def _python_security_findings(source: str) -> Counter[str]:
    if not source:
        return Counter()
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise InvalidEditError("edited Python must parse successfully") from exc
    findings: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                root_name = name.split(".", 1)[0]
                if root_name in _BLOCKED_IMPORTS:
                    findings[f"import:{root_name}"] += 1
        elif isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            if name in _BLOCKED_CALLS or name.startswith(_BLOCKED_CALL_PREFIXES):
                findings[f"call:{name}"] += 1
        elif isinstance(node, ast.Attribute) and _qualified_name(node) == "os.environ":
            findings["attribute:os.environ"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("pytest_"):
                findings[f"hook:{node.name}"] += 1
    return findings


def _qualified_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _git_environment(home: Path) -> dict[str, str]:
    # 屏蔽用户/系统 Git 配置并固定时间，保证相同编辑生成相同补丁。
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def _run_git(
    cwd: Path,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PatchPolicyError("trusted git operation failed") from exc
    if check and result.returncode != 0:
        raise PatchPolicyError("trusted git operation failed")
    return result
