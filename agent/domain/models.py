from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.domain.enums import FeedbackStatus, FeedbackType, RiskLevel
from agent.domain.fingerprints import feedback_fingerprint


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
    risk: RiskLevel = RiskLevel.UNKNOWN
    content_fingerprint: str = ""
    attempt_count: int = Field(default=0, ge=0)
    stale_requeue_count: int = Field(default=0, ge=0, le=1)
    claimed_at: datetime | None = None
    claim_token: UUID | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    pr_url: str | None = None
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
