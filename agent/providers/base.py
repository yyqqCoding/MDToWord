from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    response_schema: type[BaseModel]
    tools: tuple[str, ...]
    timeout_seconds: float | None


@dataclass(frozen=True)
class StructuredModelResponse(Generic[StructuredOutput]):
    output: StructuredOutput
    provider: str
    model: str
    provider_request_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    model_calls: int = 1
    retry_count: int = 0
    tool_calls: tuple[str, ...] = ()


class ModelProvider(Protocol):
    async def generate_structured(
        self,
        messages: tuple[ModelMessage, ...],
        response_schema: type[StructuredOutput],
        *,
        tools: tuple[str, ...],
        timeout_seconds: float | None,
    ) -> StructuredModelResponse[StructuredOutput]: ...
