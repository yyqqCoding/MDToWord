import asyncio
import json
from datetime import UTC, datetime
from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agent.domain.enums import FeedbackType
from agent.domain.models import TaskArtifact
from agent.domain.reproduction import (
    ExpectedFailureKind,
    FIX_SOURCE_PATHS,
    OracleKind,
    OracleSpec,
    ReproductionDisposition,
    ReproductionPlan,
    ReproductionReport,
    SourceReadRequest,
    TestGenerationResult as GeneratedTestResult,
    classify_reproduction_result,
)
from agent.providers.fake import FakeModelProvider
from agent.reproduction import (
    build_conversion_error_test_fallback,
    build_mermaid_test_fallback,
    generate_reproduction_test,
    plan_reproduction,
)
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


def test_existing_regression_file_requires_an_append_edit_and_gets_one_correction() -> None:
    task = TaskArtifact(
        feedback_id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content="$x$",
        description="formula conversion raises an unexpected error",
        content_fingerprint="a" * 64,
    )
    plan = _plan()
    generated_test = (
        "from docx_assertions import assert_minimum_table_count\n\n"
        f"def {SELECTOR}():\n"
        "    assert_minimum_table_count(b'docx', 3)\n"
    )
    unsafe_replacement = GeneratedTestResult(
        edits=(
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.FULL_FILE,
                content=generated_test,
            ),
        ),
        target_test_selector=SELECTOR,
        oracle=plan.oracle,
        expected_failure_kind=plan.expected_failure_kind,
        reason="replace the existing regression file",
    )
    existing_source = "def test_existing():\n    assert True\n"
    append_anchor = "    assert True\n"
    corrected = unsafe_replacement.model_copy(
        update={
            "edits": (
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.SEARCH_REPLACE,
                    search=append_anchor,
                    replace=append_anchor + "\n\n" + generated_test,
                ),
            )
        }
    )
    provider = FakeModelProvider([unsafe_replacement, corrected])

    execution = asyncio.run(
        generate_reproduction_test(
            task,
            plan=plan,
            source_files=(),
            previous_report=None,
            existing_test_source=existing_source,
            provider=provider,
        )
    )

    assert execution.output == corrected
    assert execution.model_calls == 2
    assert len(provider.requests) == 2
    request_context = provider.requests[0].messages[-1].content
    assert '"file_has_content": true' in request_context
    assert '"append_anchor": "    assert True\\n"' in request_context
    correction = provider.requests[1].messages[-1].content
    assert "mode=search_replace" in correction
    assert "append_anchor" in correction


def test_generated_fix_hints_only_reference_backend_fix_allowlist() -> None:
    plan = _plan()
    # 消息必须点名白名单：它会进入格式修正提示，模型据此改正
    with pytest.raises(ValidationError, match="backend/app/pandoc_runner.py"):
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


def test_mermaid_invalid_edit_gets_deterministic_trusted_test_fallback() -> None:
    task = TaskArtifact(
        feedback_id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content="graph TD\nA([开始]) --> B([结束])",
        description="Word only contains Mermaid source",
        content_fingerprint="a" * 64,
    )
    plan = ReproductionPlan(
        hypothesis="Mermaid source is not rendered as a drawing",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_drawing_count", "minimum": 1},
        ),
        target_test_selector="test_feedback_a257a846_mermaid_drawing",
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(
            SourceReadRequest(path="backend/app/pandoc_runner.py"),
            SourceReadRequest(path="backend/app/normalizer.py"),
        ),
    )
    previous = ReproductionReport(
        disposition=ReproductionDisposition.INVALID_TEST,
        round=1,
        target_test_selector=plan.target_test_selector,
        expected_failure_kind=plan.expected_failure_kind,
        failure_code="invalid_test_edit",
        failure_summary="generated test edit is invalid",
    )

    generated = build_mermaid_test_fallback(
        task,
        plan=plan,
        previous_report=previous,
        existing_test_source="# existing regression\n",
    )

    assert generated is not None
    assert generated.files_needed_for_fix == (
        "backend/app/pandoc_runner.py",
        "backend/app/normalizer.py",
    )
    test_edit, fixture_edit = generated.edits
    assert test_edit.content is not None
    assert test_edit.content.startswith("# existing regression\n")
    assert "assert_minimum_drawing_count(docx_bytes, 1)" in test_edit.content
    assert fixture_edit.content == task.markdown_content + "\n"


def test_conversion_error_missing_junit_gets_deterministic_trusted_fallback() -> None:
    task = TaskArtifact(
        feedback_id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content=(
            "$$\\begin{aligned} a &= b + c \\notag \\\\ "
            "b &= d + e \\end{aligned}$$"
        ),
        description="formula export raises a conversion error",
        content_fingerprint="a" * 64,
    )
    plan = ReproductionPlan(
        hypothesis="Pandoc rejects aligned math containing notag",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_math_count", "minimum": 1},
        ),
        target_test_selector="test_feedback_a257a846_aligned_notag_formula",
        expected_failure_kind=ExpectedFailureKind.UNEXPECTED_CONVERSION_ERROR,
        files_to_read=(
            SourceReadRequest(path="backend/app/pandoc_runner.py"),
            SourceReadRequest(path="backend/app/normalizer.py"),
        ),
    )
    previous = ReproductionReport(
        disposition=ReproductionDisposition.INVALID_TEST,
        round=1,
        target_test_selector=plan.target_test_selector,
        expected_failure_kind=plan.expected_failure_kind,
        failure_code="missing_junit",
        failure_summary="sandbox result is not a valid target failure",
    )

    generated = build_conversion_error_test_fallback(
        task,
        plan=plan,
        previous_report=previous,
        existing_test_source="# existing regression\n",
    )

    assert generated is not None
    assert generated.files_needed_for_fix == (
        "backend/app/pandoc_runner.py",
        "backend/app/normalizer.py",
    )
    test_edit, fixture_edit = generated.edits
    assert test_edit.content is not None
    assert test_edit.content.startswith("# existing regression\n")
    assert "convert_markdown_to_docx(markdown, tmp_path)" in test_edit.content
    assert "assert_minimum_math_count(docx_bytes, 1)" in test_edit.content
    assert fixture_edit.content == task.markdown_content + "\n"


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


def test_assertion_traceback_with_fixtures_name_is_not_infrastructure_failure() -> None:
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
                target_message=(
                    "AssertionError: expected at least 1 drawing(s), got 0 "
                    "FIXTURES / feedback / sample.md"
                ),
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


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"mode": "full_file", "search": "", "content": "x"},
         "full_file requires search to be null"),
        ({"mode": "full_file", "replace": "", "content": "x"},
         "full_file requires replace to be null"),
        ({"mode": "full_file"}, "full_file requires content"),
        ({"mode": "search_replace", "search": "", "replace": "r"},
         "search_replace requires a non-empty search"),
        ({"mode": "search_replace", "search": "s"},
         "search_replace requires replace"),
        ({"mode": "search_replace", "search": "s", "replace": "r", "content": "x"},
         "search_replace requires content to be null"),
        ({"mode": "full_file", "content": "a\x00b"},
         "edit text must not contain NUL"),
    ],
)
def test_edit_rejection_names_the_offending_field(
    fields: dict[str, str],
    expected: str,
) -> None:
    """严格 Structured Outputs 要求所有字段都出现，模型极易把未使用字段填 "" 而非 null。

    该消息会原样回传给模型作为修正提示。合并成一条时模型无从判断该改哪一项，
    实测会越改越偏；维护者从日志里也只能看到 `edits.N:value_error`。
    """

    with pytest.raises(ValidationError) as exc_info:
        Edit(path="backend/tests/test_feedback_regressions.py", **fields)

    assert expected in str(exc_info.value)


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": EditMode.FULL_FILE, "content": "x"},
        {"mode": EditMode.SEARCH_REPLACE, "search": "s", "replace": "r"},
        # 空 replace 是合法的删除
        {"mode": EditMode.SEARCH_REPLACE, "search": "s", "replace": ""},
    ],
)
def test_edit_accepts_well_formed_modes(fields: dict[str, str]) -> None:
    assert Edit(path="backend/tests/test_feedback_regressions.py", **fields)


def test_generate_test_prompt_states_edit_mode_and_fix_allowlist_rules() -> None:
    """这两条规则缺失曾让生产 run 连续两轮以 invalid_response 终结。

    严格 Structured Outputs 把所有属性都写进 required，模型既无法从 Schema 推断
    「新建文件必须用 full_file」，也无法推断 files_needed_for_fix 的白名单。
    """

    prompt = files("agent.prompts").joinpath("generate_test.md").read_text("utf-8")

    assert "新建文件" in prompt and "full_file" in prompt
    assert "search 必须非空" in prompt
    # 可读不可写，是模型最常猜错的一项
    assert "mermaid_renderer.py" in prompt


def test_fix_source_allowlist_stays_in_sync_across_policy_validator_and_prompt() -> None:
    """白名单有三处镜像，任何一处漂移都不会在运行时报错，只会让模型收到错误指引。

    Policy JSON 是安全文档的机器可读镜像；域校验器要在没有文件 I/O 的情况下判定；
    提示词必须复述，因为模型读不到 Policy 文件。
    """

    policy = json.loads(
        files("agent.policies").joinpath("patch_policy.json").read_text("utf-8")
    )
    assert tuple(policy["write"]["fix_exact"]) == FIX_SOURCE_PATHS

    prompt = files("agent.prompts").joinpath("generate_test.md").read_text("utf-8")
    for path in FIX_SOURCE_PATHS:
        assert path in prompt
