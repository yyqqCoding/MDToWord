import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agent.domain.errors import (
    SandboxAuthenticationError,
    SandboxJobConflictError,
)
from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)
from agent.sandbox.worker import FileJobStore, SandboxWorker


def _job(source: bytes, patch: bytes) -> SandboxJob:
    return SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="a" * 40,
        source_snapshot_sha256=hashlib.sha256(source).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector="feedback_ab12cd_table",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


class RecordingRunner:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, job: SandboxJob, artifacts: SandboxArtifacts) -> SandboxResult:
        self.calls += 1
        now = datetime.now(UTC)
        return SandboxResult(
            job_id=job.job_id,
            status=SandboxStatus.COMPLETED,
            exit_code=0,
            started_at=now,
            finished_at=now,
            duration_ms=0,
            stdout_tail="ok",
            workspace_diff_sha256="d" * 64,
        )


def test_worker_authenticates_verifies_hashes_and_reuses_completed_job(tmp_path: Path):
    source = b"snapshot"
    patch = b"patch"
    job = _job(source, patch)
    runner = RecordingRunner()
    worker = SandboxWorker(
        credential="worker-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "jobs"),
    )
    request = SandboxArtifacts(job=job, source_archive=source, test_patch=patch)

    first = worker.execute("Bearer worker-secret", request.to_wire())
    second = worker.execute("Bearer worker-secret", request.to_wire())

    assert first == second
    assert runner.calls == 1
    assert not any(
        "worker-secret" in path.read_text(encoding="utf-8")
        for path in (tmp_path / "jobs").iterdir()
    )


def test_worker_rejects_bad_auth_before_parsing_payload(tmp_path: Path):
    worker = SandboxWorker(
        credential="worker-secret",
        runner=RecordingRunner(),
        store=FileJobStore(tmp_path / "jobs"),
    )

    with pytest.raises(SandboxAuthenticationError):
        worker.execute("Bearer wrong", {"invalid": "payload"})


def test_worker_rejects_hash_mismatch_without_running(tmp_path: Path):
    source = b"snapshot"
    patch = b"patch"
    job = _job(source, patch)
    runner = RecordingRunner()
    worker = SandboxWorker(
        credential="worker-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "jobs"),
    )
    payload = SandboxArtifacts(job=job, source_archive=source, test_patch=patch).to_wire()
    payload["source_archive_b64"] = base64.b64encode(b"tampered").decode("ascii")

    with pytest.raises(ValueError):
        worker.execute("Bearer worker-secret", payload)

    assert runner.calls == 0


def test_same_job_id_with_different_payload_is_conflict(tmp_path: Path):
    source = b"snapshot"
    patch = b"patch"
    job = _job(source, patch)
    runner = RecordingRunner()
    worker = SandboxWorker(
        credential="worker-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "jobs"),
    )
    request = SandboxArtifacts(job=job, source_archive=source, test_patch=patch)
    worker.execute("Bearer worker-secret", request.to_wire())

    changed_job = job.model_copy(update={"base_sha": "e" * 40})
    changed = SandboxArtifacts(
        job=changed_job,
        source_archive=source,
        test_patch=patch,
    )
    with pytest.raises(SandboxJobConflictError):
        worker.execute("Bearer worker-secret", changed.to_wire())


def test_expired_job_is_rejected_without_running(tmp_path: Path):
    source = b"snapshot"
    patch = b"patch"
    job = _job(source, patch).model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    runner = RecordingRunner()
    worker = SandboxWorker(
        credential="worker-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "jobs"),
    )

    with pytest.raises(ValueError):
        worker.execute(
            "Bearer worker-secret",
            SandboxArtifacts(job=job, source_archive=source, test_patch=patch).to_wire(),
        )

    assert runner.calls == 0


def test_unexpected_runner_failure_is_redacted_persisted_and_not_retried(tmp_path: Path):
    source = b"snapshot"
    patch = b"patch"
    job = _job(source, patch)

    class FailingRunner:
        calls = 0

        def execute(self, job: SandboxJob, artifacts: SandboxArtifacts) -> SandboxResult:
            del job, artifacts
            self.calls += 1
            raise RuntimeError("MODEL_API_KEY=do-not-store")

    runner = FailingRunner()
    worker = SandboxWorker(
        credential="worker-secret",
        runner=runner,
        store=FileJobStore(tmp_path / "jobs"),
    )
    request = SandboxArtifacts(job=job, source_archive=source, test_patch=patch)

    first = worker.execute("Bearer worker-secret", request.to_wire())
    second = worker.execute("Bearer worker-secret", request.to_wire())

    assert first == second
    assert first.error_code == "sandbox_internal_error"
    assert runner.calls == 1
    stored = next((tmp_path / "jobs").iterdir()).read_text(encoding="utf-8")
    assert "do-not-store" not in stored
