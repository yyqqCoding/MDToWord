import pytest
from pydantic import ValidationError

from agent.domain.errors import ModelAuthError, ModelTimeoutError
from agent.domain.failures import (
    FailureCause,
    FailureEvent,
    FailureHandling,
    FailureKind,
    FailureRecorder,
    LocatedFailure,
    RetryContext,
    RetryDecision,
    RetryPolicy,
    failure_cause_from_code,
    failure_cause_from_exception,
    retry_delay,
)


def test_retry_policy_only_retries_transient_idempotent_attempts():
    policy = RetryPolicy()
    timeout = failure_cause_from_exception(
        ModelTimeoutError("safe", operation="gate")
    )

    assert policy.decide(
        timeout,
        RetryContext(
            attempt=1,
            max_attempts=3,
            operation_id="gate",
            idempotent=True,
        ),
        delay_seconds=1,
    ) is RetryDecision.RETRY
    assert policy.decide(
        timeout,
        RetryContext(
            attempt=3,
            max_attempts=3,
            operation_id="gate",
            idempotent=True,
        ),
        delay_seconds=2,
    ) is RetryDecision.STOP

    auth = failure_cause_from_exception(ModelAuthError("safe", operation="gate"))
    assert policy.decide(
        auth,
        RetryContext(
            attempt=1,
            max_attempts=3,
            operation_id="gate",
            idempotent=True,
        ),
    ) is RetryDecision.STOP


def test_retry_policy_stops_when_deadline_cannot_fit_delay():
    failure = failure_cause_from_exception(ModelTimeoutError("safe"))

    decision = RetryPolicy().decide(
        failure,
        RetryContext(
            attempt=1,
            max_attempts=3,
            deadline_remaining_seconds=0.5,
            operation_id="gate",
            idempotent=True,
        ),
        delay_seconds=1,
    )

    assert decision is RetryDecision.STOP


def test_retry_delay_is_exponential_and_retry_after_is_bounded():
    assert retry_delay(1) == 1
    assert retry_delay(2) == 2
    assert retry_delay(2, 7) == 7
    assert retry_delay(2, 50) == 10
    assert retry_delay(2, -1) == 2


def test_safe_details_reject_nested_untrusted_values():
    with pytest.raises(ValidationError):
        FailureCause(
            code="invalid_response",
            kind=FailureKind.INVALID,
            component="provider",
            operation="gate",
            safe_details={"nested": {"secret": "value"}},
        )

    with pytest.raises(ValidationError):
        FailureCause(
            code="invalid_response",
            kind=FailureKind.INVALID,
            component="provider",
            operation="gate",
            safe_details={"http_status": 2**64},
        )


def test_failure_code_registry_is_stable_and_unknown_codes_fail_closed():
    business = failure_cause_from_code(
        "target_passed",
        component="sandbox",
        operation="classify_reproduction",
    )
    correctable_source_request = failure_cause_from_code(
        "source_request_invalid",
        component="runtime",
        operation="read_source_file",
    )
    unknown = failure_cause_from_code(
        "new_unreviewed_code",
        component="runtime",
        operation="controller_run",
        safe_details={"raw": "must-not-survive"},
    )

    assert business.kind is FailureKind.BUSINESS
    assert correctable_source_request.kind is FailureKind.INVALID
    assert unknown.code == "unexpected_error"
    assert unknown.kind is FailureKind.PERMANENT
    assert unknown.safe_details == {}


def test_unknown_exception_only_records_its_type():
    failure = failure_cause_from_exception(
        RuntimeError("MODEL_API_KEY=must-not-appear"),
        operation="controller_run",
    )

    assert failure.code == "unexpected_error"
    assert failure.kind is FailureKind.PERMANENT
    assert failure.safe_details == {"error_type": "RuntimeError"}
    assert "must-not-appear" not in str(failure.model_dump())


def test_tool_precondition_is_invalid_not_security():
    from agent.domain.errors import ToolPreconditionError

    failure = failure_cause_from_exception(
        ToolPreconditionError(
            "submit a fix first",
            safe_details={"required_action": "submit_fix_edits"},
        ),
        operation="run_sandbox",
    )

    assert failure.code == "tool_precondition_failed"
    assert failure.kind is FailureKind.INVALID
    assert failure.safe_details == {"required_action": "submit_fix_edits"}


def test_failure_recorder_is_fail_open_when_observer_raises(caplog):
    class FailingSink:
        def record_failure(self, event: FailureEvent) -> None:
            del event
            raise RuntimeError("observer unavailable")

    cause = failure_cause_from_exception(
        ModelTimeoutError("safe", safe_details={"http_status": 408})
    )
    event = FailureEvent(
        failure=LocatedFailure(cause=cause, phase="gating", node="classify_gate"),
        attempt=1,
        max_attempts=3,
        handling=FailureHandling.TRANSPORT_RETRY,
        delay_seconds=1,
    )

    with caplog.at_level("WARNING"):
        FailureRecorder(FailingSink()).record(event)

    assert "safe_details={'http_status': 408}" in caplog.text
