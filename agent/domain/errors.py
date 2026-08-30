class AgentError(Exception):
    """Base class for expected Agent failures with stable error codes."""

    error_code = "agent_error"

    def __init__(
        self,
        message: str = "",
        *,
        attempt: int = 1,
        max_attempts: int = 1,
        safe_details: dict[str, str | int | bool | None] | None = None,
        operation: str | None = None,
        phase: str | None = None,
        node: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt = max(1, attempt)
        self.max_attempts = max(self.attempt, max_attempts)
        self.safe_details = dict(safe_details or {})
        self.operation = operation
        self.phase = phase
        self.node = node

    def locate(
        self,
        *,
        operation: str | None = None,
        phase: str | None = None,
        node: str | None = None,
    ) -> "AgentError":
        """只补充受信调用点尚未提供的位置，不覆盖更具体的内层位置。"""

        # operation 由外层业务调用点提供时比厂商协议方法更具体，应允许覆盖；位置只补空值。
        self.operation = operation or self.operation
        self.phase = self.phase or phase
        self.node = self.node or node
        return self


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


class RepositoryUnavailableError(RepositoryError):
    error_code = "repository_unavailable"


class SourceAuthenticationError(RepositoryError):
    """GitHub 源码读取凭据失效或没有仓库读取权限。"""

    error_code = "source_auth_error"


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


class SandboxRequestRejectedError(AgentError):
    error_code = "sandbox_request_rejected"


class SandboxInvalidResponseError(AgentError):
    error_code = "sandbox_invalid_response"


class ToolAuthorizationError(AgentError):
    error_code = "tool_not_authorized"


class InvalidArtifactPathError(AgentError):
    error_code = "invalid_artifact_path"


class InvalidModelResponseError(AgentError):
    error_code = "invalid_response"

    def __init__(
        self,
        message: str,
        *,
        schema_errors: str | None = None,
        **kwargs: object,
    ) -> None:
        safe_details = dict(kwargs.pop("safe_details", {}) or {})
        if schema_errors:
            safe_details["schema_errors"] = schema_errors
        super().__init__(message, safe_details=safe_details, **kwargs)
        # 不合规字段摘要，只含「字段路径:规则名」，不含模型原文，因此可以进日志和
        # Trace。Controller 只持久化异常类名、CLI 只打印 error_code，没有这个字段就
        # 无法判断 invalid_response 到底卡在哪一项。
        # 生成方式见 providers/openai_compatible.py:_schema_error_paths。
        self.schema_errors = schema_errors


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


class IssuePublicationError(PublicationError):
    error_code = "issue_publication_failed"
