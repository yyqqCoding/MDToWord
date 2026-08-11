"""在固定源码快照上组合授权补丁，产出最终可发布 Artifact。"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import PatchPolicyError


class ValidatedPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_files: tuple[str, ...]


def compose_validated_patch(
    snapshot_root: Path,
    test_patch: bytes,
    fix_patch: bytes,
) -> ValidatedPatch:
    """验证两类补丁文件互斥，并从原始基线生成唯一组合 diff。"""

    test_files = _declared_patch_files(snapshot_root, "test.patch", test_patch)
    fix_files = _declared_patch_files(snapshot_root, "fix.patch", fix_patch)
    if set(test_files).intersection(fix_files):
        raise PatchPolicyError("test and fix patches must change disjoint files")
    return _materialize_patch_set(
        snapshot_root,
        (("test.patch", test_patch), ("fix.patch", fix_patch)),
    )


def normalize_authorized_patch(
    snapshot_root: Path,
    patch: bytes,
) -> ValidatedPatch:
    """把单个授权 patch 规范化为 Worker workspace 使用的确定性 diff。"""

    return _materialize_patch_set(snapshot_root, (("authorized.patch", patch),))


def _declared_patch_files(
    snapshot_root: Path,
    name: str,
    patch: bytes,
) -> tuple[str, ...]:
    root = snapshot_root.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="mdtoword-patch-files-") as temporary:
        worktree = Path(temporary) / "worktree"
        environment = _prepare_worktree(root, worktree, Path(temporary))
        patch_path = Path(temporary) / name
        patch_path.write_bytes(patch)
        return _patch_files(worktree, environment, patch_path)


def _materialize_patch_set(
    snapshot_root: Path,
    patches: tuple[tuple[str, bytes], ...],
) -> ValidatedPatch:
    root = snapshot_root.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="mdtoword-validated-") as temporary:
        temporary_root = Path(temporary)
        worktree = temporary_root / "worktree"
        environment = _prepare_worktree(root, worktree, temporary_root)

        changed_files: list[str] = []
        for name, content in patches:
            patch_path = temporary_root / name
            patch_path.write_bytes(content)
            changed_files.extend(_patch_files(worktree, environment, patch_path))
            _git(worktree, environment, "apply", "--check", str(patch_path))
            _git(worktree, environment, "apply", str(patch_path))

        _git(worktree, environment, "add", "--intent-to-add", "--all")
        check = _git(worktree, environment, "diff", "--check", check=False)
        if check.returncode != 0:
            raise PatchPolicyError("validated patch fails git diff --check")
        content = _git(
            worktree,
            environment,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-renames",
            "--",
        ).stdout
        if not content:
            raise PatchPolicyError("validated patch is empty")
        return ValidatedPatch(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            changed_files=tuple(sorted(changed_files)),
        )


def _prepare_worktree(
    root: Path,
    worktree: Path,
    task_root: Path,
) -> dict[str, str]:
    environment = _git_environment(task_root)
    shutil.copytree(root, worktree)
    # 临时仓库把固定源码快照记录为唯一基线，使最终 diff 不依赖宿主仓库的历史和配置。
    _git(worktree, environment, "init", "-q", "--template=")
    _git(worktree, environment, "add", "--all")
    _git(
        worktree,
        environment,
        "-c",
        "user.name=MD To Word Agent",
        "-c",
        "user.email=agent.invalid@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    return environment


def _patch_files(
    worktree: Path,
    environment: dict[str, str],
    patch_path: Path,
) -> tuple[str, ...]:
    result = _git(
        worktree,
        environment,
        "apply",
        "--numstat",
        str(patch_path),
    )
    files: list[str] = []
    for line in result.stdout.decode("utf-8").splitlines():
        _, _, path = line.split("\t", 2)
        files.append(path)
    if not files:
        raise PatchPolicyError("authorized patch does not change files")
    return tuple(sorted(files))


def _git_environment(task_root: Path) -> dict[str, str]:
    # 屏蔽用户级 Git 配置与交互式认证，防止沙箱外环境改变补丁计算结果。
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(task_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(
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
