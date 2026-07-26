"""状态机(docs/AgentRequirements/00-overview/architecture.md §4)。

只做转换合法性校验;持久化由 FeedbackRepository 负责。
"""

from __future__ import annotations

from agent.exceptions import InvalidTransitionError

# §4.1 Feedback 状态
FEEDBACK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "claimed", "invalid", "duplicate"}),
    "approved": frozenset({"claimed", "invalid", "duplicate"}),
    # 超时 claimed -> claimed 的再领取由 RPC 在 SQL 层判定
    "claimed": frozenset({
        "classified", "invalid", "duplicate", "needs_human",
        "needs_extension_release", "failed", "claimed",
    }),
    "classified": frozenset({
        "reproducing", "invalid", "duplicate", "needs_human",
        "needs_extension_release", "failed",
    }),
    "reproducing": frozenset({"repairing", "needs_human", "failed"}),
    "repairing": frozenset({"validating", "needs_human", "failed"}),
    "validating": frozenset({
        "pr_opened", "validated_but_unpublished", "security_rejected", "failed",
    }),
    "validated_but_unpublished": frozenset({"pr_opened", "failed"}),
    "pr_opened": frozenset({"resolved"}),
    "failed": frozenset({"claimed", "needs_human"}),
    # 终态
    "resolved": frozenset(),
    "invalid": frozenset(),
    "duplicate": frozenset(),
    "needs_human": frozenset(),
    "needs_extension_release": frozenset(),
    "security_rejected": frozenset(),
}

# §4.2 Agent Run 状态(线性主链 + 任意节点可 failed/cancelled)
RUN_ORDER = [
    "created", "fetching_context", "classifying", "generating_test",
    "verifying_reproduction", "generating_fix", "validating",
    "ready_for_pr", "pr_created",
]
RUN_TERMINAL = frozenset({"pr_created", "failed", "cancelled"})


def feedback_can_transition(current: str, target: str) -> bool:
    return target in FEEDBACK_TRANSITIONS.get(current, frozenset())


def assert_feedback_transition(current: str, target: str) -> None:
    if not feedback_can_transition(current, target):
        raise InvalidTransitionError(f"feedback 状态不允许 {current} -> {target}")


def run_can_transition(current: str, target: str) -> bool:
    if current in RUN_TERMINAL:
        return False
    if target in ("failed", "cancelled"):
        return True
    try:
        return RUN_ORDER.index(target) == RUN_ORDER.index(current) + 1
    except ValueError:
        return False


def assert_run_transition(current: str, target: str) -> None:
    if not run_can_transition(current, target):
        raise InvalidTransitionError(f"agent_run 状态不允许 {current} -> {target}")
