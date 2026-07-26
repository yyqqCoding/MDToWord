"""领域模型:Feedback、脱敏 Task Artifact、内容指纹。

`contact` 只存在于 Feedback 内部模型;TaskArtifact 结构上不含该字段,
序列化后进入 artifact 的数据永远没有联系方式(security-policy §6/§8)。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict


def build_fingerprint(feedback_type: str, markdown: str, description: str) -> str:
    """内容指纹。用 feedback_type(用户提交时已有)而非模型分类结果,
    使去重发生在调用模型之前(README 差异 #4)。CRLF/LF 归一保证跨平台稳定。"""
    normalized = "\n".join(
        [
            (feedback_type or "").strip().lower(),
            (markdown or "").strip().replace("\r\n", "\n"),
            (description or "").strip().replace("\r\n", "\n"),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Feedback(BaseModel):
    """feedback 表行(仅 Agent 关心的字段;多余列忽略)。"""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    feedback_type: str
    markdown_content: str = ""
    description: str = ""
    contact: str | None = None  # 仅内部使用,禁止进入 artifact/日志/模型
    status: str = "pending"
    category: str | None = None
    automatable: bool | None = None
    agent_approved: bool = False
    expected_behavior: str | None = None
    content_fingerprint: str | None = None
    source_version: str | None = None
    attempt_count: int = 0
    claim_token: UUID | None = None
    pr_url: str | None = None
    resolution_type: str | None = None

    def fingerprint(self) -> str:
        return build_fingerprint(self.feedback_type, self.markdown_content, self.description)


class TaskArtifact(BaseModel):
    """脱敏任务产物(Job A 输出,Job B/C 输入)。结构上没有 contact 字段。"""

    feedback_id: UUID
    feedback_type: str
    markdown_content: str
    description: str
    expected_behavior: str | None = None
    source_version: str | None = None
    fingerprint: str
    claim_token: UUID
    agent_run_id: UUID | None = None

    @classmethod
    def from_feedback(cls, feedback: Feedback, *, claim_token: UUID,
                      agent_run_id: UUID | None = None) -> "TaskArtifact":
        return cls(
            feedback_id=feedback.id,
            feedback_type=feedback.feedback_type,
            markdown_content=feedback.markdown_content,
            description=feedback.description,
            expected_behavior=feedback.expected_behavior,
            source_version=feedback.source_version,
            fingerprint=feedback.fingerprint(),
            claim_token=claim_token,
            agent_run_id=agent_run_id,
        )

    def write(self, path: Path) -> None:
        data = json.loads(self.model_dump_json())
        assert "contact" not in data, "task artifact 禁止包含 contact"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "TaskArtifact":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
