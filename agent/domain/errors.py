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


class InvalidModelResponseError(AgentError):
    error_code = "invalid_response"


class ModelProviderError(AgentError):
    """模型厂商错误的稳定边界；消息不得包含响应正文或请求凭证。"""

    error_code = "provider_unavailable"


class ModelAuthError(ModelProviderError):
    error_code = "auth_error"


class ModelRateLimitError(ModelProviderError):
    error_code = "rate_limit"


class ModelTimeoutError(ModelProviderError):
    error_code = "timeout"


class ModelContextTooLargeError(ModelProviderError):
    error_code = "context_too_large"


class ModelSafetyRefusalError(ModelProviderError):
    error_code = "safety_refusal"


class AgentRunNotFoundError(AgentError):
    error_code = "agent_run_not_found"


class DuplicateAgentRunError(AgentError):
    error_code = "duplicate_agent_run_id"


class CheckpointConfigurationError(AgentError):
    error_code = "checkpoint_configuration_error"
