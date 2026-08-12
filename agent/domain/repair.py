"""阶段 E 修复生成与独立验证使用的严格领域契约。"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.domain.enums import RiskLevel
from agent.domain.models import TaskArtifact
from agent.domain.reproduction import (
    ExpectedFailureKind,
    ReproductionDisposition,
    ReproductionPlan,
    classify_reproduction_result,
)
from agent.sandbox.contracts import SandboxResult, SandboxStatus, TargetTestOutcome
from agent.workspace.edits import Edit


class FixGenerationResult(BaseModel):
    """模型只能返回结构化源码编辑和审查摘要，不能直接提交 diff。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edits: tuple[Edit, ...] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=1, max_length=1000)
    behavior_changes: tuple[
        Annotated[str, Field(min_length=1, max_length=500)], ...
    ] = Field(default=(), max_length=10)
    risk_level: Literal[RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
    manual_review_notes: tuple[
        Annotated[str, Field(min_length=1, max_length=500)], ...
    ] = Field(default=(), max_length=10)
    extension_sync_required: bool = False


class RepairDisposition(StrEnum):
    TARGET_PASSED = "target_passed"
    TARGET_FAILED = "target_failed"
    INVALID_RESULT = "invalid_result"
    NEEDS_HUMAN = "needs_human"
    SECURITY_REJECTED = "security_rejected"


class RepairReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: RepairDisposition
    round: int = Field(ge=1, le=2)
    failure_code: str | None = Field(default=None, max_length=80)
    failure_summary: str | None = Field(default=None, max_length=4096)


class RepairAttemptArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    round: int = Field(ge=1, le=2)
    fix_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_files: tuple[str, ...] = ()
    fix_summary: str = Field(default="", max_length=1000)
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    extension_sync_required: bool = False
    sandbox_result: SandboxResult
    report: RepairReport | None = None


class BaselineReproductionValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executed: bool
    expected_failure_observed: bool


class TargetValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool


class FullValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    tests: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)
    baseline_skipped: int = Field(ge=0)


class DocxValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: dict[str, bool]


class ValidationResult(BaseModel):
    """Publisher 后续唯一可接受的、由 Controller 计算的验证凭据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fix_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_test_selector: str = Field(pattern=r"^[a-z0-9_]{1,80}$")
    baseline_reproduction: BaselineReproductionValidation
    target_validation: TargetValidation
    full_validation: FullValidation
    docx_validation: DocxValidation
    changed_files: tuple[str, ...]
    validated_patch_ref: str
    validated_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_code: str | None = Field(default=None, max_length=80)
    failure_summary: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_consistent_pass_state(self) -> "ValidationResult":
        computed = (
            self.baseline_reproduction.executed
            and self.baseline_reproduction.expected_failure_observed
            and self.target_validation.passed
            and self.full_validation.passed
            and self.docx_validation.passed
        )
        if self.passed != computed:
            raise ValueError("validation passed flag does not match sub-results")
        if self.passed and (self.failure_code is not None or self.failure_summary is not None):
            raise ValueError("passed validation cannot contain failure details")
        if not self.passed and self.failure_code is None:
            raise ValueError("failed validation requires a failure code")
        return self


def classify_target_validation(
    result: SandboxResult,
    *,
    round_number: int,
) -> RepairReport:
    """只依赖受信 JUnit 摘要判断修复后的目标测试，绝不解析 stdout。"""

    if result.status is SandboxStatus.SECURITY_REJECTED:
        return RepairReport(
            disposition=RepairDisposition.SECURITY_REJECTED,
            round=round_number,
            failure_code=result.error_code or "sandbox_security_rejected",
            failure_summary="sandbox rejected the authorized repair workspace",
        )
    summary = result.junit_summary
    if (
        result.status is not SandboxStatus.COMPLETED
        or result.timed_out
        or summary is None
        or not summary.target_collected
    ):
        return RepairReport(
            disposition=RepairDisposition.INVALID_RESULT,
            round=round_number,
            failure_code=(
                "target_validation_timeout" if result.timed_out else "invalid_target_result"
            ),
            failure_summary="target validation did not produce a trusted JUnit result",
        )
    if (
        summary.target_outcome is TargetTestOutcome.PASSED
        and summary.failures == 0
        and summary.errors == 0
        and summary.skipped == 0
    ):
        return RepairReport(
            disposition=RepairDisposition.TARGET_PASSED,
            round=round_number,
        )
    return RepairReport(
        disposition=RepairDisposition.TARGET_FAILED,
        round=round_number,
        failure_code="target_validation_failed",
        failure_summary=_bounded_failure_summary(summary.target_message),
    )


def build_validation_result(
    *,
    base_sha: str,
    source_snapshot_sha256: str,
    test_patch_sha256: str,
    fix_patch_sha256: str,
    target_test_selector: str,
    expected_failure_kind: ExpectedFailureKind,
    trusted_docx_check: str,
    baseline_result: SandboxResult,
    target_result: SandboxResult,
    full_result: SandboxResult,
    baseline_skipped: int,
    changed_files: tuple[str, ...],
    validated_patch_ref: str,
    validated_patch_sha256: str,
) -> ValidationResult:
    """按固定优先级汇总三个全新沙箱结果，模型不能提供 passed。"""

    baseline_report = classify_reproduction_result(
        baseline_result,
        expected_failure_kind=expected_failure_kind,
        round_number=1,
        target_test_selector=target_test_selector,
    )
    baseline_ok = baseline_report.disposition is ReproductionDisposition.REPRODUCED
    target_report = classify_target_validation(target_result, round_number=1)
    target_ok = target_report.disposition is RepairDisposition.TARGET_PASSED

    full_summary = full_result.junit_summary
    full_ok = (
        full_result.status is SandboxStatus.COMPLETED
        and not full_result.timed_out
        and full_result.exit_code == 0
        and full_summary is not None
        and full_summary.failures == 0
        and full_summary.errors == 0
        and full_summary.skipped <= baseline_skipped
        and full_summary.target_collected
        and full_summary.target_outcome is TargetTestOutcome.PASSED
    )
    # DOCX Oracle 由受信回归测试执行；全量 JUnit 必须再次证明该目标已收集并通过。
    docx_ok = bool(
        full_summary is not None
        and full_summary.target_collected
        and full_summary.target_outcome is TargetTestOutcome.PASSED
        and full_result.docx_summary.get("passed") is True
    )

    failure_code: str | None = None
    failure_summary: str | None = None
    if not baseline_ok:
        failure_code = "baseline_reproduction_failed"
        failure_summary = "independent validation did not reproduce the baseline failure"
    elif not target_ok:
        failure_code = target_report.failure_code or "target_validation_failed"
        failure_summary = target_report.failure_summary or "target validation failed"
    elif full_summary is not None and full_summary.skipped > baseline_skipped:
        failure_code = "skipped_tests_increased"
        failure_summary = "full validation increased the baseline skipped test count"
    elif not full_ok:
        failure_code = "full_validation_failed"
        failure_summary = "full backend validation failed"
    elif not docx_ok:
        failure_code = "docx_validation_failed"
        failure_summary = "trusted DOCX validation failed"

    passed = baseline_ok and target_ok and full_ok and docx_ok
    return ValidationResult(
        passed=passed,
        base_sha=base_sha,
        source_snapshot_sha256=source_snapshot_sha256,
        test_patch_sha256=test_patch_sha256,
        fix_patch_sha256=fix_patch_sha256,
        target_test_selector=target_test_selector,
        baseline_reproduction=BaselineReproductionValidation(
            executed=baseline_result.status is SandboxStatus.COMPLETED,
            expected_failure_observed=baseline_ok,
        ),
        target_validation=TargetValidation(passed=target_ok),
        full_validation=FullValidation(
            passed=full_ok,
            tests=full_summary.tests if full_summary else 0,
            failures=full_summary.failures if full_summary else 0,
            errors=full_summary.errors if full_summary else 0,
            skipped=full_summary.skipped if full_summary else 0,
            baseline_skipped=baseline_skipped,
        ),
        docx_validation=DocxValidation(
            passed=docx_ok,
            checks={trusted_docx_check: docx_ok},
        ),
        changed_files=changed_files,
        validated_patch_ref=validated_patch_ref,
        validated_patch_sha256=validated_patch_sha256,
        failure_code=failure_code,
        failure_summary=failure_summary,
    )


def _bounded_failure_summary(value: str) -> str:
    text = value.strip() or "target test failed"
    return text[:4096]
