from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.domain.enums import AgentRunStatus, FeedbackStatus
from agent.domain.errors import InvalidStatusTransitionError
from agent.domain.transitions import ensure_agent_run_transition, ensure_feedback_transition
from agent.state import AgentState


def test_agent_state_contains_only_recoverable_metadata():
    state = AgentState(
        run_id=uuid4(),
        feedback_id=uuid4(),
        claim_token=uuid4(),
        trace_id="trace-123",
        status=AgentRunStatus.CREATED,
    )

    dumped = state.model_dump(mode="json")

    assert dumped["schema_version"] == 1
    assert "markdown_content" not in dumped
    assert "contact" not in dumped


def test_agent_state_rejects_large_user_fields():
    with pytest.raises(ValidationError):
        AgentState(
            run_id=uuid4(),
            feedback_id=uuid4(),
            claim_token=uuid4(),
            trace_id="trace-123",
            status=AgentRunStatus.CREATED,
            markdown_content="untrusted",
        )


def test_feedback_transition_supports_single_stale_base_requeue_path():
    ensure_feedback_transition(FeedbackStatus.PUBLISHING, FeedbackStatus.STALE_BASE)
    ensure_feedback_transition(FeedbackStatus.STALE_BASE, FeedbackStatus.PENDING)


def test_feedback_transition_rejects_skipping_validation():
    with pytest.raises(InvalidStatusTransitionError):
        ensure_feedback_transition(FeedbackStatus.REPAIRING, FeedbackStatus.PUBLISHING)


def test_non_repair_agent_run_can_complete_after_gate():
    ensure_agent_run_transition(AgentRunStatus.GATING, AgentRunStatus.COMPLETED)
