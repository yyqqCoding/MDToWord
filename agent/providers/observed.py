from agent.providers.base import (
    ModelMessage,
    ModelProvider,
    StructuredModelResponse,
    StructuredOutput,
)
from agent.telemetry.base import GenerationTrace, Telemetry
from agent.telemetry.masking import content_summary


class ObservedModelProvider:
    """在 Provider 端口外统一记录 Generation，避免厂商适配器耦合 Langfuse。"""

    def __init__(
        self,
        provider: ModelProvider,
        telemetry: Telemetry,
        *,
        operation: str,
        prompt_version: str,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self._operation = operation
        self._prompt_version = prompt_version
        self.provider = getattr(provider, "provider", "unknown")
        self.model = getattr(provider, "model", "unknown")

    async def generate_structured(
        self,
        messages: tuple[ModelMessage, ...],
        response_schema: type[StructuredOutput],
        *,
        tools: tuple[str, ...],
        timeout_seconds: float | None,
    ) -> StructuredModelResponse[StructuredOutput]:
        trace = GenerationTrace(
            operation=self._operation,
            prompt_version=self._prompt_version,
            provider=self.provider,
            model=self.model,
            input_summary={
                "messages": [
                    {"role": item.role, **content_summary(item.content)}
                    for item in messages
                ],
                "response_schema": response_schema.__name__,
                "tool_count": len(tools),
            },
        )
        with self._telemetry.start_generation(trace) as observation:
            try:
                response = await self._provider.generate_structured(
                    messages,
                    response_schema,
                    tools=tools,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                observation.fail(
                    error_code=getattr(exc, "error_code", "unexpected_error"),
                    error_type=type(exc).__name__,
                )
                raise
            observation.succeed(response)
            return response
