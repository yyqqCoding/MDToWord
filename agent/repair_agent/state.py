"""Repair Agent 私有 checkpoint 状态。"""

from operator import add
from typing import Annotated, Literal

from langchain.agents import AgentState


RepairPhase = Literal["reproducing", "repairing"]
RepairTerminal = Literal["completed", "blocked"]


def _merge_latest(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    return {**left, **right}


class RepairAgentState(AgentState[None], total=False):
    """消息可总结，业务事实由工具写入这些独立字段。"""

    phase: RepairPhase
    run_id: str
    feedback_id: str
    base_sha: str
    source_snapshot_ref: str
    test_patch_ref: str | None
    fix_patch_ref: str | None
    target_test_selector: str | None
    expected_failure_kind: str | None
    reproduction_result_ref: str | None
    repair_result_ref: str | None
    fix_summary: str | None
    fix_risk: str | None
    reproduction_confirmed: bool
    repair_confirmed: bool
    terminal: RepairTerminal | None
    blocked_code: str | None
    blocked_summary: str | None
    reproduction_round: int
    repair_round: int
    last_sandbox_summary: dict[str, object]
    model_calls: Annotated[int, add]
    tool_calls: Annotated[int, add]
    sandbox_duration_ms: Annotated[int, add]
    input_tokens: Annotated[int, add]
    output_tokens: Annotated[int, add]
    total_tokens: Annotated[int, add]
    cache_read_tokens: Annotated[int, add]
    summary_failures: Annotated[int, add]
    premature_final_count: Annotated[int, add]
    diagnostics: Annotated[dict[str, object], _merge_latest]
