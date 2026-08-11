"""阶段 D 自动复现使用的严格领域契约与结果分类。"""

from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.sandbox.contracts import SandboxResult, SandboxStatus, TargetTestOutcome
from agent.domain.models import TaskArtifact
from agent.workspace.edits import Edit


class OracleKind(StrEnum):
    CONVERSION_SUCCESS = "conversion_success"
    CONVERSION_ERROR = "conversion_error"
    DOCX_XPATH = "docx_xpath"
    TEXT_ABSENT = "text_absent"
    STYLE_PRESENT = "style_present"


class ExpectedFailureKind(StrEnum):
    ASSERTION = "assertion"
    UNEXPECTED_CONVERSION_ERROR = "unexpected_conversion_error"


class OracleParameters(BaseModel):
    """固定参数键让兼容接口可生成严格 JSON Schema，未使用的键保持 null。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validator: Literal[
        "valid_zip",
        "required_parts_present",
        "xml_parseable",
        "minimum_table_count",
        "minimum_math_count",
        "minimum_drawing_count",
        "three_line_table_structure",
    ] | None = None
    minimum: int | None = Field(default=None, ge=1, le=1000)
    text: str | None = Field(default=None, min_length=1, max_length=500)
    style: str | None = Field(default=None, min_length=1, max_length=200)


class ReproductionDisposition(StrEnum):
    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    INVALID_TEST = "invalid_test"
    BASELINE_REGRESSION = "baseline_regression"
    SECURITY_REJECTED = "security_rejected"


class OracleSpec(BaseModel):
    """模型只能选择已登记 Oracle 和数据参数，不能提交 XPath 或可执行代码。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OracleKind
    parameters: OracleParameters = Field(default_factory=OracleParameters)

    @field_validator("parameters", mode="before")
    @classmethod
    def validate_parameters(
        cls,
        value: object,
    ) -> object:
        # docx_xpath 是历史契约名称；真正执行的是登记断言，禁止模型携带 XPath 表达式。
        forbidden = {"xpath", "code", "command", "python"}
        if isinstance(value, dict) and forbidden.intersection(value):
            raise ValueError("oracle cannot contain executable expressions")
        return value

    @model_validator(mode="after")
    def validate_registered_assertion(self) -> "OracleSpec":
        parameters = self.parameters
        if self.kind is OracleKind.DOCX_XPATH:
            if parameters.validator is None:
                raise ValueError("docx oracle validator is not registered")
            count_validators = {
                "minimum_table_count",
                "minimum_math_count",
                "minimum_drawing_count",
            }
            if parameters.validator in count_validators and parameters.minimum is None:
                raise ValueError("count validator requires minimum")
            if parameters.validator not in count_validators and parameters.minimum is not None:
                raise ValueError("minimum is only valid for count validators")
            if parameters.text is not None or parameters.style is not None:
                raise ValueError("docx oracle contains unrelated parameters")
        elif self.kind is OracleKind.TEXT_ABSENT:
            if parameters.text is None:
                raise ValueError("text_absent oracle requires text")
            if any(
                item is not None
                for item in (parameters.validator, parameters.minimum, parameters.style)
            ):
                raise ValueError("text_absent oracle contains unrelated parameters")
        elif self.kind is OracleKind.STYLE_PRESENT:
            if parameters.style is None:
                raise ValueError("style_present oracle requires style")
            if any(
                item is not None
                for item in (parameters.validator, parameters.minimum, parameters.text)
            ):
                raise ValueError("style_present oracle contains unrelated parameters")
        elif any(
            item is not None
            for item in (
                parameters.validator,
                parameters.minimum,
                parameters.text,
                parameters.style,
            )
        ):
            raise ValueError("conversion oracle does not accept parameters")
        return self

    def trusted_assertion_name(self) -> str | None:
        if self.kind is OracleKind.DOCX_XPATH:
            return "assert_" + str(self.parameters.validator)
        if self.kind is OracleKind.TEXT_ABSENT:
            return "assert_text_absent"
        if self.kind is OracleKind.STYLE_PRESENT:
            return "assert_paragraph_style_present"
        return None


class SourceReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=240)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=240, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_range_and_path(self) -> "SourceReadRequest":
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or self.end_line < self.start_line:
            raise ValueError("source read request is invalid")
        if self.end_line - self.start_line + 1 < 20:
            raise ValueError("source read request must include at least 20 lines")
        return self


class ReproductionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis: str = Field(min_length=1, max_length=600)
    oracle: OracleSpec
    target_test_selector: str = Field(
        pattern=r"^test_feedback_[0-9a-f]{8}_[a-z0-9_]{1,48}$"
    )
    expected_failure_kind: ExpectedFailureKind
    files_to_read: tuple[SourceReadRequest, ...] = Field(min_length=1, max_length=8)
    extension_sync_possible: bool = False

    def validate_feedback_identity(self, feedback_id: UUID) -> None:
        expected_prefix = f"test_feedback_{feedback_id.hex[:8]}_"
        if not self.target_test_selector.startswith(expected_prefix):
            raise ValueError("target test selector does not match feedback id prefix")

    def validate_source_paths(self, allowed_paths: tuple[str, ...]) -> None:
        allowed = frozenset(allowed_paths)
        if any(item.path not in allowed for item in self.files_to_read):
            raise ValueError("reproduction plan requested unavailable source path")

    def validate_task_oracle(self, task: TaskArtifact) -> None:
        """Mermaid 复现必须证明 DOCX 中出现图形，不能用通用 ZIP 完整性代替。"""

        if not _contains_mermaid_diagram(task.markdown_content):
            return
        if (
            self.oracle.kind is not OracleKind.DOCX_XPATH
            or self.oracle.parameters.validator != "minimum_drawing_count"
            or self.oracle.parameters.minimum is None
        ):
            raise ValueError(
                "mermaid feedback requires minimum_drawing_count oracle"
            )


class TestGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edits: tuple[Edit, ...] = Field(min_length=1, max_length=5)
    target_test_selector: str = Field(
        pattern=r"^test_feedback_[0-9a-f]{8}_[a-z0-9_]{1,48}$"
    )
    oracle: OracleSpec
    expected_failure_kind: ExpectedFailureKind
    reason: str = Field(min_length=1, max_length=400)
    files_needed_for_fix: tuple[str, ...] = Field(default=(), max_length=8)
    extension_sync_required: bool = False

    @field_validator("files_needed_for_fix")
    @classmethod
    def validate_fix_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {
            "backend/app/normalizer.py",
            "backend/app/pandoc_runner.py",
        }
        for raw in value:
            path = PurePosixPath(raw)
            if (
                path.is_absolute()
                or ".." in path.parts
                or len(raw) > 240
                or raw not in allowed
            ):
                raise ValueError("fix source path is invalid")
        return value

    def validate_against(self, feedback_id: UUID, plan: ReproductionPlan) -> None:
        plan.validate_feedback_identity(feedback_id)
        if self.target_test_selector != plan.target_test_selector:
            raise ValueError("generated test selector differs from reproduction plan")
        if self.oracle != plan.oracle:
            raise ValueError("generated test oracle differs from reproduction plan")
        if self.expected_failure_kind is not plan.expected_failure_kind:
            raise ValueError("generated failure kind differs from reproduction plan")
        if self.extension_sync_required:
            raise ValueError("backend reproduction cannot require extension changes")
        expected_path = "backend/tests/test_feedback_regressions.py"
        if not any(edit.path == expected_path for edit in self.edits):
            raise ValueError("generated test must edit the fixed regression test file")
        assertion = self.oracle.trusted_assertion_name()
        if assertion is not None:
            generated_text = "\n".join(
                item
                for edit in self.edits
                for item in (edit.content, edit.replace)
                if item is not None
            )
            if "docx_assertions" not in generated_text or assertion not in generated_text:
                raise ValueError("DOCX oracle must use its registered trusted assertion")


class ReproductionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: ReproductionDisposition
    round: int = Field(ge=1, le=2)
    target_test_selector: str
    expected_failure_kind: ExpectedFailureKind
    failure_code: str | None = Field(default=None, max_length=80)
    failure_summary: str = Field(default="", max_length=1000)


class ReproductionAttemptArtifact(BaseModel):
    """单轮沙箱原始结构化结果及 Controller 的独立判定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    round: int = Field(ge=1, le=2)
    test_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_result: SandboxResult
    report: ReproductionReport | None = None


def _contains_mermaid_diagram(markdown: str) -> bool:
    return re.search(
        r"(?im)^\s*(?:graph|flowchart)\s+(?:tb|td|bt|rl|lr)\b",
        markdown,
    ) is not None


_INFRASTRUCTURE_FAILURE_MARKERS = (
    "importerror",
    "modulenotfounderror",
    "syntaxerror",
    "fixture",
    "pytestinternalerror",
    "collectionerror",
)


def classify_reproduction_result(
    result: SandboxResult,
    *,
    expected_failure_kind: ExpectedFailureKind,
    round_number: int,
    target_test_selector: str,
) -> ReproductionReport:
    """只接受目标测试的预期失败，基础设施与非目标失败都不能算复现。"""

    disposition = ReproductionDisposition.INVALID_TEST
    code: str | None = result.error_code
    summary = "sandbox result is not a valid target failure"
    junit = result.junit_summary

    if result.status is SandboxStatus.SECURITY_REJECTED:
        disposition = ReproductionDisposition.SECURITY_REJECTED
        code = code or "sandbox_security_rejected"
    elif result.timed_out or result.status is SandboxStatus.TIMED_OUT:
        code = code or "sandbox_timeout"
        summary = "sandbox execution timed out"
    elif result.status is not SandboxStatus.COMPLETED or junit is None:
        code = code or "missing_junit"
    elif not junit.target_collected or junit.target_outcome is TargetTestOutcome.NOT_COLLECTED:
        code = "target_not_collected"
        summary = "target test was not collected"
    elif junit.target_outcome is TargetTestOutcome.PASSED:
        if junit.failures or junit.errors:
            disposition = ReproductionDisposition.BASELINE_REGRESSION
            code = "non_target_failure"
            summary = "a non-target test failed"
        else:
            disposition = ReproductionDisposition.NOT_REPRODUCED
            code = "target_passed"
            summary = "target test passed on the baseline"
    elif junit.target_outcome is TargetTestOutcome.SKIPPED:
        code = "target_skipped"
        summary = "target test was skipped"
    else:
        failure_text = " ".join(
            item
            for item in (junit.target_failure_type, junit.target_message)
            if item
        ).lower()
        if any(marker in failure_text for marker in _INFRASTRUCTURE_FAILURE_MARKERS):
            code = "invalid_test_infrastructure"
            summary = "target test failed because its test infrastructure is invalid"
        elif expected_failure_kind is ExpectedFailureKind.ASSERTION:
            if (
                junit.target_outcome is TargetTestOutcome.FAILED
                and "assert" in failure_text
            ):
                disposition = ReproductionDisposition.REPRODUCED
                code = "target_assertion_failure"
                summary = "target test produced the planned assertion failure"
            else:
                code = "unexpected_target_error"
                summary = "target test failed outside the planned assertion"
        elif (
            junit.target_outcome in {
                TargetTestOutcome.FAILED,
                TargetTestOutcome.ERROR,
            }
            and "conversionerror" in failure_text
        ):
            disposition = ReproductionDisposition.REPRODUCED
            code = "target_conversion_error"
            summary = "target test produced the planned conversion error"
        else:
            code = "unexpected_target_error"
            summary = "target test did not fail with ConversionError"

    return ReproductionReport(
        disposition=disposition,
        round=round_number,
        target_test_selector=target_test_selector,
        expected_failure_kind=expected_failure_kind,
        failure_code=code,
        failure_summary=summary,
    )
