import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.domain.repair import (
    RepairDisposition,
    ValidationResult,
    build_validation_result,
    classify_target_validation,
)
from agent.domain.reproduction import ExpectedFailureKind
from agent.sandbox.contracts import (
    JUnitSummary,
    SandboxResult,
    SandboxStatus,
    TargetTestOutcome,
)


def _result(
    outcome: TargetTestOutcome,
    *,
    tests: int = 1,
    failures: int | None = None,
    errors: int = 0,
    skipped: int = 0,
    status: SandboxStatus = SandboxStatus.COMPLETED,
    exit_code: int | None = None,
) -> SandboxResult:
    now = datetime.now(UTC)
    failure_count = int(outcome is TargetTestOutcome.FAILED) if failures is None else failures
    return SandboxResult(
        job_id=uuid4(),
        status=status,
        exit_code=(0 if failure_count == 0 and errors == 0 else 1)
        if exit_code is None
        else exit_code,
        started_at=now,
        finished_at=now,
        duration_ms=1,
        docx_summary={"passed": outcome is TargetTestOutcome.PASSED},
        junit_summary=JUnitSummary(
            tests=tests,
            failures=failure_count,
            errors=errors,
            skipped=skipped,
            target_collected=outcome is not TargetTestOutcome.NOT_COLLECTED,
            target_outcome=outcome,
            target_failure_type=(
                "AssertionError" if outcome is TargetTestOutcome.FAILED else None
            ),
            target_message="trusted DOCX assertion failed",
        ),
    )


def _validation(**overrides: object) -> ValidationResult:
    values = {
        "base_sha": "a" * 40,
        "source_snapshot_sha256": "b" * 64,
        "test_patch_sha256": "c" * 64,
        "fix_patch_sha256": "d" * 64,
        "target_test_selector": "test_feedback_ab12cd34_table",
        "expected_failure_kind": ExpectedFailureKind.ASSERTION,
        "trusted_docx_check": "minimum_table_count",
        "baseline_result": _result(TargetTestOutcome.FAILED),
        "target_result": _result(TargetTestOutcome.PASSED),
        "full_result": _result(TargetTestOutcome.PASSED, tests=45),
        "baseline_skipped": 0,
        "changed_files": (
            "backend/app/normalizer.py",
            "backend/tests/test_feedback_regressions.py",
        ),
        "validated_patch_ref": "artifact://run/validated.patch",
        "validated_patch_sha256": hashlib.sha256(b"validated").hexdigest(),
    }
    values.update(overrides)
    return build_validation_result(**values)


def test_target_validation_accepts_only_collected_passing_target() -> None:
    report = classify_target_validation(
        _result(TargetTestOutcome.PASSED),
        round_number=1,
    )

    assert report.disposition is RepairDisposition.TARGET_PASSED


def test_target_validation_failure_is_bounded_for_revision() -> None:
    report = classify_target_validation(
        _result(TargetTestOutcome.FAILED),
        round_number=2,
    )

    assert report.disposition is RepairDisposition.TARGET_FAILED
    assert report.failure_code == "target_validation_failed"
    assert report.failure_summary == "trusted DOCX assertion failed"


def test_independent_validation_requires_baseline_failure_and_fixed_pass() -> None:
    result = _validation()

    assert result.passed is True
    assert result.baseline_reproduction.expected_failure_observed is True
    assert result.target_validation.passed is True
    assert result.full_validation.tests == 45
    assert result.docx_validation.checks == {"minimum_table_count": True}


def test_full_regression_fails_even_when_target_passes() -> None:
    result = _validation(
        full_result=_result(
            TargetTestOutcome.PASSED,
            tests=45,
            failures=1,
            exit_code=1,
        )
    )

    assert result.passed is False
    assert result.target_validation.passed is True
    assert result.failure_code == "full_validation_failed"


def test_increased_skipped_count_fails_validation() -> None:
    result = _validation(
        full_result=_result(
            TargetTestOutcome.PASSED,
            tests=45,
            skipped=1,
        )
    )

    assert result.passed is False
    assert result.failure_code == "skipped_tests_increased"
    assert result.full_validation.skipped == 1


@pytest.mark.parametrize(
    "outcome",
    [TargetTestOutcome.FAILED, TargetTestOutcome.ERROR, TargetTestOutcome.SKIPPED],
)
def test_docx_oracle_failure_never_validates(outcome: TargetTestOutcome) -> None:
    result = _validation(
        target_result=_result(outcome),
        full_result=_result(outcome, tests=45),
    )

    assert result.passed is False
    assert result.docx_validation.passed is False


def test_validation_schema_rejects_invented_passed_flag() -> None:
    payload = _validation().model_dump()
    payload["passed"] = False
    payload["failure_code"] = "invented"

    with pytest.raises(ValidationError, match="passed flag"):
        ValidationResult.model_validate(payload)
