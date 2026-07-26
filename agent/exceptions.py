"""Agent 错误体系。error_code 与 agent_runs.error_code / 阶段 10 排查表对齐。"""

from __future__ import annotations


class AgentError(Exception):
    """所有 Agent 异常的基类,携带机器可读的 error_code。"""

    error_code: str = "agent_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code


class ConfigError(AgentError):
    error_code = "config_error"


class InvalidTransitionError(AgentError):
    error_code = "invalid_transition"


class FeedbackNotFoundError(AgentError):
    error_code = "feedback_not_found"


class ClaimUnavailableError(AgentError):
    """领取失败:已被占用 / 状态不可领取 / 超过重试上限(由 RPC 判定)。"""

    error_code = "claim_unavailable"


class SupabaseError(AgentError):
    """Supabase HTTP 错误。按状态码映射为不同 error_code(验收要求)。

    401/403 -> supabase_unauthorized(不重试)
    429     -> supabase_rate_limited(有限重试后抛出)
    5xx     -> supabase_server_error(有限重试后抛出)
    其他    -> supabase_error
    """

    error_code = "supabase_error"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message, error_code=self._map_code(status_code))

    @staticmethod
    def _map_code(status_code: int | None) -> str:
        if status_code in (401, 403):
            return "supabase_unauthorized"
        if status_code == 429:
            return "supabase_rate_limited"
        if status_code is not None and status_code >= 500:
            return "supabase_server_error"
        return "supabase_error"
