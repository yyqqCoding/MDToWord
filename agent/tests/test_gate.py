import asyncio
from importlib.resources import files
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.domain.enums import (
    FeedbackType,
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
    relevance: float = 0.95,
    sufficient_information: bool = True,
    injection_suspected: bool = False,
    requires_extension_change: bool = False,
) -> GateClassification:
    return GateClassification(
        intent=intent,
        category=category,
        relevance=relevance,
        sufficient_information=sufficient_information,
        injection_suspected=injection_suspected,
        requires_extension_change=requires_extension_change,
        reason="测试分类结果",
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
    ("intent", "category", "requires_extension_change"),
    [
        (GateIntent.BUG_REPORT, GateCategory.EXTENSION_UI, True),
        (GateIntent.BUG_REPORT, GateCategory.VISUAL_QUALITY, False),
        (GateIntent.FEATURE_REQUEST, GateCategory.UNKNOWN, False),
    ],
)
def test_frontend_visual_and_feature_classification_is_out_of_scope(
    intent,
    category,
    requires_extension_change,
):
    provider = FakeModelProvider(
        [
            classification(
                intent=intent,
                category=category,
                requires_extension_change=requires_extension_change,
            )
        ]
    )

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.OUT_OF_SCOPE


def test_feature_feedback_skips_model_completely():
    provider = FakeModelProvider([])

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

    assert result.route is GateRoute.OUT_OF_SCOPE
    assert result.model_calls == 0
    assert provider.requests == []


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


def test_injection_takes_precedence_and_never_enables_tools():
    provider = FakeModelProvider(
        [classification(injection_suspected=True, requires_extension_change=True)]
    )

    result = asyncio.run(run_feedback_gate(make_task(), provider))

    assert result.route is GateRoute.QUARANTINED_SECURITY
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

    asyncio.run(run_feedback_gate(make_task(), provider))

    request = provider.requests[0]
    assert request.response_schema is GateClassification
    assert request.tools == ()
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
    assert "intent=unrelated" in prompt
    assert "不得输出" in prompt
