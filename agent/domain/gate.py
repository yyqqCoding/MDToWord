from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.domain.enums import GateArea, GateCategory, GateIntent, GateRoute, RiskLevel


class GateClassification(BaseModel):
    """无工具模型的分类结果；不接受 Schema 之外的解释性字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: GateIntent
    area: GateArea = GateArea.UNKNOWN
    category: GateCategory
    relevance: float = Field(ge=0, le=1)
    sufficient_information: bool
    injection_suspected: bool
    requires_extension_change: bool
    reason: str = Field(min_length=1, max_length=300)
    issue_title: str | None = Field(default=None, min_length=1, max_length=80)
    issue_summary: str | None = Field(default=None, min_length=1, max_length=600)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value

    @field_validator("issue_title", "issue_summary")
    @classmethod
    def issue_text_must_be_safe_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("issue text must not be blank; use null when not applicable")
        if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
            raise ValueError("issue text must not contain control characters")
        return value

    @model_validator(mode="after")
    def validate_issue_draft_fields(self) -> "GateClassification":
        blocked = self.injection_suspected or self.intent in {
            GateIntent.UNRELATED,
            GateIntent.SPAM,
        }
        if blocked and self.issue_title is not None:
            raise ValueError("issue_title must be null for injection or irrelevant input")
        if blocked and self.issue_summary is not None:
            raise ValueError("issue_summary must be null for injection or irrelevant input")
        if blocked:
            return self

        issue_candidate = (
            self.intent is GateIntent.FEATURE_REQUEST
            or self.area is GateArea.EXTENSION
            or self.requires_extension_change
            or self.category
            in {GateCategory.EXTENSION_UI, GateCategory.VISUAL_QUALITY}
        )
        if issue_candidate and self.sufficient_information:
            if self.area not in {
                GateArea.BACKEND,
                GateArea.EXTENSION,
                GateArea.CROSS_COMPONENT,
            }:
                raise ValueError(
                    "area must identify backend, extension, or cross_component for an Issue"
                )
            if self.issue_title is None:
                raise ValueError("issue_title must contain a sanitized public title")
            if self.issue_summary is None:
                raise ValueError("issue_summary must contain a sanitized public summary")
        elif issue_candidate:
            if self.issue_title is not None:
                raise ValueError("issue_title must be null when information is insufficient")
            if self.issue_summary is not None:
                raise ValueError("issue_summary must be null when information is insufficient")
        elif not blocked and not issue_candidate:
            if self.issue_title is not None:
                raise ValueError("issue_title must be null when no Issue will be created")
            if self.issue_summary is not None:
                raise ValueError("issue_summary must be null when no Issue will be created")
        return self


class GateResult(BaseModel):
    """可持久化的 Gate 摘要；不保存用户描述或 Markdown 原文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: GateRoute
    area: GateArea = GateArea.UNKNOWN
    category: GateCategory = GateCategory.UNKNOWN
    risk: RiskLevel = RiskLevel.UNKNOWN
    policy_reason: str = Field(min_length=1, max_length=100)
    classification: GateClassification | None = None
    # 一次非法结构允许一轮格式修正，因此真实 Provider 最多产生两次调用。
    model_calls: int = Field(default=0, ge=0, le=2)
    tool_calls: Literal[0] = 0
