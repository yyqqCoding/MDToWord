"""把受信 Sandbox Job 映射为固定、无 Shell 的受限 Docker 执行。"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent.domain.errors import SandboxExecutionError
from agent.sandbox.contracts import (
    JobType,
    ResourceSummary,
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)
from agent.telemetry.masking import mask_text
from agent.validators.junit import parse_junit_summary
from agent.workspace.source_repository import materialize_snapshot_archive


_IMAGE_DIGEST = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|"
    r"[A-Za-z0-9._/-]+(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64})$"
)


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class DockerRunner:
    """在一次性 workspace 中应用已授权补丁，并以固定约束运行容器。"""

    def __init__(
        self,
        *,
        image_digest: str,
        work_root: Path,
        docker_binary: str = "docker",
        execute_command: Callable[[tuple[str, ...], int], CommandOutcome] | None = None,
    ) -> None:
        if not _IMAGE_DIGEST.fullmatch(image_digest):
            raise SandboxExecutionError("SANDBOX_IMAGE_DIGEST must pin sha256")
        self._image_digest = image_digest
        self._work_root = work_root.resolve()
        self._work_root.mkdir(parents=True, exist_ok=True)
        self._docker_binary = docker_binary
        self._execute_command = execute_command or self._execute_docker

    def execute(self, job: SandboxJob, artifacts: SandboxArtifacts) -> SandboxResult:
        started_at = datetime.now(UTC)
        started_clock = time.monotonic()
        with tempfile.TemporaryDirectory(
            prefix=f"job-{job.job_id}-",
            dir=self._work_root,
        ) as temporary:
            job_root = Path(temporary)
            archive = job_root / "source.tar.gz"
            archive.write_bytes(artifacts.source_archive)
            workspace = job_root / "workspace"
            result_root = job_root / "result"
            result_root.mkdir()
            materialize_snapshot_archive(archive, workspace)
            workspace.chmod(0o777)
            result_root.chmod(0o777)

            git_environment = _git_environment(job_root, workspace)
            _initialize_baseline(job_root, git_environment)
            for name, content in (
                ("test.patch", artifacts.test_patch),
                ("fix.patch", artifacts.fix_patch),
            ):
                if content is None:
                    continue
                patch_path = job_root / name
                patch_path.write_bytes(content)
                _apply_patch(workspace, patch_path, git_environment)
            authorized_diff = _workspace_diff(workspace, git_environment)
            authorized_hash = hashlib.sha256(authorized_diff).hexdigest()

            argv = self._docker_argv(job, workspace, result_root)
            outcome = self._execute_command(argv, job.limits.wall_timeout_seconds)
            # 容器结束后重新计算 diff，检测测试代码越权修改挂载的 workspace。
            final_diff = _workspace_diff(workspace, git_environment)
            finished_at = datetime.now(UTC)
            duration_ms = int((time.monotonic() - started_clock) * 1000)
            common = {
                "job_id": job.job_id,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "stdout_tail": _clean_output(outcome.stdout, job.limits.stdout_bytes),
                "stderr_tail": _clean_output(outcome.stderr, job.limits.stderr_bytes),
                "workspace_diff_sha256": authorized_hash,
                "resource_summary": ResourceSummary(
                    memory_limit_bytes=job.limits.memory_bytes,
                    cpu_limit=job.limits.cpus,
                    pids_limit=job.limits.pids,
                ),
            }
            junit_path = _junit_path(job, result_root)
            if junit_path is not None and junit_path.is_file():
                try:
                    common["junit_summary"] = parse_junit_summary(
                        junit_path,
                        job.target_test_selector,
                    )
                except ValueError:
                    # XML 无效由 Controller 分类为 invalid_test，不能退回解析 stdout。
                    common["junit_summary"] = None
            if final_diff != authorized_diff:
                return SandboxResult(
                    **common,
                    status=SandboxStatus.SECURITY_REJECTED,
                    error_code="workspace_modified",
                )
            if outcome.timed_out:
                return SandboxResult(
                    **common,
                    status=SandboxStatus.TIMED_OUT,
                    error_code="sandbox_timeout",
                )
            return SandboxResult(**common, status=SandboxStatus.COMPLETED)

    def _docker_argv(
        self,
        job: SandboxJob,
        workspace: Path,
        result_root: Path,
    ) -> tuple[str, ...]:
        container_name = f"mdtoword-{job.job_id.hex}"
        command = _job_command(job)
        # 参数均由本地枚举和上限生成；不接受模型提供的命令、挂载或环境变量。
        return (
            self._docker_binary,
            "run",
            "--rm",
            f"--name={container_name}",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--memory={job.limits.memory_bytes}",
            f"--cpus={job.limits.cpus}",
            f"--pids-limit={job.limits.pids}",
            "--user=65532:65532",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=536870912",
            f"--volume={workspace}:/workspace:rw",
            f"--volume={result_root}:/result:rw",
            "--workdir=/workspace/backend",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTHONPATH=/opt/trusted",
            "--env=HOME=/tmp",
            "--entrypoint=python",
            self._image_digest,
            *command,
        )

    def _execute_docker(
        self,
        argv: tuple[str, ...],
        timeout_seconds: int,
    ) -> CommandOutcome:
        if shutil.which(self._docker_binary) is None:
            raise SandboxExecutionError("docker executable is unavailable")
        stdout = bytearray()
        stderr = bytearray()
        try:
            # 仅传入 PATH/LANG，防止主机业务 Secret 和代理变量继承到 Docker CLI。
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
            )
            assert process.stdout is not None
            assert process.stderr is not None
            readers = (
                threading.Thread(target=_drain_tail, args=(process.stdout, stdout), daemon=True),
                threading.Thread(target=_drain_tail, args=(process.stderr, stderr), daemon=True),
            )
            for reader in readers:
                reader.start()
            try:
                exit_code = process.wait(timeout=timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
                process.kill()
                process.wait(timeout=30)
                self._remove_timed_out_container(argv)
            for reader in readers:
                reader.join(timeout=30)
            return CommandOutcome(exit_code, bytes(stdout), bytes(stderr), timed_out)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxExecutionError("docker execution failed") from exc

    def _remove_timed_out_container(self, argv: tuple[str, ...]) -> None:
        try:
            container_name = next(
                item.removeprefix("--name=")
                for item in argv
                if item.startswith("--name=")
            )
            subprocess.run(
                (self._docker_binary, "rm", "-f", container_name),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.TimeoutExpired, StopIteration):
            # 原始 Job 仍按超时终结；清理失败由主机级容器监控继续处理。
            return


def _job_command(job: SandboxJob) -> tuple[str, ...]:
    if job.job_type in {JobType.REPRODUCE_TARGET, JobType.VALIDATE_TARGET}:
        assert job.target_test_selector is not None
        return (
            "-m",
            "pytest",
            "tests/test_feedback_regressions.py",
            "-k",
            job.target_test_selector,
            "-q",
            "-p",
            "no:cacheprovider",
            "--junitxml=/result/junit.xml",
        )
    if job.job_type is JobType.VALIDATE_FULL:
        return (
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--junitxml=/result/full-junit.xml",
        )
    return ("-m", "compileall", "-q", "app", "tests")


def _junit_path(job: SandboxJob, result_root: Path) -> Path | None:
    if job.job_type in {JobType.REPRODUCE_TARGET, JobType.VALIDATE_TARGET}:
        return result_root / "junit.xml"
    if job.job_type is JobType.VALIDATE_FULL:
        return result_root / "full-junit.xml"
    return None


def _initialize_baseline(job_root: Path, environment: dict[str, str]) -> None:
    # Git 元数据放在容器未挂载的 job_root，任务代码看不到或篡改基线。
    git_directory = Path(environment["GIT_DIR"])
    initialization_environment = {
        key: value
        for key, value in environment.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE"}
    }
    _git(
        job_root,
        initialization_environment,
        "init",
        "-q",
        "--bare",
        "--template=",
        str(git_directory),
    )
    _git(job_root, environment, "add", "--all")
    _git(
        job_root,
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


def _apply_patch(root: Path, patch: Path, environment: dict[str, str]) -> None:
    _git(root, environment, "apply", "--check", str(patch))
    _git(root, environment, "apply", str(patch))


def _workspace_diff(root: Path, environment: dict[str, str]) -> bytes:
    _git(root, environment, "add", "--intent-to-add", "--all")
    return _git(
        root,
        environment,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-renames",
        "--",
    ).stdout


def _git_environment(home: Path, worktree: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_DIR": str(home / "baseline.git"),
        "GIT_WORK_TREE": str(worktree),
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def _git(
    root: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxExecutionError("trusted git operation failed") from exc
    if result.returncode != 0:
        raise SandboxExecutionError("trusted git operation failed")
    return result


def _clean_output(content: bytes, limit: int) -> str:
    text = content.decode("utf-8", errors="replace")
    text = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    encoded = text.encode("utf-8")[-limit:]
    while True:
        try:
            tail = encoded.decode("utf-8")
            break
        except UnicodeDecodeError:
            encoded = encoded[1:]
    masked = mask_text(tail, max_length=limit)
    masked_bytes = masked.encode("utf-8")[-limit:]
    while True:
        try:
            return masked_bytes.decode("utf-8")
        except UnicodeDecodeError:
            masked_bytes = masked_bytes[1:]


def _drain_tail(stream: object, destination: bytearray, limit: int = 4096) -> None:
    # 持续排空管道避免子进程阻塞，同时只保留末尾固定字节。
    read = getattr(stream, "read")
    while True:
        chunk = read(8192)
        if not chunk:
            return
        destination.extend(chunk)
        if len(destination) > limit:
            del destination[:-limit]
