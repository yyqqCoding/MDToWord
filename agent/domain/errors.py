class AgentError(Exception):
    """Base class for expected Agent failures with stable error codes."""

    error_code = "agent_error"


class ConfigurationError(AgentError):
    error_code = "configuration_error"


class InvalidStatusTransitionError(AgentError):
    error_code = "invalid_status_transition"


class FeedbackNotFoundError(AgentError):
    error_code = "feedback_not_found"


class DuplicateFeedbackError(AgentError):
    error_code = "duplicate_feedback_id"


class ClaimTokenMismatchError(AgentError):
    error_code = "claim_token_mismatch"


class ConcurrentFeedbackUpdateError(AgentError):
    error_code = "concurrent_feedback_update"


class RepositoryError(AgentError):
    error_code = "repository_error"


class SourceRevisionError(AgentError):
    error_code = "source_revision_error"


class InvalidArtifactPathError(AgentError):
    error_code = "invalid_artifact_path"
