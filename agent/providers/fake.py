from collections.abc import Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel

from agent.providers.base import (
    ModelMessage,
    ModelRequest,
    StructuredModelResponse,
    StructuredOutput,
)


class FakeModelProvider:
    """按队列返回结构化结果，并保留请求供契约测试断言。"""

    def __init__(
        self,
        outputs: Sequence[BaseModel | Mapping[str, object]],
        *,
        provider: str = "fake",
        model: str = "fake-gate-v1",
    ) -> None:
        self._outputs = list(outputs)
        self.provider = provider
        self.model = model
        self.requests: list[ModelRequest] = []

    async def generate_structured(
        self,
        messages: tuple[ModelMessage, ...],
        response_schema: type[StructuredOutput],
        *,
        tools: tuple[str, ...],
        timeout_seconds: float | None,
    ) -> StructuredModelResponse[StructuredOutput]:
        self.requests.append(
            ModelRequest(
                messages=messages,
                response_schema=response_schema,
                tools=tools,
                timeout_seconds=timeout_seconds,
            )
        )
        if not self._outputs:
            raise RuntimeError("fake provider has no queued response")

        raw = self._outputs.pop(0)
        payload = raw.model_dump() if isinstance(raw, BaseModel) else raw
        output = response_schema.model_validate(payload)
        return StructuredModelResponse(
            output=output,
            provider=self.provider,
            model=self.model,
            provider_request_id=f"fake-{uuid4()}",
        )
