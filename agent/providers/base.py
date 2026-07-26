"""Model Provider 统一接口与标准错误模型(阶段 03 spec §1/§4)。

业务代码只依赖本模块的 Protocol 与 ModelError;
禁止出现按模型名/厂商分支的逻辑(architecture.md 设计原则 #1)。
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, TypeVar

from pydantic import BaseModel

from agent.exceptions import AgentError

T = TypeVar("T", bound=BaseModel)


class ModelUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __add__(self, other: "ModelUsage") -> "ModelUsage":
        def add(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return ModelUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
        )


class ModelErrorCode(str, Enum):
    AUTH_ERROR = "auth_error"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    CONTEXT_TOO_LARGE = "context_too_large"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SAFETY_REFUSAL = "safety_refusal"


class ModelError(AgentError):
    """所有 Provider 的错误统一收敛到这里;差异封装在 Provider 内部。

    error_code 形如 `model_rate_limit`,与 agent_runs.error_code /
    阶段 10 排查表对齐。
    """

    def __init__(self, message: str, *, code: ModelErrorCode) -> None:
        self.code = code
        super().__init__(message, error_code=f"model_{code.value}")


class ModelProvider(Protocol):
    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> tuple[T, ModelUsage]: ...
