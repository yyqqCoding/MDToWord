"""Publisher 的严格输入输出契约；GitHub 不能接触原始反馈或主机路径。"""

import hashlib
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.domain.enums import GateArea, GateCategory, GateIntent, RiskLevel
from agent.domain.repair import ValidationResult
from agent.workspace.patch_policy import PatchPolicy


class PublicationDisposition(StrEnum):
    PR_OPENED = "pr_opened"
    STALE_BASE = "stale_base"


class PublicationFile(BaseModel):
    """应用 validated.patch 后要写入 GitHub Tree 的单个受控文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=300)
    content: bytes | None


class PublicationEvidence(BaseModel):
    """可公开的审查证据；结构中故意不提供描述、Markdown 或联系方式字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: GateCategory
    risk: RiskLevel
    graph_version: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)
    prompt_versions: dict[str, str]
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost: str = Field(pattern=r"^\d+(?:\.\d+)?$")
    extension_sync_required: bool = False
    trace_id: str = Field(min_length=1, max_length=200)
    trace_url: str | None = Field(default=None, max_length=1000)


class PublicationRequest(BaseModel):
    """只允许通过验证且内容哈希一致的 Artifact 到达 Publisher。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: UUID
    validation: ValidationResult
    validated_patch: bytes
    files: tuple[PublicationFile, ...] = Field(min_length=1, max_length=10)
    evidence: PublicationEvidence

    @model_validator(mode="after")
    def validate_publishable_artifacts(self) -> "PublicationRequest":
        if not self.validation.passed:
            raise ValueError("publisher requires a passed validation result")
        patch_hash = hashlib.sha256(self.validated_patch).hexdigest()
        if patch_hash != self.validation.validated_patch_sha256:
            raise ValueError("validated patch hash does not match validation result")
        paths = tuple(sorted(item.path for item in self.files))
        if len(paths) != len(set(paths)):
            raise ValueError("publication files must be unique")
        if paths != tuple(sorted(self.validation.changed_files)):
            raise ValueError("publication files do not match validated changed files")

        policy = PatchPolicy.load_default()
        for path in paths:
            phase = "fix" if path.startswith("backend/app/") else "test"
            policy.authorize_write(path, phase)
        return self


class PublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: PublicationDisposition
    branch: str = Field(min_length=1, max_length=255)
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    pr_number: int | None = Field(default=None, ge=1)
    pr_url: str | None = Field(default=None, max_length=1000)
    reused: bool = False

    @model_validator(mode="after")
    def validate_disposition_fields(self) -> "PublicationResult":
        if self.disposition is PublicationDisposition.PR_OPENED:
            if self.commit_sha is None or self.pr_number is None or self.pr_url is None:
                raise ValueError("opened publication requires commit and pull request fields")
        elif any(value is not None for value in (self.commit_sha, self.pr_number, self.pr_url)):
            raise ValueError("stale publication cannot contain GitHub side effects")
        return self


class PullRequestPublisher(Protocol):
    async def publish(self, request: PublicationRequest) -> PublicationResult: ...


class IssueDraft(BaseModel):
    """模型生成、Policy 复核后的最小公开 Issue 内容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=600)
    intent: GateIntent
    area: GateArea
    category: GateCategory

    @model_validator(mode="after")
    def validate_issue_kind(self) -> "IssueDraft":
        if self.intent not in {GateIntent.BUG_REPORT, GateIntent.FEATURE_REQUEST}:
            raise ValueError("issue draft intent must be bug_report or feature_request")
        if self.area not in {
            GateArea.BACKEND,
            GateArea.EXTENSION,
            GateArea.CROSS_COMPONENT,
        }:
            raise ValueError("issue draft area must identify an owned component")
        if "\n" in self.title or "\r" in self.title:
            raise ValueError("issue title must be a single line")
        return self


class IssuePublicationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_version: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=80)
    prompt_versions: dict[str, str]
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    trace_url: str | None = Field(default=None, max_length=1000)


class IssuePublicationRequest(BaseModel):
    """不含原始用户字段、源码或 Patch 的 Issue Publisher 输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: UUID
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_ref: str = Field(pattern=r"^[0-9a-f]{12}$")
    draft: IssueDraft
    evidence: IssuePublicationEvidence


class IssuePublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: str = Field(default="issue_opened", pattern=r"^issue_opened$")
    issue_number: int = Field(ge=1)
    issue_url: str = Field(min_length=1, max_length=1000)
    reused: bool = False


class IssuePublisher(Protocol):
    async def publish_issue(
        self,
        request: IssuePublicationRequest,
    ) -> IssuePublicationResult: ...
