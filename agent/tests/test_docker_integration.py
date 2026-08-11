import difflib
import hashlib
import io
import os
import shutil
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxLimits,
    SandboxStatus,
)
from agent.sandbox.docker_runner import DockerRunner
from agent.sandbox.worker import FileJobStore, SandboxWorker


pytestmark = pytest.mark.docker


def test_real_docker_worker_has_no_network_or_business_secrets_and_is_idempotent(
    tmp_path: Path,
):
    image_digest = os.environ.get("SANDBOX_IMAGE_DIGEST", "")
    if not image_digest or shutil.which("docker") is None:
        pytest.skip("SANDBOX_IMAGE_DIGEST and Docker are required")

    snapshot = tmp_path / "snapshot"
    (snapshot / "backend/tests").mkdir(parents=True)
    target = snapshot / "backend/tests/test_feedback_regressions.py"
    target.write_text("# Stage C baseline\n", encoding="utf-8")
    test_source = '''
def test_feedback_stagec_isolation():
    import os
    import socket
    from pathlib import Path

    forbidden = {
        "SUPABASE_AGENT_KEY",
        "MODEL_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "GITHUB_APP_PRIVATE_KEY",
        "SANDBOX_WORKER_CREDENTIAL",
    }
    assert forbidden.isdisjoint(os.environ)
    assert os.geteuid() != 0
    assert not Path("/var/run/docker.sock").exists()

    process_status = Path("/proc/self/status").read_text(encoding="utf-8")
    assert "CapEff:\t0000000000000000" in process_status
    assert "NoNewPrivs:\t1" in process_status
    root_mount = next(
        line for line in Path("/proc/mounts").read_text().splitlines()
        if line.split()[1] == "/"
    )
    assert "ro" in root_mount.split()[3].split(",")
    try:
        Path("/stage-c-write-probe").write_text("forbidden", encoding="utf-8")
    except OSError:
        pass
    else:
        raise AssertionError("container root filesystem must be read-only")

    cgroup = Path("/sys/fs/cgroup")
    if (cgroup / "memory.max").exists():
        assert (cgroup / "memory.max").read_text().strip() == "2147483648"
        assert (cgroup / "pids.max").read_text().strip() == "256"
        quota, period = (cgroup / "cpu.max").read_text().split()
        assert quota != "max"
        assert abs(int(quota) / int(period) - 2.0) < 0.01
    else:
        assert (cgroup / "memory/memory.limit_in_bytes").read_text().strip() == "2147483648"
        assert (cgroup / "pids/pids.max").read_text().strip() == "256"
        quota = int((cgroup / "cpu/cpu.cfs_quota_us").read_text())
        period = int((cgroup / "cpu/cpu.cfs_period_us").read_text())
        assert abs(quota / period - 2.0) < 0.01

    connection = socket.socket()
    connection.settimeout(0.5)
    try:
        connection.connect(("1.1.1.1", 53))
    except OSError:
        pass
    else:
        raise AssertionError("sandbox network must be disabled")
    finally:
        connection.close()


def test_feedback_stagec_timeout():
    import time

    time.sleep(10)
'''.lstrip()
    patch = _trusted_smoke_patch(target.read_text(encoding="utf-8"), test_source)
    source_archive = _archive_snapshot(snapshot)
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="a" * 40,
        source_snapshot_sha256=hashlib.sha256(source_archive).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector="feedback_stagec_isolation",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    artifacts = SandboxArtifacts(
        job=job,
        source_archive=source_archive,
        test_patch=patch,
    )

    class CountingRunner:
        def __init__(self) -> None:
            self.calls = 0
            self.runner = DockerRunner(
                image_digest=image_digest,
                work_root=tmp_path / "work",
            )

        def execute(self, job: SandboxJob, artifacts: SandboxArtifacts):
            self.calls += 1
            return self.runner.execute(job, artifacts)

    runner = CountingRunner()
    worker = SandboxWorker(
        credential="integration-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "results"),
    )

    first = worker.execute("Bearer integration-secret", artifacts.to_wire())
    second = worker.execute("Bearer integration-secret", artifacts.to_wire())

    assert first == second
    assert first.status is SandboxStatus.COMPLETED
    assert first.exit_code == 0
    assert runner.calls == 1
    assert not any((tmp_path / "work").iterdir())

    timeout_job = job.model_copy(
        update={
            "job_id": uuid4(),
            "target_test_selector": "feedback_stagec_timeout",
            "limits": SandboxLimits(wall_timeout_seconds=1),
        }
    )
    timeout_artifacts = SandboxArtifacts(
        job=timeout_job,
        source_archive=source_archive,
        test_patch=patch,
    )
    timed_out = worker.execute(
        "Bearer integration-secret",
        timeout_artifacts.to_wire(),
    )

    assert timed_out.status is SandboxStatus.TIMED_OUT
    assert timed_out.timed_out is True
    assert runner.calls == 2
    assert not any((tmp_path / "work").iterdir())


def _archive_snapshot(snapshot: Path) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                content = path.read_bytes()
                relative = path.relative_to(snapshot).as_posix()
                info = tarfile.TarInfo(f"repo-root/{relative}")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _trusted_smoke_patch(original: str, replacement: str) -> bytes:
    path = "backend/tests/test_feedback_regressions.py"
    body = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}".encode("utf-8")
