import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from agent.domain.errors import (
    InvalidModelResponseError,
    ModelAuthError,
    ModelContextTooLargeError,
    ModelProviderError,
    ModelRateLimitError,
    ModelSafetyRefusalError,
    ModelTimeoutError,
)
from agent.providers.base import (
    ModelMessage,
    StructuredModelResponse,
    StructuredOutput,
)


_Sleep = Callable[[float], Awaitable[None]]


class OpenAICompatibleProvider:
    """基于 Chat Completions 协议的 Provider，不依赖具体厂商 SDK。"""

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        max_format_retries: int = 1,
        max_transport_retries: int = 2,
        input_cost_per_million: Decimal = Decimal("0"),
        output_cost_per_million: Decimal = Decimal("0"),
        sleep: _Sleep = asyncio.sleep,
    ) -> None:
        if not api_key.strip() or not model.strip() or not base_url.strip():
            raise ValueError("api_key, model and base_url are required")
        if max_format_retries not in {0, 1}:
            raise ValueError("max_format_retries must be 0 or 1")
        if max_transport_retries < 0:
            raise ValueError("max_transport_retries must be non-negative")
        self._api_key = api_key
        self.model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None
        self._max_format_retries = max_format_retries
        self._max_transport_retries = max_transport_retries
        self._input_cost_per_million = input_cost_per_million
        self._output_cost_per_million = output_cost_per_million
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_structured(
        self,
        messages: tuple[ModelMessage, ...],
        response_schema: type[StructuredOutput],
        *,
        tools: tuple[str, ...],
        timeout_seconds: float | None,
    ) -> StructuredModelResponse[StructuredOutput]:
        # 当前工具端口只有名称，没有参数 Schema；B3 Gate 必须始终传空集合。
        if tools:
            raise InvalidModelResponseError(
                "openai-compatible structured provider does not accept tool names"
            )

        request_messages = [
            {"role": message.role, "content": message.content} for message in messages
        ]
        total_input = 0
        total_output = 0
        total_cached = 0
        total_reasoning = 0
        total_tokens = 0
        total_cost = Decimal("0")
        provider_request_id = "unknown"

        for format_attempt in range(self._max_format_retries + 1):
            payload = await self._post(
                request_messages,
                response_schema,
                timeout_seconds=timeout_seconds,
            )
            provider_request_id = payload["request_id"]
            usage = payload["usage"]
            total_input += usage["input_tokens"]
            total_output += usage["output_tokens"]
            total_cached += usage["cached_input_tokens"]
            total_reasoning += usage["reasoning_tokens"]
            total_tokens += usage["total_tokens"]
            total_cost += usage["estimated_cost"]

            try:
                output = response_schema.model_validate_json(payload["content"])
            except (ValidationError, ValueError, TypeError):
                if format_attempt >= self._max_format_retries:
                    raise InvalidModelResponseError(
                        "model returned invalid structured output"
                    ) from None
                # 不回传无效原文，避免把潜在敏感内容扩大到下一轮上下文。
                request_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一条响应不符合指定 JSON Schema。请仅重新输出符合 Schema "
                            "的 JSON，不要添加解释、Markdown 或工具调用。"
                        ),
                    }
                )
                continue

            return StructuredModelResponse(
                output=output,
                provider=self.provider,
                model=payload["model"] or self.model,
                provider_request_id=provider_request_id,
                input_tokens=total_input,
                output_tokens=total_output,
                cached_input_tokens=total_cached,
                reasoning_tokens=total_reasoning,
                total_tokens=total_tokens,
                estimated_cost=total_cost,
                model_calls=format_attempt + 1,
                retry_count=format_attempt,
                tool_calls=payload["tool_calls"],
            )

        raise AssertionError("format retry loop must return or raise")

    async def _post(
        self,
        messages: list[dict[str, str]],
        response_schema: type[StructuredOutput],
        *,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        request_body = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(response_schema),
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            },
        }
        for attempt in range(self._max_transport_retries + 1):
            try:
                response = await self._client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                error: ModelProviderError = ModelTimeoutError(
                    "model request timed out"
                )
            except httpx.HTTPError as exc:
                error = ModelProviderError(
                    f"model transport failed: {type(exc).__name__}"
                )
            else:
                if response.status_code < 400:
                    return _parse_response(response, self)
                error = _http_error(response)

            if not isinstance(error, (ModelRateLimitError, ModelProviderError)):
                raise error
            retryable = type(error) in {
                ModelRateLimitError,
                ModelProviderError,
                ModelTimeoutError,
            }
            if not retryable or attempt >= self._max_transport_retries:
                raise error
            await self._sleep(0.25 * (2**attempt))

        raise AssertionError("transport retry loop must return or raise")


def _parse_response(
    response: httpx.Response,
    provider: OpenAICompatibleProvider,
) -> dict[str, Any]:
    try:
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise InvalidModelResponseError(
            "model returned an unexpected response shape"
        ) from None

    if message.get("refusal"):
        raise ModelSafetyRefusalError("model refused the structured request")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidModelResponseError("model response has no structured content")

    tool_calls = tuple(
        str(item.get("function", {}).get("name", "unknown"))
        for item in message.get("tool_calls") or ()
        if isinstance(item, dict)
    )
    usage = _normalized_usage(body.get("usage"), provider)
    request_id = response.headers.get("x-request-id") or body.get("id") or "unknown"
    return {
        "content": content,
        "model": str(body.get("model") or provider.model),
        "request_id": str(request_id),
        "tool_calls": tool_calls,
        "usage": usage,
    }


def _normalized_usage(
    raw: object,
    provider: OpenAICompatibleProvider,
) -> dict[str, int | Decimal]:
    usage = raw if isinstance(raw, dict) else {}
    prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
    completion_tokens = _nonnegative_int(usage.get("completion_tokens"))
    cached_tokens = _detail_tokens(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _detail_tokens(
        usage,
        "completion_tokens_details",
        "reasoning_tokens",
    )
    total_tokens = _nonnegative_int(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    # 数据库保存 Provider 的总输入/输出；Langfuse 适配器再转换为互斥 bucket。
    provider_cost = usage.get("cost")
    if isinstance(provider_cost, (int, float, str)):
        try:
            estimated_cost = max(Decimal(str(provider_cost)), Decimal("0"))
        except Exception:
            estimated_cost = Decimal("0")
    else:
        estimated_cost = (
            Decimal(prompt_tokens) * provider._input_cost_per_million
            + Decimal(completion_tokens) * provider._output_cost_per_million
        ) / Decimal(1_000_000)
    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "cached_input_tokens": min(cached_tokens, prompt_tokens),
        "reasoning_tokens": min(reasoning_tokens, completion_tokens),
        "total_tokens": total_tokens,
        "estimated_cost": estimated_cost,
    }


def _http_error(response: httpx.Response) -> ModelProviderError:
    status = response.status_code
    code = ""
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = str(body["error"].get("code") or "").lower()
    except ValueError:
        pass
    if status in {401, 403}:
        return ModelAuthError(f"model request rejected with status {status}")
    if status == 429:
        return ModelRateLimitError("model request was rate limited")
    if status == 408:
        return ModelTimeoutError("model request timed out")
    if status == 413 or code in {"context_length_exceeded", "context_too_large"}:
        return ModelContextTooLargeError("model context is too large")
    if code in {"content_filter", "safety_refusal"}:
        return ModelSafetyRefusalError("model request was refused for safety")
    if status >= 500:
        return ModelProviderError(f"model service failed with status {status}")
    return InvalidModelResponseError(
        f"model request was rejected with status {status}"
    )


def _detail_tokens(usage: dict[str, object], group: str, name: str) -> int:
    details = usage.get(group)
    if not isinstance(details, dict):
        return 0
    return _nonnegative_int(details.get(name))


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _schema_name(response_schema: type[StructuredOutput]) -> str:
    return response_schema.__name__.lower()[:64]
