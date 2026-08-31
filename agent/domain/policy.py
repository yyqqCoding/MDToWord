"""Gate 的确定性业务 Policy。

模型负责提供分类事实，Policy 负责把事实按固定优先级转换成可执行路由；
安全拦截、最小证据和人工接管都在这里落地，而不是交给 Prompt 自律。
"""

import re

from agent.domain.content import contains_mermaid_diagram
from agent.domain.enums import (
    FeedbackType,
    GateArea,
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

    classification = _normalize_classification(classification)

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
    issue_candidate = (
        classification.intent is GateIntent.FEATURE_REQUEST
        or (
            classification.intent is GateIntent.BUG_REPORT
            and classification.area is GateArea.EXTENSION
        )
    )
    if issue_candidate and classification.relevance < min_confidence:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "confidence_below_threshold",
            model_calls=model_calls,
        )
    if issue_candidate and not classification.sufficient_information:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "insufficient_information",
            model_calls=model_calls,
        )
    if issue_candidate and classification.area not in {
        GateArea.BACKEND,
        GateArea.EXTENSION,
        GateArea.CROSS_COMPONENT,
    }:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "issue_area_unknown",
            model_calls=model_calls,
        )
    if issue_candidate and (
        classification.issue_title is None or classification.issue_summary is None
    ):
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "issue_draft_missing",
            model_calls=model_calls,
        )
    if issue_candidate:
        return _classified_terminal(
            GateRoute.ISSUE_REQUIRED,
            classification,
            (
                "extension_bug_requires_issue"
                if classification.intent is GateIntent.BUG_REPORT
                else "feature_requires_issue"
            ),
            model_calls=model_calls,
        )
    if (
        classification.intent is GateIntent.BUG_REPORT
        and _has_explicit_conversion_crash_evidence(task)
    ):
        # 明确的后端/转换报错已有非空输入即可交给 Sandbox 复现，不依赖模型是否稳定地
        # 选择 conversion_crash；注入、无关和前端范围外仍由上面的高优先级规则拦截。
        return _classified_terminal(
            GateRoute.ACCEPTED_BACKEND_BUG,
            classification,
            "explicit_conversion_crash",
            category=GateCategory.CONVERSION_CRASH,
            risk=RiskLevel.LOW,
            model_calls=model_calls,
        )
    if (
        classification.intent is GateIntent.BUG_REPORT
        and _has_explicit_formula_output_evidence(task)
    ):
        # Word/DOCX 中公式文本化或丢失是输出解析问题；与“送入 Pandoc 前未规范化”
        # 分开，避免模型在两个相邻类别间波动而选择错误的复现 Oracle。
        return _classified_terminal(
            GateRoute.ACCEPTED_BACKEND_BUG,
            classification,
            "explicit_formula_output_failure",
            category=GateCategory.FORMULA_PARSING,
            risk=RiskLevel.LOW,
            model_calls=model_calls,
        )
    if classification.relevance < min_confidence:
        return _classified_terminal(
            GateRoute.NEEDS_HUMAN,
            classification,
            "confidence_below_threshold",
            model_calls=model_calls,
        )
    reproducible_minimum_evidence = (
        classification.intent is GateIntent.BUG_REPORT
        and (
            classification.category is GateCategory.CONVERSION_CRASH
            # 转换崩溃只需用户已经提供非空 Markdown，即可由 Sandbox 判断能否复现；
            # 错误堆栈缺失不应阻止进入有界复现阶段。
            or (
                classification.category is GateCategory.DOCX_STRUCTURE
                and contains_mermaid_diagram(task.markdown_content)
            )
        )
    )
    if not classification.sufficient_information and not reproducible_minimum_evidence:
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
    category: GateCategory | None = None,
    risk: RiskLevel = RiskLevel.UNKNOWN,
    model_calls: int = 1,
) -> GateResult:
    return GateResult(
        route=route,
        area=classification.area,
        category=category if category is not None else classification.category,
        risk=risk,
        policy_reason=policy_reason,
        classification=classification,
        model_calls=model_calls,
    )


def _normalize_classification(
    classification: GateClassification,
) -> GateClassification:
    """把模型的相邻类别收敛为持久化和展示使用的稳定分类。"""

    if classification.injection_suspected:
        return classification.model_copy(
            update={
                "area": GateArea.NONE,
                "category": GateCategory.PROMPT_INJECTION,
                "issue_title": None,
                "issue_summary": None,
            }
        )
    if classification.intent in {GateIntent.UNRELATED, GateIntent.SPAM}:
        return classification.model_copy(
            update={
                "area": GateArea.NONE,
                "category": GateCategory.IRRELEVANT_CONTENT,
                "issue_title": None,
                "issue_summary": None,
            }
        )

    extension_signal = (
        classification.area is GateArea.EXTENSION
        or classification.requires_extension_change
        or classification.category
        in {GateCategory.EXTENSION_UI, GateCategory.VISUAL_QUALITY}
    )
    if classification.category is GateCategory.VISUAL_QUALITY:
        return classification.model_copy(
            update={
                "intent": GateIntent.FEATURE_REQUEST,
                "area": GateArea.EXTENSION,
                "category": GateCategory.FEATURE_REQUEST,
                "requires_extension_change": True,
            }
        )
    if classification.intent is GateIntent.FEATURE_REQUEST:
        return classification.model_copy(
            update={
                "area": (
                    GateArea.EXTENSION
                    if extension_signal
                    else classification.area
                ),
                "category": GateCategory.FEATURE_REQUEST,
            }
        )
    if classification.intent is GateIntent.BUG_REPORT and extension_signal:
        return classification.model_copy(
            update={
                "area": GateArea.EXTENSION,
                "category": GateCategory.EXTENSION_UI,
                "requires_extension_change": True,
            }
        )
    if (
        classification.intent is GateIntent.BUG_REPORT
        and classification.area is GateArea.UNKNOWN
        and classification.category in BACKEND_CATEGORY_ALLOWLIST
    ):
        return classification.model_copy(update={"area": GateArea.BACKEND})
    return classification


_CONVERSION_CRASH_PATTERNS = (
    re.compile(r"(?:后端|服务端).{0,12}(?:报错|崩溃|异常)"),
    re.compile(r"(?:转换|导出).{0,8}(?:直接)?(?:报错|崩溃)"),
    re.compile(
        r"pandoc.{0,80}(?:无法|失败|报错|错误|could not|failed|error)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:backend|server).{0,20}(?:error|crash|exception)", re.IGNORECASE),
    re.compile(r"conversion.{0,20}(?:error|crash|exception)", re.IGNORECASE),
)
_NEGATED_CRASH = re.compile(r"(?:没有|未|不|不会|无)(?:直接)?报错")


def _has_explicit_conversion_crash_evidence(task: TaskArtifact) -> bool:
    """识别可直接复现的转换崩溃描述，同时排除“没有报错”等否定表达。"""

    if task.feedback_type is not FeedbackType.BUG or not task.markdown_content.strip():
        return False
    description = " ".join(task.description.split())
    if _NEGATED_CRASH.search(description):
        return False
    return any(pattern.search(description) for pattern in _CONVERSION_CRASH_PATTERNS)


def _has_explicit_formula_output_evidence(task: TaskArtifact) -> bool:
    """识别 Word 输出中的公式结构症状，不匹配仅描述输入规范化的反馈。"""

    if task.feedback_type is not FeedbackType.BUG or not task.markdown_content.strip():
        return False
    description = " ".join(task.description.lower().split())
    has_output = "word" in description or "docx" in description
    has_formula = "公式" in description or any(
        word in description for word in ("formula", "math")
    )
    has_symptom = any(
        symptom in description
        for symptom in (
            "普通文本",
            "变成文本",
            "丢失",
            "乱码",
            "错误",
            "不显示",
            "未显示",
            "plain text",
            "missing",
            "broken",
        )
    )
    return has_output and has_formula and has_symptom
