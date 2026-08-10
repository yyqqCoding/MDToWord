from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.domain.enums import GateCategory, GateIntent, GateRoute, RiskLevel


class GateClassification(BaseModel):
    """无工具模型的分类结果；不接受 Schema 之外的解释性字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: GateIntent
    category: GateCategory
    relevance: float = Field(ge=0, le=1)
    sufficient_information: bool
    injection_suspected: bool
    requires_extension_change: bool
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class GateResult(BaseModel):
    """可持久化的 Gate 摘要；不保存用户描述或 Markdown 原文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: GateRoute
    category: GateCategory = GateCategory.UNKNOWN
    risk: RiskLevel = RiskLevel.UNKNOWN
    policy_reason: str = Field(min_length=1, max_length=100)
    classification: GateClassification | None = None
    # 一次非法结构允许一轮格式修正，因此真实 Provider 最多产生两次调用。
    model_calls: int = Field(default=0, ge=0, le=2)
    tool_calls: Literal[0] = 0
