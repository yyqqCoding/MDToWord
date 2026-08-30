from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.enums import AgentRunStatus, RiskLevel


class UsageTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)


class AgentState(BaseModel):
    """可持久化的小型状态；大对象和用户原文只保存受控 Artifact 引用。"""

    model_config = ConfigDict(extra="forbid")

    # v3 直接替换旧计划 JSON 路径；旧 checkpoint 不跨不兼容 Schema 恢复。
    schema_version: Literal[3] = 3
    run_id: UUID
    feedback_id: UUID
    # claim token 是恢复条件更新所需的租约能力，只存放在私有 checkpoint 中。
    claim_token: UUID
    trace_id: str = Field(min_length=1, max_length=200)
    status: AgentRunStatus
    dry_run: bool = True
    route: str | None = None
    area: str | None = None
    category: str | None = None
    risk: RiskLevel = RiskLevel.UNKNOWN
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    extension_version: str = "unknown"
    task_artifact_ref: str | None = None
    source_snapshot_ref: str | None = None
    gate_result_ref: str | None = None
    reproduction_plan_ref: str | None = None
    target_test_selector: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]{1,80}$",
    )
    expected_failure_kind: str | None = None
    agent_blocked_code: str | None = None
    agent_blocked_summary: str | None = None
    agent_final_phase: str | None = None
    test_patch_ref: str | None = None
    reproduction_result_ref: str | None = None
    fix_patch_ref: str | None = None
    repair_result_ref: str | None = None
    validation_result_ref: str | None = None
    publication_result_ref: str | None = None
    issue_publication_result_ref: str | None = None
    fix_summary: str | None = Field(default=None, max_length=1000)
    fix_source_paths: tuple[str, ...] = ()
    reproduction_round: int = Field(default=0, ge=0)
    repair_round: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    sandbox_duration_ms: int = Field(default=0, ge=0)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    validated_patch_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pr_url: str | None = None
    issue_url: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
