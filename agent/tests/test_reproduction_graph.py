import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agent.controller import GateController
from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    FeedbackType,
    GateCategory,
    GateIntent,
)
from agent.domain.gate import GateClassification
from agent.domain.models import FeedbackRecord
from agent.domain.reproduction import (
    ExpectedFailureKind,
    OracleKind,
    OracleSpec,
    ReproductionDisposition,
    ReproductionPlan,
    SourceReadRequest,
    TestGenerationResult as GeneratedTestResult,
)
from agent.graph import ReproductionDependencies
from agent.providers.fake import FakeModelProvider
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.sandbox.contracts import (
    JUnitSummary,
    SandboxResult,
    SandboxStatus,
    TargetTestOutcome,
)
from agent.tools.edits import StructuredEditTools
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import Edit, EditMode, PatchBuilder
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.source_repository import SourceSnapshot


FEEDBACK_ID = UUID("a257a846-1728-4d39-81bf-75a388041215")
SELECTOR = "test_feedback_a257a846_table_structure"
BASE_SHA = "a" * 40


class _SourceWorkspace:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "backend/app").mkdir(parents=True)
        (root / "backend/tests").mkdir(parents=True)
        (root / "backend/app/normalizer.py").write_text(
            "def normalize(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
        self.archive = root.parent / "source.tar.gz"
        self.archive.write_bytes(b"fixed-baseline-archive")
        self.snapshot = SourceSnapshot(
            base_sha=BASE_SHA,
            source_snapshot_sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            root=root,
            archive_path=self.archive,
        )

    async def prepare(self, run_id):
        return f"source://{run_id}/{BASE_SHA}", self.snapshot

    def resolve(self, reference: str):
        assert reference.endswith(BASE_SHA)
        return self.snapshot


class _Sandbox:
    def __init__(self, outcomes: list[TargetTestOutcome]) -> None:
        self.outcomes = outcomes
        self.jobs = []

    async def submit(self, artifacts):
        self.jobs.append(artifacts)
        outcome = self.outcomes.pop(0)
        now = datetime.now(UTC)
        return SandboxResult(
            job_id=artifacts.job.job_id,
            status=SandboxStatus.COMPLETED,
            exit_code=0 if outcome is TargetTestOutcome.PASSED else 1,
            started_at=now,
            finished_at=now,
            duration_ms=5,
            junit_summary=JUnitSummary(
                tests=1,
                failures=int(outcome is TargetTestOutcome.FAILED),
                errors=int(outcome is TargetTestOutcome.ERROR),
                skipped=0,
                target_collected=True,
                target_outcome=outcome,
                target_failure_type=(
                    "AssertionError" if outcome is TargetTestOutcome.FAILED else None
                ),
                target_message="expected three rows",
            ),
        )


def _classification() -> GateClassification:
    return GateClassification(
        intent=GateIntent.BUG_REPORT,
        category=GateCategory.TABLE_PARSING,
        relevance=0.99,
        sufficient_information=True,
        injection_suspected=False,
        requires_extension_change=False,
        reason="backend table regression",
    )


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        hypothesis="table loses a row after DOCX export",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_table_count", "minimum": 3},
        ),
        target_test_selector=SELECTOR,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/normalizer.py"),),
    )


def _generated() -> GeneratedTestResult:
    plan = _plan()
    return GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "from docx_assertions import assert_minimum_table_count\n\n"
                    f"def {SELECTOR}():\n"
                    "    assert_minimum_table_count(b'docx', 3)\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="assert the trusted table structure",
        files_needed_for_fix=("backend/app/normalizer.py",),
    )


async def _run(
    tmp_path: Path,
    outcomes: list[TargetTestOutcome],
    *,
    generated_outputs: list[GeneratedTestResult] | None = None,
):
    feedback = FeedbackRecord(
        id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content="|a|b|\n|-|-|\n|1|2|",
        description="exported Word table structure is incorrect",
        status=FeedbackStatus.CLAIMED,
        claim_token=UUID("11111111-1111-4111-8111-111111111111"),
        claimed_at=datetime.now(UTC),
    )
    feedbacks = FakeFeedbackRepository([feedback])
    runs = FakeAgentRunRepository()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    source = _SourceWorkspace(tmp_path / "snapshot")
    sandbox = _Sandbox(outcomes.copy())
    test_provider = FakeModelProvider(
        generated_outputs or [_generated(), _generated()]
    )
    controller = GateController(
        feedback_repository=feedbacks,
        run_repository=runs,
        provider=FakeModelProvider([_classification()]),
        artifact_store=artifacts,
        checkpointer=MemorySaver(),
        reproduction=ReproductionDependencies(
            plan_provider=FakeModelProvider([_plan()]),
            test_provider=test_provider,
            source_workspace=source,
            edit_tools=StructuredEditTools(
                PatchBuilder(PatchPolicy.load_default()),
                artifacts,
            ),
            sandbox_client=sandbox,
        ),
    )
    outcome = await controller.start(feedback)
    return outcome, await feedbacks.get(FEEDBACK_ID), await runs.get(outcome.run_id), sandbox, test_provider


def test_known_target_failure_produces_reproduction_report(tmp_path: Path) -> None:
    outcome, feedback, run, sandbox, test_provider = asyncio.run(
        _run(tmp_path, [TargetTestOutcome.FAILED])
    )

    assert outcome.completed is False
    assert feedback is not None and feedback.status is FeedbackStatus.REPAIRING
    assert run is not None and run.status is AgentRunStatus.REPAIRING
    assert run.base_sha == BASE_SHA
    assert run.reproduction is not None
    assert run.reproduction["disposition"] == ReproductionDisposition.REPRODUCED.value
    assert len(sandbox.jobs) == 1
    assert len(test_provider.requests) == 1
    assert test_provider.requests[0].timeout_seconds == 180.0


def test_two_direct_passes_become_cannot_reproduce(tmp_path: Path) -> None:
    _, feedback, run, sandbox, test_provider = asyncio.run(
        _run(tmp_path, [TargetTestOutcome.PASSED, TargetTestOutcome.PASSED])
    )

    assert feedback is not None
    assert feedback.status is FeedbackStatus.CANNOT_REPRODUCE
    assert run is not None and run.reproduction is not None
    assert run.reproduction["round"] == 2
    assert run.reproduction["disposition"] == ReproductionDisposition.NOT_REPRODUCED.value
    assert len(sandbox.jobs) == 2
    assert len(test_provider.requests) == 2
    # 两轮 Job 都携带同一原始源码 archive，而不是上一轮 workspace。
    assert sandbox.jobs[0].source_archive == sandbox.jobs[1].source_archive


def test_invalid_python_edit_is_revised_once_before_sandbox(tmp_path: Path) -> None:
    plan = _plan()
    invalid = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "from docx_assertions import assert_minimum_table_count\n"
                    f"def {SELECTOR}(:\n"
                    "    assert_minimum_table_count(b'docx', 3)\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="first malformed attempt",
    )
    _, feedback, run, sandbox, test_provider = asyncio.run(
        _run(
            tmp_path,
            [TargetTestOutcome.FAILED],
            generated_outputs=[invalid, _generated()],
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.REPAIRING
    assert run is not None and run.reproduction is not None
    assert run.reproduction["round"] == 2
    assert len(test_provider.requests) == 2
    assert len(sandbox.jobs) == 1


def test_missing_search_replace_target_is_revised_instead_of_terminal(tmp_path: Path) -> None:
    plan = _plan()
    missing_target = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.SEARCH_REPLACE,
                search="def existing_test():\n    pass\n",
                replace=(
                    "from docx_assertions import assert_minimum_table_count\n\n"
                    f"def {SELECTOR}():\n"
                    "    assert_minimum_table_count(b'docx', 3)\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="first attempt assumed the fixed regression file already existed",
    )
    _, feedback, run, sandbox, test_provider = asyncio.run(
        _run(
            tmp_path,
            [TargetTestOutcome.FAILED],
            generated_outputs=[missing_target, _generated()],
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.REPAIRING
    assert run is not None and run.reproduction is not None
    assert run.reproduction["round"] == 2
    assert len(test_provider.requests) == 2
    assert len(sandbox.jobs) == 1


def test_local_test_policy_gets_one_bounded_model_correction(tmp_path: Path) -> None:
    plan = _plan()
    missing_trusted_assertion = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    f"def {SELECTOR}():\n"
                    "    assert True\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="first attempt omitted the registered assertion",
    )
    _, feedback, run, sandbox, test_provider = asyncio.run(
        _run(
            tmp_path,
            [TargetTestOutcome.FAILED],
            generated_outputs=[missing_trusted_assertion, _generated()],
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.REPAIRING
    assert run is not None and run.reproduction is not None
    assert run.reproduction["round"] == 1
    assert len(test_provider.requests) == 2
    assert len(sandbox.jobs) == 1
    correction = test_provider.requests[1].messages[-1].content
    assert "DOCX oracle must use its registered trusted assertion" in correction
    assert "backend/tests/test_feedback_regressions.py" in correction


def test_two_invalid_python_edits_become_cannot_reproduce(tmp_path: Path) -> None:
    plan = _plan()
    invalid = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "from docx_assertions import assert_minimum_table_count\n"
                    f"def {SELECTOR}(:\n"
                    "    assert_minimum_table_count(b'docx', 3)\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="malformed attempt",
    )
    _, feedback, run, sandbox, _ = asyncio.run(
        _run(
            tmp_path,
            [],
            generated_outputs=[invalid, invalid],
        )
    )

    assert feedback is not None
    assert feedback.status is FeedbackStatus.CANNOT_REPRODUCE
    assert run is not None and run.reproduction is not None
    assert run.reproduction["disposition"] == ReproductionDisposition.INVALID_TEST.value
    assert run.reproduction["round"] == 2
    assert not sandbox.jobs


def test_forbidden_pytest_plugin_is_security_rejected_before_sandbox(tmp_path: Path) -> None:
    plan = _plan()
    unsafe = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "pytest_plugins = ['external_plugin']\n"
                    "from docx_assertions import assert_minimum_table_count\n\n"
                    f"def {SELECTOR}():\n"
                    "    assert_minimum_table_count(b'docx', 3)\n"
                ),
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="unsafe plugin request",
    )
    outcome, feedback, run, sandbox, _ = asyncio.run(
        _run(tmp_path, [], generated_outputs=[unsafe])
    )

    assert outcome.completed is False
    assert feedback is not None
    assert feedback.status is FeedbackStatus.SECURITY_REJECTED
    assert run is not None and run.status is AgentRunStatus.SECURITY_REJECTED
    assert not sandbox.jobs
