from collections.abc import Mapping

from agent.domain.enums import AgentRunStatus, FeedbackStatus
from agent.domain.errors import InvalidStatusTransitionError


# 状态转换集中在领域层，Repository 和后续 LangGraph 都不能自行发明跳转。
_FEEDBACK_TRANSITIONS: Mapping[FeedbackStatus, frozenset[FeedbackStatus]] = {
    FeedbackStatus.PENDING: frozenset({FeedbackStatus.CLAIMED}),
    FeedbackStatus.CLAIMED: frozenset(
        {FeedbackStatus.GATING, FeedbackStatus.NEEDS_HUMAN, FeedbackStatus.FAILED}
    ),
    FeedbackStatus.GATING: frozenset(
        {
            FeedbackStatus.REJECTED_IRRELEVANT,
            FeedbackStatus.QUARANTINED_SECURITY,
            FeedbackStatus.ISSUE_REQUIRED,
            FeedbackStatus.PUBLISHING_ISSUE,
            FeedbackStatus.OUT_OF_SCOPE,
            FeedbackStatus.NEEDS_HUMAN,
            FeedbackStatus.DUPLICATE,
            FeedbackStatus.REPRODUCING,
            FeedbackStatus.FAILED,
        }
    ),
    FeedbackStatus.REPRODUCING: frozenset(
        {
            FeedbackStatus.REPAIRING,
            FeedbackStatus.CANNOT_REPRODUCE,
            FeedbackStatus.SECURITY_REJECTED,
            FeedbackStatus.FAILED,
        }
    ),
    FeedbackStatus.REPAIRING: frozenset(
        {
            FeedbackStatus.VALIDATING,
            FeedbackStatus.NEEDS_HUMAN,
            FeedbackStatus.SECURITY_REJECTED,
            FeedbackStatus.FAILED,
        }
    ),
    FeedbackStatus.VALIDATING: frozenset(
        {
            FeedbackStatus.VALIDATED,
            FeedbackStatus.SECURITY_REJECTED,
            FeedbackStatus.FAILED,
        }
    ),
    FeedbackStatus.VALIDATED: frozenset(
        {FeedbackStatus.PUBLISHING, FeedbackStatus.STALE_BASE}
    ),
    FeedbackStatus.PUBLISHING: frozenset(
        {FeedbackStatus.PR_OPENED, FeedbackStatus.STALE_BASE, FeedbackStatus.FAILED}
    ),
    FeedbackStatus.PUBLISHING_ISSUE: frozenset(
        {FeedbackStatus.ISSUE_OPENED, FeedbackStatus.FAILED}
    ),
    FeedbackStatus.STALE_BASE: frozenset(
        {FeedbackStatus.PENDING, FeedbackStatus.NEEDS_HUMAN}
    ),
}

_AGENT_RUN_TRANSITIONS: Mapping[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.CREATED: frozenset({AgentRunStatus.GATING}),
    AgentRunStatus.GATING: frozenset(
        {
            AgentRunStatus.PREPARING_SOURCE,
            AgentRunStatus.PUBLISHING_ISSUE,
            AgentRunStatus.COMPLETED,
        }
    ),
    AgentRunStatus.PREPARING_SOURCE: frozenset({AgentRunStatus.REPRODUCING}),
    AgentRunStatus.REPRODUCING: frozenset(
        {AgentRunStatus.REPAIRING, AgentRunStatus.COMPLETED}
    ),
    AgentRunStatus.REPAIRING: frozenset(
        {AgentRunStatus.VALIDATING, AgentRunStatus.COMPLETED}
    ),
    AgentRunStatus.VALIDATING: frozenset(
        {AgentRunStatus.PUBLISHING, AgentRunStatus.COMPLETED}
    ),
    AgentRunStatus.PUBLISHING: frozenset(
        {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.STALE_BASE,
            AgentRunStatus.FAILED,
        }
    ),
    AgentRunStatus.PUBLISHING_ISSUE: frozenset(
        {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}
    ),
}

_AGENT_FAILURE_TERMINALS = frozenset(
    {
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.BUDGET_EXHAUSTED,
        AgentRunStatus.SECURITY_REJECTED,
    }
)


def ensure_feedback_transition(
    current: FeedbackStatus,
    target: FeedbackStatus,
) -> None:
    if target not in _FEEDBACK_TRANSITIONS.get(current, frozenset()):
        raise InvalidStatusTransitionError(
            f"feedback status cannot transition from {current.value} to {target.value}"
        )


def ensure_agent_run_transition(
    current: AgentRunStatus,
    target: AgentRunStatus,
) -> None:
    allowed = _AGENT_RUN_TRANSITIONS.get(current, frozenset())
    if target in allowed:
        return
    # 活动节点都允许进入统一失败终态；完成后的 run 不允许再次被打开。
    if current not in {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.BUDGET_EXHAUSTED,
        AgentRunStatus.SECURITY_REJECTED,
        AgentRunStatus.STALE_BASE,
    } and target in _AGENT_FAILURE_TERMINALS:
        return
    raise InvalidStatusTransitionError(
        f"agent run status cannot transition from {current.value} to {target.value}"
    )
