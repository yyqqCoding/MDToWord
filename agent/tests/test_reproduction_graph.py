import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
    GateRoute,
    RiskLevel,
)
from agent.domain.gate import GateClassification
from agent.domain.errors import PublicationError
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
from agent.domain.repair import FixGenerationResult
from agent.graph import (
    PublishingDependencies,
    RepairDependencies,
    ReproductionDependencies,
)
from agent.publishing.contracts import (
    PublicationDisposition,
    PublicationResult,
)
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
        (root / "backend/app/mermaid_renderer.py").write_text(
            "def render_mermaid_blocks(markdown: str, work_dir):\n    return markdown\n",
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
            docx_summary={"passed": outcome is TargetTestOutcome.PASSED},
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


class _Publisher:
    def __init__(self, disposition: PublicationDisposition) -> None:
        self.disposition = disposition
        self.requests = []

    async def publish(self, request):
        self.requests.append(request)
        branch = f"agent/feedback-{str(request.feedback_id)[:8]}-table_parsing"
        if self.disposition is PublicationDisposition.STALE_BASE:
            return PublicationResult(
                disposition=self.disposition,
                branch=branch,
            )
        return PublicationResult(
            disposition=self.disposition,
            branch=branch,
            commit_sha="f" * 40,
            pr_number=17,
            pr_url="https://github.com/example/md-to-word/pull/17",
        )


class _FailingPublisher:
    def __init__(self) -> None:
        self.requests = []

    async def publish(self, request):
        self.requests.append(request)
        raise PublicationError("safe publication failure")


class _RetryingPublisher(_Publisher):
    def __init__(self) -> None:
        super().__init__(PublicationDisposition.PR_OPENED)
        self.attempts = 0

    async def publish(self, request):
        self.attempts += 1
        if self.attempts == 1:
            self.requests.append(request)
            raise PublicationError("safe first publication failure")
        return await super().publish(request)


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


def _fix(*, strip: bool = True) -> FixGenerationResult:
    return FixGenerationResult(
        edits=(
            Edit(
                path="backend/app/normalizer.py",
                mode=EditMode.SEARCH_REPLACE,
                search="    return value\n",
                replace=(
                    "    return value.strip()\n" if strip else "    return value.rstrip()\n"
                ),
            ),
        ),
        summary="normalize the backend value before export",
        behavior_changes=("the regression input is normalized",),
        risk_level=RiskLevel.LOW,
        manual_review_notes=(),
    )


def _external_dependency_fix() -> FixGenerationResult:
    return FixGenerationResult(
        edits=(
            Edit(
                path="backend/app/normalizer.py",
                mode=EditMode.SEARCH_REPLACE,
                search="    return value\n",
                replace=(
                    "    return value if shutil.which('pandoc-mermaid') else value\n"
                ),
            ),
        ),
        summary="convert Mermaid through an external Pandoc filter",
        behavior_changes=("Mermaid blocks are rendered by pandoc-mermaid",),
        risk_level=RiskLevel.HIGH,
        manual_review_notes=("deployment must install pandoc-mermaid",),
    )


async def _run(
    tmp_path: Path,
    outcomes: list[TargetTestOutcome],
    *,
    generated_outputs: list[GeneratedTestResult] | None = None,
    markdown: str = "|a|b|\n|-|-|\n|1|2|",
    description: str = "exported Word table structure is incorrect",
    gate_classification: GateClassification | None = None,
    reproduction_plan: ReproductionPlan | None = None,
):
    feedback = FeedbackRecord(
        id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content=markdown,
        description=description,
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
        provider=FakeModelProvider([gate_classification or _classification()]),
        artifact_store=artifacts,
        checkpointer=MemorySaver(),
        reproduction=ReproductionDependencies(
            plan_provider=FakeModelProvider([reproduction_plan or _plan()]),
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


async def _run_stage_e(
    tmp_path: Path,
    outcomes: list[TargetTestOutcome],
    *,
    fixes: list[FixGenerationResult],
    max_model_calls: int = 8,
    markdown: str = "|a|b|\n|-|-|\n|1|2|",
    description: str = "exported Word table structure is incorrect",
    gate_classification: GateClassification | None = None,
    reproduction_plan: ReproductionPlan | None = None,
    generated_test: GeneratedTestResult | None = None,
    publisher: _Publisher | _FailingPublisher | _RetryingPublisher | None = None,
    capture_publication_error: bool = False,
    runtime_capture: dict[str, object] | None = None,
):
    feedback = FeedbackRecord(
        id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content=markdown,
        description=description,
        status=FeedbackStatus.CLAIMED,
        claim_token=UUID("11111111-1111-4111-8111-111111111111"),
        claimed_at=datetime.now(UTC),
    )
    feedbacks = FakeFeedbackRepository([feedback])
    runs = FakeAgentRunRepository()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    source = _SourceWorkspace(tmp_path / "snapshot")
    sandbox = _Sandbox(outcomes.copy())
    fix_provider = FakeModelProvider(fixes)
    controller = GateController(
        feedback_repository=feedbacks,
        run_repository=runs,
        provider=FakeModelProvider([gate_classification or _classification()]),
        artifact_store=artifacts,
        checkpointer=MemorySaver(),
        reproduction=ReproductionDependencies(
            plan_provider=FakeModelProvider([reproduction_plan or _plan()]),
            test_provider=FakeModelProvider([generated_test or _generated()]),
            source_workspace=source,
            edit_tools=StructuredEditTools(
                PatchBuilder(PatchPolicy.load_default()),
                artifacts,
            ),
            sandbox_client=sandbox,
        ),
        repair=RepairDependencies(
            fix_provider=fix_provider,
            max_model_calls=max_model_calls,
        ),
        publishing=(
            PublishingDependencies(
                publisher=publisher,
                trace_url_template="https://trace.example/{trace_id}",
            )
            if publisher is not None
            else None
        ),
    )
    if runtime_capture is not None:
        runtime_capture.update(
            {
                "controller": controller,
                "feedbacks": feedbacks,
                "runs": runs,
            }
        )
    try:
        outcome = await controller.start(feedback)
    except PublicationError as exc:
        if not capture_publication_error:
            raise
        run_directories = list((tmp_path / "artifacts").iterdir())
        assert len(run_directories) == 1
        outcome = SimpleNamespace(
            run_id=UUID(run_directories[0].name),
            completed=False,
            pr_url=None,
            error=exc,
        )
    return (
        outcome,
        await feedbacks.get(FEEDBACK_ID),
        await runs.get(outcome.run_id),
        sandbox,
        fix_provider,
        artifacts,
    )


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


def test_mermaid_invalid_model_edit_uses_trusted_fallback_without_second_call(
    tmp_path: Path,
) -> None:
    selector = "test_feedback_a257a846_mermaid_drawing"
    plan = ReproductionPlan(
        hypothesis="Mermaid source remains text instead of a Word drawing",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_drawing_count", "minimum": 1},
        ),
        target_test_selector=selector,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/normalizer.py"),),
    )
    invalid = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "from docx_assertions import assert_minimum_drawing_count\n"
                    f"def {selector}(:\n"
                    "    assert_minimum_drawing_count(b'docx', 1)\n"
                ),
            ),
        ),
        target_test_selector=selector,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="first attempt contains invalid Python",
    )
    outcome, feedback, run, sandbox, test_provider = asyncio.run(
        _run(
            tmp_path,
            [TargetTestOutcome.FAILED],
            generated_outputs=[invalid],
            markdown="graph TD\nA([开始]) --> B([结束])",
            description="Word only contains Mermaid source instead of a drawing",
            gate_classification=GateClassification(
                intent=GateIntent.BUG_REPORT,
                category=GateCategory.DOCX_STRUCTURE,
                relevance=0.99,
                sufficient_information=True,
                injection_suspected=False,
                requires_extension_change=False,
                reason="backend Mermaid drawing regression",
            ),
            reproduction_plan=plan,
        )
    )

    assert outcome.completed is False
    assert feedback is not None and feedback.status is FeedbackStatus.REPAIRING
    assert run is not None and run.status is AgentRunStatus.REPAIRING
    assert run.reproduction is not None
    assert run.reproduction["disposition"] == ReproductionDisposition.REPRODUCED.value
    assert run.reproduction["round"] == 2
    assert len(test_provider.requests) == 1
    assert len(sandbox.jobs) == 1
    patch = sandbox.jobs[0].test_patch
    assert patch is not None
    assert b"assert_minimum_drawing_count" in patch
    assert b"graph TD" in patch


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


def test_stage_e_first_fix_is_independently_validated(tmp_path: Path) -> None:
    outcome, feedback, run, sandbox, fix_provider, artifacts = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
        )
    )

    assert outcome.completed is True
    assert feedback is not None and feedback.status is FeedbackStatus.VALIDATED
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.validation is not None and run.validation["passed"] is True
    assert run.validated_patch_sha256 is not None
    validated = artifacts.path_for(outcome.run_id, "validated.patch").read_bytes()
    assert hashlib.sha256(validated).hexdigest() == run.validated_patch_sha256
    assert len(fix_provider.requests) == 1
    assert [item.job.job_type.value for item in sandbox.jobs] == [
        "reproduce_target",
        "validate_target",
        "reproduce_target",
        "validate_target",
        "validate_full",
    ]


def test_stage_f_publishes_validated_patch_and_persists_pr_url(tmp_path: Path) -> None:
    publisher = _Publisher(PublicationDisposition.PR_OPENED)
    outcome, feedback, run, _, _, artifacts = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
            publisher=publisher,
        )
    )

    assert outcome.completed is True
    assert outcome.pr_url == "https://github.com/example/md-to-word/pull/17"
    assert feedback is not None and feedback.status is FeedbackStatus.PR_OPENED
    assert feedback.pr_url == outcome.pr_url
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.pr_url == outcome.pr_url
    assert len(publisher.requests) == 1
    assert publisher.requests[0].validation.passed is True
    assert publisher.requests[0].validated_patch == artifacts.path_for(
        outcome.run_id,
        "validated.patch",
    ).read_bytes()
    assert artifacts.path_for(outcome.run_id, "publication.json").is_file()


def test_stage_f_stale_base_requeues_feedback_once_without_pr(tmp_path: Path) -> None:
    publisher = _Publisher(PublicationDisposition.STALE_BASE)
    outcome, feedback, run, _, _, artifacts = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
            publisher=publisher,
        )
    )

    assert outcome.completed is False
    assert feedback is not None and feedback.status is FeedbackStatus.PENDING
    assert feedback.stale_requeue_count == 1
    assert feedback.pr_url is None
    assert run is not None and run.status is AgentRunStatus.STALE_BASE
    assert run.error_code == "stale_base"
    assert artifacts.path_for(outcome.run_id, "publication.json").is_file()


def test_stage_g_fake_e2e_publication_failure_preserves_validated_artifacts(
    tmp_path: Path,
) -> None:
    publisher = _FailingPublisher()
    outcome, feedback, run, _, _, artifacts = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
            publisher=publisher,
            capture_publication_error=True,
        )
    )

    assert isinstance(outcome.error, PublicationError)
    assert feedback is not None and feedback.status is FeedbackStatus.FAILED
    assert feedback.last_error_code == "publication_failed"
    assert run is not None and run.status is AgentRunStatus.FAILED
    assert run.error_code == "publication_failed"
    assert run.validated_patch_sha256 is not None
    assert artifacts.path_for(outcome.run_id, "validated.patch").is_file()
    assert len(publisher.requests) == 1


def test_stage_f_explicit_retry_reuses_validation_without_rerunning_sandbox(
    tmp_path: Path,
) -> None:
    async def scenario():
        publisher = _RetryingPublisher()
        capture: dict[str, object] = {}
        first, _, _, sandbox, _, _ = await _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
            publisher=publisher,
            capture_publication_error=True,
            runtime_capture=capture,
        )
        controller = capture["controller"]
        resumed = await controller.resume(first.run_id)
        feedbacks = capture["feedbacks"]
        runs = capture["runs"]
        return (
            publisher,
            resumed,
            await feedbacks.get(FEEDBACK_ID),
            await runs.get(first.run_id),
            sandbox,
        )

    publisher, outcome, feedback, run, sandbox = asyncio.run(scenario())

    assert outcome.completed is True
    assert feedback is not None and feedback.status is FeedbackStatus.PR_OPENED
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.pr_url == "https://github.com/example/md-to-word/pull/17"
    assert publisher.attempts == 2
    assert len(sandbox.jobs) == 5


def test_stage_e_second_fix_uses_fresh_baseline_and_then_validates(tmp_path: Path) -> None:
    _, feedback, run, sandbox, fix_provider, _ = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix(), _fix(strip=False)],
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.VALIDATED
    assert run is not None and run.repair is not None
    assert run.repair["round"] == 2
    assert len(fix_provider.requests) == 2
    assert sandbox.jobs[1].source_archive == sandbox.jobs[2].source_archive


def test_stage_e_two_failed_fixes_stop_without_final_validation(tmp_path: Path) -> None:
    _, feedback, run, sandbox, fix_provider, _ = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.FAILED,
            ],
            fixes=[_fix(), _fix(strip=False)],
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.FAILED
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.repair is not None and run.repair["round"] == 2
    assert len(fix_provider.requests) == 2
    assert len(sandbox.jobs) == 3


def test_stage_e_external_dependency_fix_goes_to_human_without_sandbox(
    tmp_path: Path,
) -> None:
    outcome, feedback, run, sandbox, fix_provider, _ = asyncio.run(
        _run_stage_e(
            tmp_path,
            [TargetTestOutcome.FAILED],
            fixes=[_external_dependency_fix()],
        )
    )

    assert outcome.completed is True
    assert outcome.route is GateRoute.NEEDS_HUMAN
    assert feedback is not None and feedback.status is FeedbackStatus.NEEDS_HUMAN
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.route is GateRoute.NEEDS_HUMAN
    assert run.repair is not None
    assert run.repair["disposition"] == "needs_human"
    assert run.error_code == "external_dependency_required"
    assert len(fix_provider.requests) == 1
    # 只保留阶段 D 的复现 Job，依赖型修复不会进入目标验证。
    assert len(sandbox.jobs) == 1


def test_stage_e_reproduced_mermaid_uses_preinstalled_renderer_fix_path(
    tmp_path: Path,
) -> None:
    selector = "test_feedback_a257a846_mermaid_drawing"
    plan = ReproductionPlan(
        hypothesis="Mermaid source is not rendered as a drawing",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_drawing_count", "minimum": 1},
        ),
        target_test_selector=selector,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(
            SourceReadRequest(path="backend/app/normalizer.py"),
        ),
    )
    generated = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=(
                    "from app.pandoc_runner import convert_markdown_to_docx\n"
                    "from docx_assertions import assert_minimum_drawing_count\n\n\n"
                    f"def {selector}(tmp_path):\n"
                    "    document = convert_markdown_to_docx(\n"
                    "        'graph TD\\nA([start]) --> B([end])', tmp_path\n"
                    "    )\n"
                    "    assert_minimum_drawing_count(document, 1)\n"
                ),
            ),
        ),
        target_test_selector=selector,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="assert that Mermaid produces a Word drawing",
        files_needed_for_fix=("backend/app/normalizer.py",),
    )
    outcome, feedback, run, sandbox, fix_provider, _ = asyncio.run(
        _run_stage_e(
            tmp_path,
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ],
            fixes=[_fix()],
            markdown="graph TD\nA([开始]) --> B([结束])",
            description="Word only contains Mermaid source instead of a drawing",
            gate_classification=GateClassification(
                intent=GateIntent.BUG_REPORT,
                category=GateCategory.DOCX_STRUCTURE,
                relevance=0.99,
                sufficient_information=True,
                injection_suspected=False,
                requires_extension_change=False,
                reason="backend Mermaid drawing regression",
            ),
            reproduction_plan=plan,
            generated_test=generated,
        )
    )

    assert outcome.completed is True
    assert outcome.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert feedback is not None and feedback.status is FeedbackStatus.VALIDATED
    assert run is not None and run.status is AgentRunStatus.COMPLETED
    assert run.repair is not None
    assert run.repair["disposition"] == "target_passed"
    assert run.error_code is None
    assert len(fix_provider.requests) == 1
    assert len(sandbox.jobs) == 5
    repair_context = fix_provider.requests[0].messages[-1].content
    assert "backend/app/mermaid_renderer.py" in repair_context


def test_stage_e_budget_exhaustion_blocks_model_and_sandbox_calls(tmp_path: Path) -> None:
    _, feedback, run, sandbox, fix_provider, _ = asyncio.run(
        _run_stage_e(
            tmp_path,
            [TargetTestOutcome.FAILED],
            fixes=[_fix()],
            # Gate + plan + test 已使用三次模型调用，不允许再生成修复。
            max_model_calls=3,
        )
    )

    assert feedback is not None and feedback.status is FeedbackStatus.FAILED
    assert run is not None and run.status is AgentRunStatus.BUDGET_EXHAUSTED
    assert len(fix_provider.requests) == 0
    assert len(sandbox.jobs) == 1


def test_stage_d_terminal_checkpoint_can_resume_into_stage_e(tmp_path: Path) -> None:
    async def scenario():
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
        sandbox = _Sandbox(
            [
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.FAILED,
                TargetTestOutcome.PASSED,
                TargetTestOutcome.PASSED,
            ]
        )
        checkpointer = MemorySaver()
        reproduction = ReproductionDependencies(
            plan_provider=FakeModelProvider([_plan()]),
            test_provider=FakeModelProvider([_generated()]),
            source_workspace=source,
            edit_tools=StructuredEditTools(
                PatchBuilder(PatchPolicy.load_default()),
                artifacts,
            ),
            sandbox_client=sandbox,
        )
        stage_d = GateController(
            feedback_repository=feedbacks,
            run_repository=runs,
            provider=FakeModelProvider([_classification()]),
            artifact_store=artifacts,
            checkpointer=checkpointer,
            reproduction=reproduction,
        )
        first = await stage_d.start(feedback)
        fix_provider = FakeModelProvider([_fix()])
        stage_e = GateController(
            feedback_repository=feedbacks,
            run_repository=runs,
            provider=FakeModelProvider([]),
            artifact_store=artifacts,
            checkpointer=checkpointer,
            reproduction=ReproductionDependencies(
                plan_provider=FakeModelProvider([]),
                test_provider=FakeModelProvider([]),
                source_workspace=source,
                edit_tools=reproduction.edit_tools,
                sandbox_client=sandbox,
            ),
            repair=RepairDependencies(fix_provider=fix_provider),
        )
        resumed = await stage_e.resume(first.run_id)
        return resumed, await feedbacks.get(FEEDBACK_ID), fix_provider, sandbox

    outcome, feedback, fix_provider, sandbox = asyncio.run(scenario())

    assert outcome.completed is True
    assert feedback is not None and feedback.status is FeedbackStatus.VALIDATED
    assert len(fix_provider.requests) == 1
    assert len(sandbox.jobs) == 5
