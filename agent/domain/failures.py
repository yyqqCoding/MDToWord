"""失败事实、短传输重试策略和脱敏记录边界。"""

from __future__ import annotations

import logging
import math
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.domain import errors


_LOGGER = logging.getLogger(__name__)
_MAX_SAFE_DETAIL_KEYS = 8
_MAX_SAFE_DETAIL_TEXT = 1024
_MAX_SAFE_DETAIL_INTEGER = (2**63) - 1


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    INVALID = "invalid"
    BUSINESS = "business"
    SECURITY = "security"
    PERMANENT = "permanent"


class RetryDecision(StrEnum):
    RETRY = "retry"
    STOP = "stop"


class FailureHandling(StrEnum):
    TRANSPORT_RETRY = "transport_retry"
    FORMAT_REVISE = "format_revise"
    GRAPH_REVISE = "graph_revise"
    TRUSTED_FALLBACK = "trusted_fallback"
    STALE_REQUEUE = "stale_requeue"
    STOP = "stop"


SafeScalar = str | int | bool | None


class FailureCause(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: FailureKind
    component: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    safe_details: dict[str, SafeScalar] = Field(default_factory=dict)

    @field_validator("safe_details")
    @classmethod
    def validate_safe_details(
        cls,
        value: dict[str, SafeScalar],
    ) -> dict[str, SafeScalar]:
        if len(value) > _MAX_SAFE_DETAIL_KEYS:
            raise ValueError("safe_details has too many keys")
        clean: dict[str, SafeScalar] = {}
        for key, item in value.items():
            if not key or len(key) > 64 or not key.replace("_", "a").isalnum():
                raise ValueError("safe_details key is invalid")
            if isinstance(item, str):
                clean[key] = item[:_MAX_SAFE_DETAIL_TEXT]
            elif item is None or isinstance(item, bool):
                clean[key] = item
            elif isinstance(item, int):
                if abs(item) > _MAX_SAFE_DETAIL_INTEGER:
                    raise ValueError("safe_details integer is out of range")
                clean[key] = item
            else:
                raise ValueError("safe_details values must be scalar")
        return clean


class LocatedFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cause: FailureCause
    phase: str = Field(min_length=1, max_length=80)
    node: str = Field(min_length=1, max_length=120)


class RetryContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=3)
    budget_remaining: bool = True
    deadline_remaining_seconds: float | None = Field(default=None, ge=0)
    operation_id: str = Field(min_length=1, max_length=200)
    idempotent: bool

    @model_validator(mode="after")
    def require_valid_attempt(self) -> "RetryContext":
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        return self


class RetryPolicy:
    """相同输入短传输重试的纯本地 Strategy。"""

    def decide(
        self,
        failure: FailureCause,
        context: RetryContext,
        *,
        delay_seconds: float = 0,
    ) -> RetryDecision:
        if (
            failure.kind is not FailureKind.TRANSIENT
            or not context.idempotent
            or not context.budget_remaining
            or context.attempt >= context.max_attempts
        ):
            return RetryDecision.STOP
        remaining = context.deadline_remaining_seconds
        if remaining is not None and remaining <= max(0.0, delay_seconds):
            return RetryDecision.STOP
        return RetryDecision.RETRY


class FailureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: FailureKind
    component: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    phase: str = Field(min_length=1, max_length=80)
    node: str = Field(min_length=1, max_length=120)
    handling: FailureHandling
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=3)
    safe_details: dict[str, SafeScalar] = Field(default_factory=dict)

    @field_validator("safe_details")
    @classmethod
    def validate_safe_details(
        cls,
        value: dict[str, SafeScalar],
    ) -> dict[str, SafeScalar]:
        return FailureCause.validate_safe_details(value)

    @model_validator(mode="after")
    def require_valid_attempt(self) -> "FailureSnapshot":
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        return self

    @classmethod
    def final(
        cls,
        failure: LocatedFailure,
        *,
        attempt: int,
        max_attempts: int,
    ) -> "FailureSnapshot":
        return cls(
            **failure.cause.model_dump(),
            phase=failure.phase,
            node=failure.node,
            handling=FailureHandling.STOP,
            attempt=attempt,
            max_attempts=max_attempts,
        )


class FailureEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure: LocatedFailure
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=3)
    handling: FailureHandling
    delay_seconds: float | None = Field(default=None, ge=0, le=10)
    deadline_remaining_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_valid_attempt(self) -> "FailureEvent":
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        return self


class FailureEventSink(Protocol):
    def record_failure(self, event: FailureEvent) -> None: ...


class FailureRecorder:
    """脱敏日志与Telemetry的fail-open Observer。"""

    def __init__(self, sink: FailureEventSink | None = None) -> None:
        self._sink = sink

    def record(self, event: FailureEvent) -> None:
        try:
            failure = event.failure
            _LOGGER.warning(
                "agent failure code=%s kind=%s component=%s operation=%s "
                "phase=%s node=%s attempt=%s max_attempts=%s handling=%s "
                "safe_details=%s",
                failure.cause.code,
                failure.cause.kind.value,
                failure.cause.component,
                failure.cause.operation,
                failure.phase,
                failure.node,
                event.attempt,
                event.max_attempts,
                event.handling.value,
                failure.cause.safe_details,
            )
            if self._sink is not None:
                self._sink.record_failure(event)
        except Exception as exc:  # pragma: no cover - observer safety boundary
            _LOGGER.warning("failure recorder failed: %s", type(exc).__name__)


_KIND_BY_CODE: dict[str, FailureKind] = {
    "agent_run_not_found": FailureKind.PERMANENT,
    "auth_error": FailureKind.PERMANENT,
    "baseline_reproduction_failed": FailureKind.BUSINESS,
    "budget_exhausted": FailureKind.BUSINESS,
    "checkpoint_configuration_error": FailureKind.PERMANENT,
    "claim_token_mismatch": FailureKind.PERMANENT,
    "concurrent_feedback_update": FailureKind.PERMANENT,
    "configuration_error": FailureKind.PERMANENT,
    "context_too_large": FailureKind.PERMANENT,
    "docx_validation_failed": FailureKind.BUSINESS,
    "duplicate_agent_run_id": FailureKind.PERMANENT,
    "duplicate_feedback_id": FailureKind.PERMANENT,
    "external_dependency_required": FailureKind.PERMANENT,
    "feedback_not_found": FailureKind.PERMANENT,
    "fix_edit_security_rejected": FailureKind.SECURITY,
    "full_validation_failed": FailureKind.BUSINESS,
    "invalid_artifact_path": FailureKind.SECURITY,
    "invalid_edit": FailureKind.INVALID,
    "invalid_fix_edit": FailureKind.INVALID,
    "invalid_response": FailureKind.INVALID,
    "invalid_status_transition": FailureKind.PERMANENT,
    "invalid_target_result": FailureKind.INVALID,
    "invalid_test_infrastructure": FailureKind.INVALID,
    "invalid_test_edit": FailureKind.INVALID,
    "publication_auth_error": FailureKind.PERMANENT,
    "publication_conflict": FailureKind.PERMANENT,
    "publication_failed": FailureKind.PERMANENT,
    "issue_publication_failed": FailureKind.PERMANENT,
    "non_target_failure": FailureKind.BUSINESS,
    "provider_unavailable": FailureKind.TRANSIENT,
    "rate_limit": FailureKind.TRANSIENT,
    "repository_error": FailureKind.PERMANENT,
    "repository_unavailable": FailureKind.TRANSIENT,
    "safety_refusal": FailureKind.PERMANENT,
    "sandbox_auth_error": FailureKind.PERMANENT,
    "sandbox_execution_error": FailureKind.BUSINESS,
    "sandbox_invalid_response": FailureKind.PERMANENT,
    "sandbox_job_conflict": FailureKind.PERMANENT,
    "sandbox_request_rejected": FailureKind.PERMANENT,
    "sandbox_security_rejected": FailureKind.SECURITY,
    "sandbox_timeout": FailureKind.BUSINESS,
    "sandbox_unavailable": FailureKind.TRANSIENT,
    "skipped_tests_increased": FailureKind.BUSINESS,
    "source_access_denied": FailureKind.SECURITY,
    "source_auth_error": FailureKind.PERMANENT,
    "source_revision_error": FailureKind.PERMANENT,
    "source_snapshot_error": FailureKind.PERMANENT,
    "stale_base": FailureKind.BUSINESS,
    "test_edit_security_rejected": FailureKind.SECURITY,
    "target_not_collected": FailureKind.INVALID,
    "target_passed": FailureKind.BUSINESS,
    "target_skipped": FailureKind.BUSINESS,
    "target_validation_failed": FailureKind.BUSINESS,
    "target_validation_timeout": FailureKind.BUSINESS,
    "timeout": FailureKind.TRANSIENT,
    "tool_not_authorized": FailureKind.SECURITY,
    "unexpected_target_error": FailureKind.BUSINESS,
    "unexpected_error": FailureKind.PERMANENT,
    "workspace_modified": FailureKind.SECURITY,
    "workspace_diff_mismatch": FailureKind.SECURITY,
}


def failure_cause_from_code(
    code: str,
    *,
    component: str,
    operation: str,
    safe_details: dict[str, SafeScalar] | None = None,
) -> FailureCause:
    """从受信本地结论构造Cause；未登记code安全降级为unexpected_error。"""

    if code not in _KIND_BY_CODE:
        code = "unexpected_error"
        safe_details = None
    return FailureCause(
        code=code,
        kind=_KIND_BY_CODE[code],
        component=component,
        operation=operation,
        safe_details=safe_details or {},
    )


def failure_cause_from_exception(
    exc: BaseException,
    *,
    operation: str | None = None,
) -> FailureCause:
    code = getattr(exc, "error_code", "unexpected_error")
    if code not in _KIND_BY_CODE:
        code = "unexpected_error"
    details = dict(getattr(exc, "safe_details", {}) or {})
    if code == "unexpected_error":
        details = {"error_type": type(exc).__name__[:120]}
    return failure_cause_from_code(
        code,
        component=_component_for_exception(exc),
        operation=(getattr(exc, "operation", None) or operation or "unknown_operation"),
        safe_details=details,
    )


def located_failure_from_exception(
    exc: BaseException,
    *,
    operation: str,
    phase: str,
    node: str,
) -> LocatedFailure:
    return LocatedFailure(
        cause=failure_cause_from_exception(exc, operation=operation),
        phase=getattr(exc, "phase", None) or phase,
        node=getattr(exc, "node", None) or node,
    )


def retry_delay(attempt: int, retry_after: float | None = None) -> float:
    delay = min(1.0 * (2 ** (attempt - 1)), 10.0)
    if retry_after is None or not math.isfinite(retry_after) or retry_after < 0:
        return delay
    return min(max(delay, retry_after), 10.0)


def _component_for_exception(exc: BaseException) -> str:
    if isinstance(exc, (errors.ModelProviderError, errors.InvalidModelResponseError)):
        return "provider"
    if isinstance(
        exc,
        (
            errors.SandboxAuthenticationError,
            errors.SandboxJobConflictError,
            errors.SandboxExecutionError,
            errors.SandboxUnavailableError,
            errors.SandboxRequestRejectedError,
            errors.SandboxInvalidResponseError,
        ),
    ):
        return "sandbox"
    if isinstance(exc, errors.PublicationError):
        return "publisher"
    if isinstance(
        exc,
        (
            errors.RepositoryError,
            errors.SourceRevisionError,
            errors.SourceSnapshotError,
        ),
    ):
        return "repository"
    if isinstance(exc, (errors.PatchPolicyError, errors.ToolAuthorizationError)):
        return "policy"
    return "runtime"
