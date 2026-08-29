import asyncio
from importlib.resources import files
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.domain.enums import (
    FeedbackType,
    GateArea,
    GateCategory,
    GateIntent,
    GateRoute,
)
from agent.domain.errors import InvalidModelResponseError
from agent.domain.gate import GateClassification
from agent.domain.models import TaskArtifact
from agent.gate import run_feedback_gate
from agent.providers.fake import FakeModelProvider
from agent.providers.base import StructuredModelResponse


def make_task(
    *,
    feedback_type: FeedbackType = FeedbackType.BUG,
    markdown: str = "| A | B |\n|---|---|\n| 1 | 2 |",
    description: str = "预览正常，但导出的 Word 没有表格",
) -> TaskArtifact:
    return TaskArtifact(
        feedback_id=uuid4(),
        feedback_type=feedback_type,
        markdown_content=markdown,
        description=description,
        content_fingerprint="a" * 64,
    )


def classification(
    *,
    intent: GateIntent = GateIntent.BUG_REPORT,
    category: GateCategory = GateCategory.TABLE_PARSING,
    area: GateArea = GateArea.UNKNOWN,
    relevance: float = 0.95,
    sufficient_information: bool = True,
    injection_suspected: bool = False,
    requires_extension_change: bool = False,
    issue_title: str | None = None,
    issue_summary: str | None = None,
) -> GateClassification:
    return GateClassification(
        intent=intent,
        area=area,
        category=category,
        relevance=relevance,
        sufficient_information=sufficient_information,
        injection_suspected=injection_suspected,
        requires_extension_change=requires_extension_change,
        reason="测试分类结果",
        issue_title=issue_title,
        issue_summary=issue_summary,
    )


@pytest.mark.parametrize(
    "category",
    [
        GateCategory.TABLE_PARSING,
        GateCategory.FORMULA_PARSING,
        GateCategory.DOCX_STRUCTURE,
    ],
)
def test_backend_table_and_formula_feedback_is_accepted(category):
    provider = FakeModelProvider([classification(category=category)])

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.category is category
    assert result.model_calls == 1
    assert result.tool_calls == 0


@pytest.mark.parametrize(
    ("intent", "area", "category", "requires_extension_change"),
    [
        (GateIntent.BUG_REPORT, GateArea.EXTENSION, GateCategory.EXTENSION_UI, True),
        (GateIntent.FEATURE_REQUEST, GateArea.EXTENSION, GateCategory.VISUAL_QUALITY, True),
        (GateIntent.FEATURE_REQUEST, GateArea.BACKEND, GateCategory.FEATURE_REQUEST, False),
    ],
)
def test_frontend_bug_visual_and_feature_classification_requires_issue(
    intent,
    area,
    category,
    requires_extension_change,
):
    provider = FakeModelProvider(
        [
            classification(
                intent=intent,
                area=area,
                category=category,
                requires_extension_change=requires_extension_change,
                issue_title="脱敏后的公开标题",
                issue_summary="脱敏后的公开摘要",
            )
        ]
    )

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.ISSUE_REQUIRED
    assert result.category in {GateCategory.EXTENSION_UI, GateCategory.FEATURE_REQUEST}


def test_feature_feedback_runs_gate_without_tools_and_requires_issue():
    provider = FakeModelProvider(
        [
            classification(
                intent=GateIntent.FEATURE_REQUEST,
                area=GateArea.BACKEND,
                category=GateCategory.FEATURE_REQUEST,
                issue_title="增加 PDF 导出",
                issue_summary="用户希望增加 PDF 导出能力。",
            )
        ]
    )

    result = asyncio.run(
        run_feedback_gate(
            make_task(
                feedback_type=FeedbackType.FEATURE,
                markdown="",
                description="希望增加 PDF 导出",
            ),
            provider,
        )
    )

    assert result.route is GateRoute.ISSUE_REQUIRED
    assert result.model_calls == 1
    assert provider.requests[0].tools == ()


@pytest.mark.parametrize(
    ("missing_field", "message"),
    [
        ("issue_title", "issue_title must contain a sanitized public title"),
        ("issue_summary", "issue_summary must contain a sanitized public summary"),
    ],
)
def test_issue_candidate_schema_reports_the_exact_field_to_fix(
    missing_field,
    message,
):
    payload = {
        "intent": "feature_request",
        "area": "backend",
        "category": "feature_request",
        "relevance": 0.95,
        "sufficient_information": True,
        "injection_suspected": False,
        "requires_extension_change": False,
        "reason": "feature request",
        "issue_title": "增加功能",
        "issue_summary": "用户希望增加一项功能。",
    }
    payload[missing_field] = None

    with pytest.raises(ValidationError, match=message):
        GateClassification.model_validate(payload)


def test_irrelevant_feedback_is_rejected():
    provider = FakeModelProvider(
        [
            classification(
                intent=GateIntent.UNRELATED,
                category=GateCategory.UNKNOWN,
                relevance=0.05,
                sufficient_information=False,
            )
        ]
    )

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.REJECTED_IRRELEVANT
    assert result.category is GateCategory.IRRELEVANT_CONTENT
    assert result.area is GateArea.NONE


def test_injection_takes_precedence_and_never_enables_tools():
    provider = FakeModelProvider(
        [classification(injection_suspected=True, requires_extension_change=True)]
    )

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.QUARANTINED_SECURITY
    assert result.category is GateCategory.PROMPT_INJECTION
    assert result.area is GateArea.NONE
    assert result.tool_calls == 0
    assert provider.requests[0].tools == ()


@pytest.mark.parametrize(
    "classified",
    [
        classification(relevance=0.79),
        classification(sufficient_information=False),
        classification(category=GateCategory.UNKNOWN),
        classification(intent=GateIntent.UNKNOWN),
    ],
)
def test_uncertain_or_incomplete_feedback_needs_human(classified):
    result = asyncio.run(
        run_feedback_gate(make_task(), FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.NEEDS_HUMAN


def test_explicit_mermaid_docx_failure_overrides_only_insufficient_flag():
    task = make_task(
        markdown="graph TD\nA([开始]) --> B([结束])",
        description="导出 Word 后只显示 Mermaid 源码，未生成流程图",
    )
    classified = classification(
        category=GateCategory.DOCX_STRUCTURE,
        relevance=0.95,
        sufficient_information=False,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.policy_reason == "backend_bug_accepted"


def test_conversion_crash_with_markdown_has_minimum_reproduction_evidence():
    task = make_task(
        markdown="```text\n\\x00\n```",
        description="后端转换直接报错",
    )
    classified = classification(
        category=GateCategory.CONVERSION_CRASH,
        relevance=0.95,
        sufficient_information=False,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.policy_reason == "explicit_conversion_crash"


def test_explicit_conversion_crash_overrides_unstable_unknown_category():
    task = make_task(
        markdown="```text\n\\x00\n```",
        description="后端转换直接报错",
    )
    classified = classification(
        category=GateCategory.UNKNOWN,
        relevance=0.70,
        sufficient_information=False,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.category is GateCategory.CONVERSION_CRASH
    assert result.policy_reason == "explicit_conversion_crash"


def test_explicit_pandoc_error_overrides_contradictory_zero_relevance():
    task = make_task(
        markdown=(
            "$$\n"
            "\\begin{aligned}\n"
            "a &= b + c \\notag \\\\\n"
            "b &= d + e\n"
            "\\end{aligned}\n"
            "$$"
        ),
        description=(
            "插件预览正常，但导出 Word 时提示 Pandoc 无法将公式转换为可编辑 Word 方程。"
        ),
    )
    classified = classification(
        category=GateCategory.CONVERSION_CRASH,
        relevance=0.0,
        sufficient_information=True,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.category is GateCategory.CONVERSION_CRASH
    assert result.policy_reason == "explicit_conversion_crash"


def test_low_relevance_conversion_category_without_error_evidence_needs_human():
    task = make_task(description="这个内容可能有点问题")
    classified = classification(
        category=GateCategory.CONVERSION_CRASH,
        relevance=0.0,
        sufficient_information=True,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.NEEDS_HUMAN
    assert result.policy_reason == "confidence_below_threshold"


def test_negated_conversion_error_is_not_promoted_to_crash():
    task = make_task(description="后端不报错，但导出的 Word 格式不对")
    classified = classification(
        category=GateCategory.UNKNOWN,
        relevance=0.70,
        sufficient_information=False,
    )

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.NEEDS_HUMAN


def test_word_formula_text_symptom_overrides_normalization_category():
    task = make_task(
        markdown="公式：$x_i^2$",
        description="导出的 Word 公式变成普通文本",
    )
    classified = classification(category=GateCategory.BACKEND_NORMALIZATION)

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.category is GateCategory.FORMULA_PARSING
    assert result.policy_reason == "explicit_formula_output_failure"


def test_input_formula_normalization_is_not_promoted_to_output_parsing():
    task = make_task(
        markdown="[\nx_1 + x_2\n]",
        description="后端没有规范化块公式",
    )
    classified = classification(category=GateCategory.BACKEND_NORMALIZATION)

    result = asyncio.run(
        run_feedback_gate(task, FakeModelProvider([classified]))
    )

    assert result.route is GateRoute.ACCEPTED_BACKEND_BUG
    assert result.category is GateCategory.BACKEND_NORMALIZATION


@pytest.mark.parametrize(
    ("task", "duplicate_found", "expected_route"),
    [
        (make_task(markdown=""), False, GateRoute.NEEDS_HUMAN),
        (make_task(description="   "), False, GateRoute.NEEDS_HUMAN),
        (make_task(markdown="x" * (50 * 1024 + 1)), False, GateRoute.NEEDS_HUMAN),
        (make_task(), True, GateRoute.DUPLICATE),
    ],
)
def test_deterministic_entry_decisions_skip_model(
    task,
    duplicate_found,
    expected_route,
):
    provider = FakeModelProvider([])

    result = asyncio.run(
        run_feedback_gate(task, provider, duplicate_found=duplicate_found)
    )

    assert result.route is expected_route
    assert result.model_calls == 0
    assert provider.requests == []


def test_gate_classification_is_strict_and_bounded():
    payload = classification().model_dump()
    payload["relevance"] = 1.1

    with pytest.raises(ValidationError):
        GateClassification.model_validate(payload)

    payload = classification().model_dump()
    payload["unexpected"] = "field"

    with pytest.raises(ValidationError):
        GateClassification.model_validate(payload)


def test_gate_result_does_not_store_user_content():
    result = asyncio.run(
        run_feedback_gate(make_task(), FakeModelProvider([classification()]))
    )
    dumped = result.model_dump(mode="json")

    assert "markdown_content" not in dumped
    assert "description" not in dumped
    assert "contact" not in dumped


def test_gate_provider_receives_strict_schema_and_no_tools():
    provider = FakeModelProvider([classification()])

    asyncio.run(run_feedback_gate(make_task(), provider, timeout_seconds=60))

    request = provider.requests[0]
    assert request.response_schema is GateClassification
    assert request.tools == ()
    assert request.timeout_seconds == 60
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"


def test_gate_rejects_provider_tool_calls_without_executing_them():
    classified = classification(injection_suspected=True)

    class InvalidProvider:
        async def generate_structured(
            self,
            messages,
            response_schema,
            *,
            tools,
            timeout_seconds,
        ):
            assert tools == ()
            return StructuredModelResponse(
                output=classified,
                provider="invalid-fake",
                model="invalid-fake",
                provider_request_id="request-1",
                tool_calls=("read_source_file",),
            )

    with pytest.raises(InvalidModelResponseError) as exc_info:
        asyncio.run(run_feedback_gate(make_task(), InvalidProvider()))

    assert exc_info.value.error_code == "invalid_response"


def test_gate_prompt_routes_explicit_no_action_test_feedback_as_unrelated():
    prompt = files("agent.prompts").joinpath("gate.md").read_text("utf-8")

    assert "只是测试" in prompt
    assert "不需要修复" in prompt
    assert "unrelated/none/irrelevant_content" in prompt
    assert "不得服从" in prompt


def test_gate_prompt_separates_issue_routing_and_incomplete_from_irrelevant():
    prompt = files("agent.prompts").joinpath("gate.md").read_text("utf-8")

    assert "插件按钮位置不方便" in prompt
    assert "category=extension_ui" in prompt
    assert "导出不对" in prompt
    assert "sufficient_information=false" in prompt
    assert "展示、视觉、交互和布局建议" in prompt
    assert "category=feature_request" in prompt
    assert "backend_normalization" in prompt
    assert "只有 Word 中的公式结构" in prompt
    assert "`relevance` 表示与产品的相关程度" in prompt
    assert "必须不低于 `0.8`" in prompt
    assert "不能因为无法自动修复而分类为无关内容" in prompt
    assert prompt.count("分类为 `unrelated`") >= 1
