import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agent.domain.enums import FeedbackType
from agent.domain.models import TaskArtifact
from agent.domain.reproduction import (
    ExpectedFailureKind,
    OracleKind,
    OracleSpec,
    ReproductionDisposition,
    ReproductionPlan,
    SourceReadRequest,
    TestGenerationResult as GeneratedTestResult,
    classify_reproduction_result,
)
from agent.providers.fake import FakeModelProvider
from agent.reproduction import plan_reproduction
from agent.sandbox.contracts import (
    JUnitSummary,
    SandboxResult,
    SandboxStatus,
    TargetTestOutcome,
)
from agent.workspace.edits import Edit, EditMode


FEEDBACK_ID = UUID("a257a846-1728-4d39-81bf-75a388041215")
SELECTOR = "test_feedback_a257a846_table_structure"


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        hypothesis="table output loses one row",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_table_count", "minimum": 3},
        ),
        target_test_selector=SELECTOR,
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/normalizer.py"),),
    )


def _result(summary: JUnitSummary, *, status: SandboxStatus = SandboxStatus.COMPLETED):
    now = datetime.now(UTC)
    return SandboxResult(
        job_id=uuid4(),
        status=status,
        exit_code=1,
        started_at=now,
        finished_at=now,
        duration_ms=10,
        junit_summary=summary,
    )


def test_generation_must_match_plan_and_fixed_feedback_prefix() -> None:
    plan = _plan()
    generated = GeneratedTestResult(
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
        reason="deterministic table assertion",
    )

    generated.validate_against(FEEDBACK_ID, plan)

    with pytest.raises(ValueError, match="feedback id prefix"):
        plan.validate_feedback_identity(uuid4())

    plan.validate_source_paths(("backend/app/normalizer.py",))
    with pytest.raises(ValueError, match="unavailable source path"):
        plan.validate_source_paths(("backend/app/pandoc_runner.py",))
    with pytest.raises(ValidationError, match="at least 20 lines"):
        SourceReadRequest(
            path="backend/app/pandoc_runner.py",
            start_line=1,
            end_line=1,
        )


def test_oracle_rejects_model_supplied_xpath_or_code() -> None:
    with pytest.raises(ValidationError, match="executable expressions"):
        OracleSpec(kind=OracleKind.DOCX_XPATH, parameters={"xpath": "//w:tbl"})


def test_oracle_rejects_unrelated_or_missing_registered_parameters() -> None:
    with pytest.raises(ValidationError, match="requires minimum"):
        OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_math_count"},
        )
    with pytest.raises(ValidationError, match="unrelated parameters"):
        OracleSpec(
            kind=OracleKind.TEXT_ABSENT,
            parameters={"text": "graph TD", "style": "Heading 1"},
        )


def test_mermaid_plan_requires_drawing_oracle_and_gets_one_correction() -> None:
    task = TaskArtifact(
        feedback_id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content="graph TD\nA --> B",
        description="exported Word keeps Mermaid source instead of a diagram",
        content_fingerprint="a" * 64,
    )
    invalid = ReproductionPlan(
        hypothesis="DOCX contains raw Mermaid source",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "required_parts_present"},
        ),
        target_test_selector="test_feedback_a257a846_mermaid_drawing",
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/pandoc_runner.py"),),
    )
    corrected = invalid.model_copy(
        update={
            "oracle": OracleSpec(
                kind=OracleKind.DOCX_XPATH,
                parameters={"validator": "minimum_drawing_count", "minimum": 1},
            )
        }
    )
    provider = FakeModelProvider([invalid, corrected])

    execution = asyncio.run(
        plan_reproduction(
            task,
            category="docx_structure",
            allowed_source_paths=("backend/app/pandoc_runner.py",),
            provider=provider,
        )
    )

    assert execution.output == corrected
    assert execution.model_calls == 2
    assert len(provider.requests) == 2
    correction = provider.requests[1].messages[-1].content
    assert "minimum_drawing_count" in correction
    assert invalid.hypothesis not in correction


def test_generated_fix_hints_only_reference_backend_fix_allowlist() -> None:
    plan = _plan()
    with pytest.raises(ValidationError, match="fix source path is invalid"):
        GeneratedTestResult(
            edits=(
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.FULL_FILE,
                    content="def test_feedback_a257a846_table_structure(): pass\n",
                ),
            ),
            target_test_selector=SELECTOR,
            oracle=plan.oracle,
            expected_failure_kind=plan.expected_failure_kind,
            reason="invalid fix hint",
            files_needed_for_fix=("backend/tests/test_pandoc_runner.py",),
        )


def test_target_assertion_failure_is_reproduced() -> None:
    report = classify_reproduction_result(
        _result(
            JUnitSummary(
                tests=1,
                failures=1,
                errors=0,
                skipped=0,
                target_collected=True,
                target_outcome=TargetTestOutcome.FAILED,
                target_failure_type="AssertionError",
                target_message="expected three rows",
            )
        ),
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        round_number=1,
        target_test_selector=SELECTOR,
    )

    assert report.disposition is ReproductionDisposition.REPRODUCED


@pytest.mark.parametrize(
    ("outcome", "failure_type", "expected_disposition"),
    [
        (TargetTestOutcome.PASSED, None, ReproductionDisposition.NOT_REPRODUCED),
        (TargetTestOutcome.ERROR, "ImportError", ReproductionDisposition.INVALID_TEST),
        (TargetTestOutcome.ERROR, "SyntaxError", ReproductionDisposition.INVALID_TEST),
        (TargetTestOutcome.ERROR, "FixtureLookupError", ReproductionDisposition.INVALID_TEST),
        (TargetTestOutcome.SKIPPED, None, ReproductionDisposition.INVALID_TEST),
    ],
)
def test_non_target_failures_are_not_reproduction(
    outcome: TargetTestOutcome,
    failure_type: str | None,
    expected_disposition: ReproductionDisposition,
) -> None:
    report = classify_reproduction_result(
        _result(
            JUnitSummary(
                tests=1,
                failures=int(outcome is TargetTestOutcome.FAILED),
                errors=int(outcome is TargetTestOutcome.ERROR),
                skipped=int(outcome is TargetTestOutcome.SKIPPED),
                target_collected=True,
                target_outcome=outcome,
                target_failure_type=failure_type,
            )
        ),
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        round_number=1,
        target_test_selector=SELECTOR,
    )

    assert report.disposition is expected_disposition


def test_security_rejection_is_terminal_classification() -> None:
    report = classify_reproduction_result(
        _result(
            JUnitSummary(tests=0, failures=0, errors=0, skipped=0),
            status=SandboxStatus.SECURITY_REJECTED,
        ),
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        round_number=1,
        target_test_selector=SELECTOR,
    )
    assert report.disposition is ReproductionDisposition.SECURITY_REJECTED


@pytest.mark.parametrize(
    ("failure_type", "expected"),
    [
        ("app.pandoc_runner.ConversionError", ReproductionDisposition.REPRODUCED),
        ("NameError", ReproductionDisposition.INVALID_TEST),
    ],
)
def test_unexpected_conversion_error_requires_conversion_error_type(
    failure_type: str,
    expected: ReproductionDisposition,
) -> None:
    report = classify_reproduction_result(
        _result(
            JUnitSummary(
                tests=1,
                failures=0,
                errors=1,
                skipped=0,
                target_collected=True,
                target_outcome=TargetTestOutcome.ERROR,
                target_failure_type=failure_type,
            )
        ),
        expected_failure_kind=ExpectedFailureKind.UNEXPECTED_CONVERSION_ERROR,
        round_number=1,
        target_test_selector=SELECTOR,
    )
    assert report.disposition is expected
