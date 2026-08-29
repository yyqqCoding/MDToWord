import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
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
from agent.domain.failures import (
    FailureEvent,
    FailureHandling,
    FailureRecorder,
    LocatedFailure,
    RetryContext,
    RetryDecision,
    RetryPolicy,
    failure_cause_from_exception,
    retry_delay,
)
from agent.providers.base import (
    ModelMessage,
    StructuredModelResponse,
    StructuredOutput,
)


_Sleep = Callable[[float], Awaitable[None]]

_LOGGER = logging.getLogger(__name__)


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
        retry_policy: RetryPolicy | None = None,
        failure_recorder: FailureRecorder | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip() or not base_url.strip():
            raise ValueError("api_key, model and base_url are required")
        if max_format_retries not in {0, 1}:
            raise ValueError("max_format_retries must be 0 or 1")
        if max_transport_retries not in {0, 1, 2}:
            raise ValueError("max_transport_retries must be between 0 and 2")
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
        self._retry_policy = retry_policy or RetryPolicy()
        self._failure_recorder = failure_recorder or FailureRecorder()

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
            except (ValidationError, ValueError, TypeError) as exc:
                schema_errors = _schema_error_paths(exc)
                format_error = InvalidModelResponseError(
                    "model returned invalid structured output",
                    schema_errors=schema_errors,
                    attempt=format_attempt + 1,
                    max_attempts=self._max_format_retries + 1,
                    operation="structured_output_validation",
                )
                # 这是不合规字段唯一的留痕点：下面用 from None 切断异常链，
                # controller 只持久化异常类名，cli 只打印 error_code。
                # 两次尝试都记，便于判断修正提示是否起了作用。
                # detail 带校验器文案，只进本机日志；上 Trace 和展示站的是 errors。
                _LOGGER.warning(
                    "structured output rejected: schema=%s attempt=%s errors=%s detail=%s",
                    _schema_name(response_schema),
                    format_attempt,
                    schema_errors,
                    _validation_error_hint(exc),
                )
                if format_attempt >= self._max_format_retries:
                    self._record_failure(
                        format_error,
                        handling=FailureHandling.STOP,
                    )
                    raise format_error from None
                self._record_failure(
                    format_error,
                    handling=FailureHandling.FORMAT_REVISE,
                )
                # 不回传无效原文，避免把潜在敏感内容扩大到下一轮上下文。
                request_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一条响应不符合指定 JSON Schema。请仅重新输出符合 Schema "
                            "的 JSON，不要添加解释、Markdown 或工具调用。"
                            f"脱敏校验摘要：{_validation_error_hint(exc)}"
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
                    "schema": _strict_response_schema(response_schema),
                },
            },
        }
        for attempt in range(self._max_transport_retries + 1):
            response: httpx.Response | None = None
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
                    "model request timed out",
                    safe_details={"error_type": type(exc).__name__[:120]},
                )
            except httpx.HTTPError as exc:
                error = ModelProviderError(
                    f"model transport failed: {type(exc).__name__}",
                    safe_details={"error_type": type(exc).__name__[:120]},
                )
            else:
                if response.status_code < 400:
                    return _parse_response(response, self)
                error = _http_error(response)

            attempt_number = attempt + 1
            max_attempts = self._max_transport_retries + 1
            error.attempt = attempt_number
            error.max_attempts = max_attempts
            error.operation = "chat_completions"
            delay = _transport_retry_delay(attempt_number, response)
            cause = failure_cause_from_exception(error)
            decision = self._retry_policy.decide(
                cause,
                RetryContext(
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    operation_id=_schema_name(response_schema),
                    idempotent=True,
                ),
                delay_seconds=delay,
            )
            if decision is RetryDecision.STOP:
                self._record_failure(error, handling=FailureHandling.STOP)
                raise error
            self._record_failure(
                error,
                handling=FailureHandling.TRANSPORT_RETRY,
                delay_seconds=delay,
            )
            await self._sleep(delay)

        raise AssertionError("transport retry loop must return or raise")

    def _record_failure(
        self,
        error: Exception,
        *,
        handling: FailureHandling,
        delay_seconds: float | None = None,
    ) -> None:
        cause = failure_cause_from_exception(error)
        self._failure_recorder.record(
            FailureEvent(
                failure=LocatedFailure(
                    cause=cause,
                    phase="provider",
                    node=cause.operation,
                ),
                attempt=getattr(error, "attempt", 1),
                max_attempts=getattr(error, "max_attempts", 1),
                handling=handling,
                delay_seconds=delay_seconds,
            )
        )


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
    details = {"http_status": status}
    code = ""
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = str(body["error"].get("code") or "").lower()
    except ValueError:
        pass
    if status in {401, 403}:
        return ModelAuthError(
            f"model request rejected with status {status}",
            safe_details=details,
        )
    if status == 429:
        return ModelRateLimitError(
            "model request was rate limited",
            safe_details=details,
        )
    if status == 408:
        return ModelTimeoutError("model request timed out", safe_details=details)
    if status == 413 or code in {"context_length_exceeded", "context_too_large"}:
        return ModelContextTooLargeError(
            "model context is too large",
            safe_details=details,
        )
    if code in {"content_filter", "safety_refusal"}:
        return ModelSafetyRefusalError(
            "model request was refused for safety",
            safe_details=details,
        )
    if status >= 500:
        return ModelProviderError(
            f"model service failed with status {status}",
            safe_details=details,
        )
    return InvalidModelResponseError(
        f"model request was rejected with status {status}",
        safe_details=details,
    )


def _transport_retry_delay(
    attempt: int,
    response: httpx.Response | None,
) -> float:
    """使用有界退避；429若给出秒数形式 Retry-After，则尊重更长等待。"""

    requested: float | None = None
    if response is None or response.status_code != 429:
        return retry_delay(attempt)
    raw = response.headers.get("Retry-After")
    if raw is None:
        return retry_delay(attempt)
    try:
        requested = float(raw)
    except ValueError:
        requested = None
    return retry_delay(attempt, requested)


def _detail_tokens(usage: dict[str, object], group: str, name: str) -> int:
    details = usage.get(group)
    if not isinstance(details, dict):
        return 0
    return _nonnegative_int(details.get(name))


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _schema_name(response_schema: type[StructuredOutput]) -> str:
    return response_schema.__name__.lower()[:64]


def _strict_response_schema(
    response_schema: type[StructuredOutput],
) -> dict[str, Any]:
    """补齐严格 Structured Outputs 要求，同时保留 Pydantic 的本地验证。"""

    schema = deepcopy(response_schema.model_json_schema())

    def normalize(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                # 严格模式要求对象全部属性列入 required；可选值通过 null 表达。
                node["required"] = list(properties)
                node["additionalProperties"] = False
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    normalize(schema)
    return schema


def _validation_error_hint(error: Exception) -> str:
    """只返回字段路径与规则，不包含模型原始输出或 Pydantic input/context。

    两个消费方：回传给模型的修正提示，以及只有维护者能看的本机 WARNING 日志。
    含校验器文案，因此不上 Langfuse 与公开展示站 —— 那边用 `_schema_error_paths`。
    """

    if not isinstance(error, ValidationError):
        return json.dumps(
            [{"loc": [], "type": type(error).__name__}],
            ensure_ascii=False,
        )
    safe = [
        {
            "loc": [str(part) for part in item["loc"]],
            "type": item["type"],
            "message": item["msg"],
        }
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:8]
    ]
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def _schema_error_paths(error: Exception) -> str:
    """给日志与 Trace 用的不合规字段摘要，形如 `edits.0.content:string_too_long`。

    比 `_validation_error_hint` 更严：连校验器文案都不带，只留字段路径与 Pydantic
    规则名。两者受众不同 —— 那份回传给模型、并写入只有维护者能看的本机日志；这份
    会进 Langfuse 和公开展示站，必须在任何校验器改动之后都不可能夹带模型原文。

    `extra="forbid"` 下 loc 会带上模型自己编造的字段名，所以每段单独截断。
    """

    if not isinstance(error, ValidationError):
        return type(error).__name__
    parts = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:8]:
        location = ".".join(str(part)[:40] for part in item["loc"]) or "<root>"
        parts.append(f"{location}:{item['type']}")
    return ",".join(parts)
