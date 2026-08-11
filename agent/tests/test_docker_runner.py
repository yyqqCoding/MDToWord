import hashlib
import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent.sandbox.contracts import (
    JobType,
    SandboxArtifacts,
    SandboxJob,
    SandboxStatus,
    TargetTestOutcome,
)
from agent.sandbox.docker_runner import CommandOutcome, DockerRunner


def _archive() -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        files = {
            "repo-root/backend/app/__init__.py": b"",
            "repo-root/backend/tests/test_feedback_regressions.py": (
                b"def test_existing():\n    assert True\n"
            ),
        }
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _patch() -> bytes:
    return (
        b"diff --git a/backend/tests/test_feedback_regressions.py "
        b"b/backend/tests/test_feedback_regressions.py\n"
        b"index 3456c3f..c9089a2 100644\n"
        b"--- a/backend/tests/test_feedback_regressions.py\n"
        b"+++ b/backend/tests/test_feedback_regressions.py\n"
        b"@@ -1,2 +1,5 @@\n"
        b" def test_existing():\n"
        b"     assert True\n"
        b"+\n"
        b"+def test_feedback_ab12cd_table():\n"
        b"+    assert False\n"
    )


def test_docker_runner_builds_fixed_hardened_argv_and_destroys_workspace(tmp_path: Path):
    source = _archive()
    patch = _patch()
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="a" * 40,
        source_snapshot_sha256=hashlib.sha256(source).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector="feedback_ab12cd_table",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    captured: dict[str, object] = {}

    def execute(argv: tuple[str, ...], timeout_seconds: int) -> CommandOutcome:
        captured["argv"] = argv
        captured["timeout"] = timeout_seconds
        workspace_arg = next(item for item in argv if item.endswith(":/workspace:rw"))
        workspace = Path(
            workspace_arg.removeprefix("--volume=").removesuffix(":/workspace:rw")
        )
        captured["workspace"] = workspace
        assert workspace.is_dir()
        return CommandOutcome(exit_code=0, stdout=b"ok", stderr=b"")

    runner = DockerRunner(
        image_digest="registry.example/mdtoword-sandbox@sha256:" + "f" * 64,
        work_root=tmp_path / "work",
        execute_command=execute,
    )
    result = runner.execute(
        job,
        SandboxArtifacts(job=job, source_archive=source, test_patch=patch),
    )

    argv = captured["argv"]
    assert isinstance(argv, tuple)
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--memory=2147483648" in argv
    assert "--cpus=2.0" in argv
    assert "--pids-limit=256" in argv
    assert "--user=65532:65532" in argv
    assert "--env=PYTHONPATH=/opt/trusted" in argv
    assert "sh" not in argv
    assert "bash" not in argv
    assert result.status is SandboxStatus.COMPLETED
    assert result.workspace_diff_sha256 is not None
    assert not Path(captured["workspace"]).exists()


def test_docker_runner_parses_target_result_from_junit_not_stdout(tmp_path: Path):
    source = _archive()
    patch = _patch()
    job = SandboxJob(
        job_id=uuid4(),
        run_id=uuid4(),
        job_type=JobType.REPRODUCE_TARGET,
        base_sha="a" * 40,
        source_snapshot_sha256=hashlib.sha256(source).hexdigest(),
        test_patch_sha256=hashlib.sha256(patch).hexdigest(),
        target_test_selector="feedback_ab12cd_table",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    def execute(argv: tuple[str, ...], timeout_seconds: int) -> CommandOutcome:
        del timeout_seconds
        result_arg = next(item for item in argv if item.endswith(":/result:rw"))
        result_root = Path(
            result_arg.removeprefix("--volume=").removesuffix(":/result:rw")
        )
        (result_root / "junit.xml").write_text(
            """<testsuite><testcase name="test_feedback_ab12cd_table">
<failure type="AssertionError" message="assert 2 == 3">traceback</failure>
</testcase></testsuite>""",
            encoding="utf-8",
        )
        return CommandOutcome(
            exit_code=1,
            stdout=b"misleading text: 1 passed",
            stderr=b"",
        )

    result = DockerRunner(
        image_digest="registry.example/mdtoword-sandbox@sha256:" + "f" * 64,
        work_root=tmp_path / "work",
        execute_command=execute,
    ).execute(
        job,
        SandboxArtifacts(job=job, source_archive=source, test_patch=patch),
    )

    assert result.junit_summary is not None
    assert result.junit_summary.target_outcome is TargetTestOutcome.FAILED
    assert result.junit_summary.target_failure_type == "AssertionError"
