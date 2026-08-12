import difflib
import hashlib
import io
import os
import shutil
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agent.domain.enums import FeedbackType
from agent.domain.models import TaskArtifact
from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxLimits,
    SandboxStatus,
    TargetTestOutcome,
)
from agent.domain.reproduction import (
    ExpectedFailureKind,
    OracleKind,
    OracleSpec,
    ReproductionDisposition,
    ReproductionPlan,
    ReproductionReport,
    SourceReadRequest,
    classify_reproduction_result,
)
from agent.domain.repair import build_validation_result
from agent.reproduction import build_mermaid_test_fallback
from agent.sandbox.docker_runner import DockerRunner
from agent.sandbox.worker import FileJobStore, SandboxWorker
from agent.workspace.edits import EditPhase, PatchBuilder
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.validation import compose_validated_patch, normalize_authorized_patch


pytestmark = pytest.mark.docker


def test_stage_d_known_table_defect_becomes_trusted_target_failure(tmp_path: Path):
    image_digest = os.environ.get("SANDBOX_IMAGE_DIGEST", "")
    if not image_digest or shutil.which("docker") is None:
        pytest.skip("SANDBOX_IMAGE_DIGEST and Docker are required")

    snapshot = tmp_path / "snapshot"
    (snapshot / "backend/app").mkdir(parents=True)
    (snapshot / "backend/tests").mkdir(parents=True)
    (snapshot / "backend/app/__init__.py").write_text("", encoding="utf-8")
    # 固定一个可打开但缺少表格节点的 DOCX，代表已知“表格导出成普通文本”基线缺陷。
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            "| A | B |</w:t></w:r></w:p></w:body></w:document>",
        )
    (snapshot / "backend/app/repro_target.py").write_text(
        "DOCUMENT = bytes.fromhex(" + repr(docx.getvalue().hex()) + ")\n"
        "def convert_known_table_case():\n    return DOCUMENT\n",
        encoding="utf-8",
    )
    target = snapshot / "backend/tests/test_feedback_regressions.py"
    original = "# Stage D known-defect baseline\n"
    target.write_text(original, encoding="utf-8")
    selector = "test_feedback_ab12cd34_table_structure"
    generated_test = f'''from app.repro_target import convert_known_table_case
from docx_assertions import assert_minimum_table_count


def {selector}():
    assert_minimum_table_count(convert_known_table_case(), 1)
'''
    patch = _trusted_smoke_patch(original, generated_test)
    source_archive = _archive_snapshot(snapshot)
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="b" * 40,
        source_snapshot_sha256=hashlib.sha256(source_archive).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector=selector,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    result = DockerRunner(
        image_digest=image_digest,
        work_root=tmp_path / "work",
    ).execute(
        job,
        SandboxArtifacts(job=job, source_archive=source_archive, test_patch=patch),
    )

    assert result.junit_summary is not None
    assert result.junit_summary.target_outcome is TargetTestOutcome.FAILED
    report = classify_reproduction_result(
        result,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        round_number=1,
        target_test_selector=selector,
    )
    assert report.disposition is ReproductionDisposition.REPRODUCED


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


def test_stage_e_real_docker_reproves_baseline_and_validates_fix(tmp_path: Path):
    image_digest = os.environ.get("SANDBOX_IMAGE_DIGEST", "")
    if not image_digest or shutil.which("docker") is None:
        pytest.skip("SANDBOX_IMAGE_DIGEST and Docker are required")

    snapshot = tmp_path / "stage-e-snapshot"
    (snapshot / "backend/app").mkdir(parents=True)
    (snapshot / "backend/tests").mkdir(parents=True)
    (snapshot / "backend/app/__init__.py").write_text("", encoding="utf-8")

    broken = io.BytesIO()
    fixed = io.BytesIO()
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p /></w:body></w:document>',
        )
    with zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p />'
            "</w:tc></w:tr></w:tbl></w:body></w:document>",
        )
    app_path = "backend/app/normalizer.py"
    broken_source = (
        "DOCUMENT = bytes.fromhex(" + repr(broken.getvalue().hex()) + ")\n"
        "def convert_feedback_case():\n    return DOCUMENT\n"
    )
    fixed_source = (
        "DOCUMENT = bytes.fromhex(" + repr(fixed.getvalue().hex()) + ")\n"
        "def convert_feedback_case():\n    return DOCUMENT\n"
    )
    (snapshot / app_path).write_text(broken_source, encoding="utf-8")
    test_path = "backend/tests/test_feedback_regressions.py"
    original_test = "# Stage E baseline\n"
    selector = "test_feedback_ab12cd34_table_fixed"
    generated_test = f'''from app.normalizer import convert_feedback_case
from docx_assertions import assert_minimum_table_count


def {selector}():
    assert_minimum_table_count(convert_feedback_case(), 1)
'''
    (snapshot / test_path).write_text(original_test, encoding="utf-8")
    test_patch = _patch_for(test_path, original_test, generated_test)
    fix_patch = _patch_for(app_path, broken_source, fixed_source)
    source_archive = _archive_snapshot(snapshot)
    source_hash = hashlib.sha256(source_archive).hexdigest()
    validated = compose_validated_patch(snapshot, test_patch, fix_patch)
    normalized_test_patch = normalize_authorized_patch(snapshot, test_patch)
    run_id = uuid4()
    runner = DockerRunner(image_digest=image_digest, work_root=tmp_path / "stage-e-work")

    def execute(job_type: JobType, include_fix: bool):
        job = SandboxJob(
            job_id=uuid4(),
            run_id=run_id,
            job_type=job_type,
            base_sha="e" * 40,
            source_snapshot_sha256=source_hash,
            test_patch_sha256=hashlib.sha256(test_patch).hexdigest(),
            fix_patch_sha256=(
                hashlib.sha256(fix_patch).hexdigest() if include_fix else None
            ),
            target_test_selector=selector,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        return runner.execute(
            job,
            SandboxArtifacts(
                job=job,
                source_archive=source_archive,
                test_patch=test_patch,
                fix_patch=fix_patch if include_fix else None,
            ),
        )

    baseline = execute(JobType.REPRODUCE_TARGET, False)
    target = execute(JobType.VALIDATE_TARGET, True)
    full = execute(JobType.VALIDATE_FULL, True)
    result = build_validation_result(
        base_sha="e" * 40,
        source_snapshot_sha256=source_hash,
        test_patch_sha256=hashlib.sha256(test_patch).hexdigest(),
        fix_patch_sha256=hashlib.sha256(fix_patch).hexdigest(),
        target_test_selector=selector,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        trusted_docx_check="assert_minimum_table_count",
        baseline_result=baseline,
        target_result=target,
        full_result=full,
        baseline_skipped=0,
        changed_files=validated.changed_files,
        validated_patch_ref="artifact://run/validated.patch",
        validated_patch_sha256=validated.sha256,
    )

    assert baseline.junit_summary is not None
    assert baseline.junit_summary.target_outcome is TargetTestOutcome.FAILED
    assert baseline.workspace_diff_sha256 == normalized_test_patch.sha256
    assert target.junit_summary is not None
    assert target.junit_summary.target_outcome is TargetTestOutcome.PASSED
    assert full.junit_summary is not None and full.junit_summary.failures == 0
    assert full.docx_summary == {"passed": True}
    assert target.workspace_diff_sha256 == validated.sha256
    assert full.workspace_diff_sha256 == validated.sha256
    assert result.passed is True


def test_mermaid_renderer_reproduces_and_validates_real_fix_in_docker(tmp_path: Path):
    image_digest = os.environ.get("SANDBOX_IMAGE_DIGEST", "")
    if not image_digest or shutil.which("docker") is None:
        pytest.skip("SANDBOX_IMAGE_DIGEST and Docker are required")

    snapshot = tmp_path / "mermaid-fallback-snapshot"
    repository_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repository_root / "backend/app", snapshot / "backend/app")
    tests_root = snapshot / "backend/tests"
    tests_root.mkdir(parents=True)
    existing_test = "# Mermaid fallback baseline\n"
    (tests_root / "test_feedback_regressions.py").write_text(
        existing_test,
        encoding="utf-8",
    )

    feedback_id = uuid4()
    selector = f"test_feedback_{feedback_id.hex[:8]}_mermaid_drawing"
    task = TaskArtifact(
        feedback_id=feedback_id,
        feedback_type=FeedbackType.BUG,
        markdown_content="graph TD\nA([开始]) --> B([结束])",
        description="Word only contains Mermaid source instead of a drawing",
        content_fingerprint="f" * 64,
    )
    plan = ReproductionPlan(
        hypothesis="Mermaid source is not rendered as a drawing",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_drawing_count", "minimum": 1},
        ),
        target_test_selector=selector,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/pandoc_runner.py"),),
    )
    previous = ReproductionReport(
        disposition=ReproductionDisposition.INVALID_TEST,
        round=1,
        target_test_selector=selector,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        failure_code="invalid_test_edit",
        failure_summary="generated test edit is invalid",
    )
    generated = build_mermaid_test_fallback(
        task,
        plan=plan,
        previous_report=previous,
        existing_test_source=existing_test,
    )
    assert generated is not None
    test_patch = PatchBuilder(PatchPolicy.load_default()).build(
        snapshot,
        generated.edits,
        EditPhase.TEST,
        target_test_selector=selector,
    ).content
    source_archive = _archive_snapshot(snapshot)
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="f" * 40,
        source_snapshot_sha256=hashlib.sha256(source_archive).hexdigest(),
        test_patch_sha256=hashlib.sha256(test_patch).hexdigest(),
        target_test_selector=selector,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    result = DockerRunner(
        image_digest=image_digest,
        work_root=tmp_path / "mermaid-fallback-work",
    ).execute(
        job,
        SandboxArtifacts(
            job=job,
            source_archive=source_archive,
            test_patch=test_patch,
        ),
    )
    report = classify_reproduction_result(
        result,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        round_number=2,
        target_test_selector=selector,
    )

    assert result.junit_summary is not None
    assert result.junit_summary.tests == 1
    assert result.junit_summary.target_failure_type == "AssertionError"
    assert result.junit_summary.target_outcome is TargetTestOutcome.FAILED
    assert report.disposition is ReproductionDisposition.REPRODUCED

    runner_path = snapshot / "backend/app/pandoc_runner.py"
    original_runner = runner_path.read_text(encoding="utf-8")
    fixed_runner = original_runner.replace(
        "from app.normalizer import normalize_markdown\n",
        (
            "from app.mermaid_renderer import "
            "MermaidRenderError, render_mermaid_blocks\n"
            "from app.normalizer import normalize_markdown\n"
        ),
        1,
    ).replace(
        '    input_path.write_text(normalize_markdown(markdown), encoding="utf-8")\n',
        (
            "    normalized = normalize_markdown(markdown)\n"
            "    try:\n"
            "        normalized = render_mermaid_blocks(normalized, work_dir)\n"
            "    except MermaidRenderError as exc:\n"
            "        raise ConversionError(exc.message, exc.details) from exc\n"
            '    input_path.write_text(normalized, encoding="utf-8")\n'
        ),
        1,
    )
    assert fixed_runner != original_runner
    fix_patch = _patch_for(
        "backend/app/pandoc_runner.py",
        original_runner,
        fixed_runner,
    )
    validation_job = job.model_copy(
        update={
            "job_id": uuid4(),
            "job_type": JobType.VALIDATE_TARGET,
            "fix_patch_sha256": hashlib.sha256(fix_patch).hexdigest(),
        }
    )
    validated = DockerRunner(
        image_digest=image_digest,
        work_root=tmp_path / "mermaid-validation-work",
    ).execute(
        validation_job,
        SandboxArtifacts(
            job=validation_job,
            source_archive=source_archive,
            test_patch=test_patch,
            fix_patch=fix_patch,
        ),
    )

    assert validated.junit_summary is not None
    assert validated.junit_summary.tests == 1
    assert validated.junit_summary.target_outcome is TargetTestOutcome.PASSED
    assert validated.status is SandboxStatus.COMPLETED


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
    return _patch_for(path, original, replacement)


def _patch_for(path: str, original: str, replacement: str) -> bytes:
    body = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}".encode("utf-8")
