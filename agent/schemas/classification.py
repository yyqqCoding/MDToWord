"""分类 Schema 与确定性后置规则(阶段 04 spec §1/§3)。

后置规则由本地代码执行,不信模型自评(automatable 可被强制置 False,
永远不会被规则置 True)。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCategory(str, Enum):
    CONVERSION_CRASH = "conversion_crash"
    FORMULA_PARSING = "formula_parsing"
    TABLE_PARSING = "table_parsing"
    HEADING_PARSING = "heading_parsing"
    LIST_PARSING = "list_parsing"
    DOCX_STRUCTURE = "docx_structure"
    BACKEND_NORMALIZATION = "backend_normalization"
    PREVIEW_EXPORT_MISMATCH = "preview_export_mismatch"
    EXTENSION_UI = "extension_ui"
    FEATURE_REQUEST = "feature_request"
    VISUAL_QUALITY = "visual_quality"
    INVALID_FEEDBACK = "invalid_feedback"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"


# 公式类问题的两条复现路径(pandoc_runner 对 TeX 失败直接抛 ConversionError,
# 不会生成 DOCX;其余场景生成 DOCX 但缺目标节点):
ReproductionStrategy = Literal[
    "expect_conversion_error",   # 预期 convert_markdown_to_docx 抛 ConversionError
    "expect_docx_missing_node",  # 预期生成 DOCX 但缺少目标节点(m:oMath / w:tbl / 样式)
    "none",                      # 不可自动化/无需复现
]

# 这些类别永远不进自动修复
NON_AUTOMATABLE_CATEGORIES = frozenset({
    FeedbackCategory.EXTENSION_UI,
    FeedbackCategory.FEATURE_REQUEST,
    FeedbackCategory.VISUAL_QUALITY,
})


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FeedbackCategory
    automatable: bool
    confidence: float = Field(ge=0, le=1)
    affected_files: list[str] = Field(default_factory=list)
    requires_extension_change: bool = False
    injection_suspected: bool = False
    reproduction_strategy: ReproductionStrategy
    reason: str


def apply_post_rules(result: ClassificationResult, *,
                     min_confidence: float) -> ClassificationResult:
    """确定性后置规则(spec §3)。只降级,不升级。"""
    updated = result.model_copy()
    if updated.requires_extension_change:
        updated.automatable = False
    if updated.category in NON_AUTOMATABLE_CATEGORIES:
        updated.automatable = False
    if updated.confidence < min_confidence:
        updated.automatable = False
    if updated.injection_suspected:
        updated.automatable = False
    return updated


def decide_next(result: ClassificationResult) -> tuple[str, str]:
    """由后置规则处理后的结果决定 feedback 状态与下一步动作。"""
    if result.injection_suspected:
        return "needs_human", "manual_review"
    if result.category is FeedbackCategory.INVALID_FEEDBACK:
        return "invalid", "none"
    if result.category is FeedbackCategory.DUPLICATE:
        return "duplicate", "none"
    if result.requires_extension_change or result.category is FeedbackCategory.EXTENSION_UI:
        return "needs_extension_release", "open_issue"
    if result.automatable:
        return "classified", "generate_test"
    return "needs_human", "manual_review"
