import pytest

from agent.exceptions import InvalidTransitionError
from agent.state_machine import (
    FEEDBACK_TRANSITIONS,
    assert_feedback_transition,
    assert_run_transition,
    feedback_can_transition,
    run_can_transition,
)


def test_happy_path_feedback_transitions():
    path = ["pending", "claimed", "classified", "reproducing",
            "repairing", "validating", "pr_opened", "resolved"]
    for current, target in zip(path, path[1:]):
        assert feedback_can_transition(current, target), f"{current} -> {target}"


def test_security_rejected_and_unpublished_paths():
    assert feedback_can_transition("validating", "security_rejected")
    assert feedback_can_transition("validating", "validated_but_unpublished")
    assert feedback_can_transition("validated_but_unpublished", "pr_opened")


def test_invalid_feedback_transition_raises():
    with pytest.raises(InvalidTransitionError):
        assert_feedback_transition("pending", "pr_opened")
    with pytest.raises(InvalidTransitionError):
        assert_feedback_transition("resolved", "claimed")


def test_terminal_states_have_no_outgoing():
    for terminal in ("resolved", "invalid", "duplicate", "needs_human",
                     "needs_extension_release", "security_rejected"):
        assert FEEDBACK_TRANSITIONS[terminal] == frozenset()


def test_pr_opened_only_allows_resolved():
    assert FEEDBACK_TRANSITIONS["pr_opened"] == frozenset({"resolved"})


def test_run_transitions_follow_linear_order():
    assert run_can_transition("created", "fetching_context")
    assert run_can_transition("classifying", "generating_test")
    assert not run_can_transition("created", "classifying")  # 跳级禁止
    assert not run_can_transition("pr_created", "failed")    # 终态

    assert run_can_transition("generating_fix", "failed")
    assert run_can_transition("created", "cancelled")
    with pytest.raises(InvalidTransitionError):
        assert_run_transition("validating", "classifying")
