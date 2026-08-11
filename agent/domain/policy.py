from agent.domain.content import contains_mermaid_diagram
from agent.domain.enums import (
    FeedbackType,
    GateCategory,
    GateIntent,
    GateRoute,
    RiskLevel,
)
from agent.domain.gate import GateClassification, GateResult
from agent.domain.models import TaskArtifact


MAX_DESCRIPTION_CHARS = 1000
MAX_MARKDOWN_BYTES = 50 * 1024
MIN_GATE_CONFIDENCE = 0.80

BACKEND_CATEGORY_ALLOWLIST = frozenset(
    {
        GateCategory.CONVERSION_CRASH,
        GateCategory.FORMULA_PARSING,
        GateCategory.TABLE_PARSING,
        GateCategory.HEADING_PARSING,
        GateCategory.LIST_PARSING,
        GateCategory.DOCX_STRUCTURE,
        GateCategory.BACKEND_NORMALIZATION,
    }
)


def deterministic_gate_result(
    task: TaskArtifact,
    *,
    duplicate_found: bool,
) -> GateResult | None:
    """先处理无需模型的输入；返回 None 才允许进入模型分类。"""

    if not task.description.strip():
        return _terminal(GateRoute.NEEDS_HUMAN, "description_blank")
    if len(task.description) > MAX_DESCRIPTION_CHARS:
        return _terminal(GateRoute.NEEDS_HUMAN, "description_too_long")
    if task.feedback_type is FeedbackType.BUG:
        if not task.markdown_content.strip():
            return _terminal(GateRoute.NEEDS_HUMAN, "bug_markdown_blank")
        if len(task.markdown_content.encode("utf-8")) > MAX_MARKDOWN_BYTES:
            return _terminal(GateRoute.NEEDS_HUMAN, "bug_markdown_too_large")

    # 重复判定早于反馈类型分流，确保已有处理结果始终是唯一事实来源。
    if duplicate_found:
        return _terminal(GateRoute.DUPLICATE, "open_duplicate_found")
    if task.feedback_type is FeedbackType.FEATURE:
        return _terminal(GateRoute.OUT_OF_SCOPE, "feature_feedback_type")
    return None


def apply_gate_policy(
    classification: GateClassification,
    *,
    task: TaskArtifact,
    min_confidence: float = MIN_GATE_CONFIDENCE,
    model_calls: int = 1,
) -> GateResult:
    """模型只提供事实分类；本地规则按固定优先级决定最终路由。"""

    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")

    if classification.injection_suspected:
        return _classified_terminal(
            GateRoute.QUARANTINED_SECURITY,
            classification,
            "injection_suspected",
            risk=RiskLevel.HIGH,
            model_calls=model_calls,
        )
    if classification.intent in {GateIntent.UNRELATED, GateIntent.SPAM}:
        return _classified_terminal(
            GateRoute.REJECTED_IRRELEVANT,
            classification,
            "irrelevant_or_spam",
            model_calls=model_calls,
        )
    if (
        classification.intent is GateIntent.FEATURE_REQUEST
        or classification.requires_extension_change
        or classification.category
        in {GateCategory.EXTENSION_UI, GateCategory.VISUAL_QUALITY}
    ):
        return _classified_terminal(
            GateRoute.OUT_OF_SCOPE,
            classification,
            "frontend_feature_or_visual",
            model_calls=model_calls,
        )
    if classification.relevance < min_confidence:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "confidence_below_threshold",
            model_calls=model_calls,
        )
    mermaid_docx_evidence = (
        classification.intent is GateIntent.BUG_REPORT
        and classification.category is GateCategory.DOCX_STRUCTURE
        and contains_mermaid_diagram(task.markdown_content)
    )
    if not classification.sufficient_information and not mermaid_docx_evidence:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "insufficient_information",
            model_calls=model_calls,
        )
    if classification.intent is not GateIntent.BUG_REPORT:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "unsupported_intent",
            model_calls=model_calls,
        )
    if classification.category not in BACKEND_CATEGORY_ALLOWLIST:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "unsupported_category",
            model_calls=model_calls,
        )
    return _classified_terminal(
        GateRoute.ACCEPTED_BACKEND_BUG,
        classification,
        "backend_bug_accepted",
        risk=RiskLevel.LOW,
        model_calls=model_calls,
    )


def _terminal(route: GateRoute, policy_reason: str) -> GateResult:
    return GateResult(route=route, policy_reason=policy_reason)


def _classified_terminal(
    route: GateRoute,
    classification: GateClassification,
    policy_reason: str,
    *,
    risk: RiskLevel = RiskLevel.UNKNOWN,
    model_calls: int = 1,
) -> GateResult:
    return GateResult(
        route=route,
        category=classification.category,
        risk=risk,
        policy_reason=policy_reason,
        classification=classification,
        model_calls=model_calls,
    )
