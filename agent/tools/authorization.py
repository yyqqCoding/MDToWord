"""按 Graph 节点固定可见工具，拒绝模型跨阶段调用能力。"""

from enum import StrEnum

from agent.domain.errors import ToolAuthorizationError


class ToolName(StrEnum):
    SEARCH_SOURCE = "search_source"
    READ_SOURCE_FILE = "read_source_file"
    SUBMIT_TEST_EDITS = "submit_test_edits"
    RUN_REPRODUCTION = "run_reproduction"
    SUBMIT_FIX_EDITS = "submit_fix_edits"
    RUN_TARGET_VALIDATION = "run_target_validation"


class ToolNode(StrEnum):
    GATE = "gate"
    REPRODUCTION_INSPECT = "reproduction_inspect"
    TEST_EDIT = "test_edit"
    REPRODUCTION_RUN = "reproduction_run"
    FIX_EDIT = "fix_edit"
    TARGET_VALIDATION = "target_validation"


# Gate 明确保持空集合；后续节点只得到完成本步所需的最小能力。
_AUTHORIZED: dict[ToolNode, frozenset[ToolName]] = {
    ToolNode.GATE: frozenset(),
    ToolNode.REPRODUCTION_INSPECT: frozenset(
        {ToolName.SEARCH_SOURCE, ToolName.READ_SOURCE_FILE}
    ),
    ToolNode.TEST_EDIT: frozenset({ToolName.SUBMIT_TEST_EDITS}),
    ToolNode.REPRODUCTION_RUN: frozenset({ToolName.RUN_REPRODUCTION}),
    ToolNode.FIX_EDIT: frozenset({ToolName.SUBMIT_FIX_EDITS}),
    ToolNode.TARGET_VALIDATION: frozenset({ToolName.RUN_TARGET_VALIDATION}),
}


def authorize_tool(node: ToolNode, requested_tool: str) -> ToolName:
    try:
        tool = ToolName(requested_tool)
    except ValueError as exc:
        raise ToolAuthorizationError("tool is not registered") from exc
    if tool not in _AUTHORIZED[node]:
        raise ToolAuthorizationError("tool is not authorized for this node")
    return tool
