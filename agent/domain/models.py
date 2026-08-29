from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.domain.enums import (
    AgentRunStatus,
    FeedbackStatus,
    FeedbackType,
    GateArea,
    GateCategory,
    GateRoute,
    RiskLevel,
)
from agent.domain.fingerprints import feedback_fingerprint
from agent.domain.failures import FailureSnapshot
from agent.domain.gate import GateResult


def utc_now() -> datetime:
    return datetime.now(UTC)


class FeedbackRecord(BaseModel):
    """数据库反馈记录；contact 仅供维护者联系用户，不进入 Agent State。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    feedback_type: FeedbackType
    markdown_content: str = ""
    description: str = Field(min_length=1)
    # 禁止 repr 输出联系方式，避免异常日志无意带出用户信息。
    contact: str = Field(default="", repr=False)
    status: FeedbackStatus = FeedbackStatus.PENDING
    category: str | None = None
    area: GateArea = GateArea.UNKNOWN
    risk: RiskLevel = RiskLevel.UNKNOWN
    content_fingerprint: str = ""
    attempt_count: int = Field(default=0, ge=0)
    stale_requeue_count: int = Field(default=0, ge=0, le=1)
    claimed_at: datetime | None = None
    claim_token: UUID | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    pr_url: str | None = None
    issue_url: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def replace_null_fingerprint(cls, value: str | None) -> str:
        return value or ""

    @field_validator("claimed_at", "created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def populate_fingerprint(self) -> "FeedbackRecord":
        # 兼容尚未回填指纹的历史反馈，由 Controller 首次读取时确定性补齐。
        if not self.content_fingerprint:
            self.content_fingerprint = feedback_fingerprint(
                self.feedback_type,
                self.markdown_content,
                self.description,
            )
        return self


class TaskArtifact(BaseModel):
    """供受控复现使用的用户输入；Schema 从结构上排除 contact。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: UUID
    feedback_type: FeedbackType
    markdown_content: str
    description: str
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_feedback(cls, feedback: FeedbackRecord) -> "TaskArtifact":
        # 显式逐字段复制，不使用 model_dump，防止未来新增敏感字段后被自动带入。
        return cls(
            feedback_id=feedback.id,
            feedback_type=feedback.feedback_type,
            markdown_content=feedback.markdown_content,
            description=feedback.description,
            content_fingerprint=feedback.content_fingerprint,
        )


class AgentRunRecord(BaseModel):
    """数据库中的运行摘要；原始反馈只通过受控 Artifact 引用关联。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    feedback_id: UUID
    claim_token: UUID = Field(repr=False)
    trace_id: str = Field(min_length=1, max_length=200)
    status: AgentRunStatus = AgentRunStatus.CREATED
    route: GateRoute | None = None
    area: GateArea = GateArea.UNKNOWN
    category: GateCategory | None = None
    dry_run: bool = True
    base_sha: str | None = None
    extension_version: str = "unknown"
    provider: str | None = None
    model: str | None = None
    graph_version: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    policy_version: str
    langfuse_trace_id: str | None = None
    classification: GateResult | None = None
    reproduction: dict[str, object] | None = None
    repair: dict[str, object] | None = None
    validation: dict[str, object] | None = None
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    validated_patch_sha256: str | None = None
    artifact_path: str
    task_artifact_ref: str
    pr_url: str | None = None
    issue_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    failure: FailureSnapshot | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_run_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value
