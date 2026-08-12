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


class SourceSnapshotError(AgentError):
    error_code = "source_snapshot_error"


class SourceAccessError(AgentError):
    error_code = "source_access_denied"


class InvalidEditError(AgentError):
    error_code = "invalid_edit"


class PatchPolicyError(AgentError):
    error_code = "patch_policy_rejected"


class ExternalDependencyError(PatchPolicyError):
    """修复需要当前固定运行环境之外的依赖，应交由人工评估部署变更。"""

    error_code = "external_dependency_required"


class SandboxAuthenticationError(AgentError):
    error_code = "sandbox_auth_error"


class SandboxJobConflictError(AgentError):
    error_code = "sandbox_job_conflict"


class SandboxExecutionError(AgentError):
    error_code = "sandbox_execution_error"


class SandboxUnavailableError(AgentError):
    error_code = "sandbox_unavailable"


class ToolAuthorizationError(AgentError):
    error_code = "tool_not_authorized"


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


class BudgetExceededError(AgentError):
    error_code = "budget_exhausted"


class PublicationError(AgentError):
    """GitHub 发布失败的稳定边界；消息不得包含令牌、响应正文或用户内容。"""

    error_code = "publication_failed"


class PublicationAuthenticationError(PublicationError):
    error_code = "publication_auth_error"


class PublicationConflictError(PublicationError):
    error_code = "publication_conflict"
