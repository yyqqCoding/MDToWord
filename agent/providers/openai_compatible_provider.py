"""OpenAI 兼容 Chat Completions Provider。

一个实现覆盖 DeepSeek / Qwen / OpenRouter / Anthropic 兼容端点等,
切换只需改 MODEL_BASE_URL / MODEL_NAME / MODEL_API_KEY(零代码改动)。

结构化策略(阶段 03 spec §3):
- prompt_json(默认):系统提示附加 Schema → 剥围栏/前后杂文 → json.loads
  → Pydantic 严格校验 → 失败附错误摘要仅重试一次;
- native_schema:请求体带 response_format json_schema(服务原生支持时)。

安全:API Key 只进请求 Header,不进日志与异常消息;
默认不保存/记录完整用户 Markdown(日志只记载荷字节数)。
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable

import httpx
from pydantic import BaseModel, ValidationError

from agent.logging_utils import get_logger
from agent.providers.base import ModelError, ModelErrorCode, ModelUsage, T

HTTP_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 8.0)
ERROR_BODY_LIMIT = 300
CONTEXT_LIMIT_MARKERS = ("context_length", "maximum context", "context window",
                         "too many tokens", "max_tokens")
JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
        structured_strategy: str = "prompt_json",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if structured_strategy not in ("prompt_json", "native_schema"):
            raise ValueError(f"未知结构化策略: {structured_strategy}")
        self.model = model
        self._strategy = structured_strategy
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._sleep = sleep
        self._log = get_logger("agent.provider", provider="openai_compatible",
                               model=model)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    # -- 对外接口 --------------------------------------------------------------

    def generate_structured(
        self, *, system_prompt: str, user_payload: dict, response_model: type[T],
    ) -> tuple[T, ModelUsage]:
        schema = response_model.model_json_schema()
        messages = [
            {"role": "system",
             "content": f"{system_prompt}\n\n只输出一个符合以下 JSON Schema 的 JSON 对象,"
                        f"不要输出任何其他文本:\n{json.dumps(schema, ensure_ascii=False)}"},
            {"role": "user",
             "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        total = ModelUsage()

        content, usage = self._chat(messages, schema)
        total = total + usage
        try:
            return self._parse(content, response_model), total
        except (json.JSONDecodeError, ValidationError) as first_error:
            # 失败附 Schema 错误摘要,仅重试一次(spec §3)
            summary = str(first_error)[:500]
            self._log.warning("结构化输出解析失败,重试一次",
                              context={"parse_error": summary})
            retry_messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user",
                 "content": f"上一次输出不符合 Schema,错误摘要:\n{summary}\n"
                            "请重新只输出一个符合 Schema 的 JSON 对象,不要任何其他文本。"},
            ]
            content, usage = self._chat(retry_messages, schema)
            total = total + usage
            try:
                return self._parse(content, response_model), total
            except (json.JSONDecodeError, ValidationError) as second_error:
                raise ModelError(
                    f"模型输出两次均不符合 {response_model.__name__} Schema: "
                    f"{str(second_error)[:300]}",
                    code=ModelErrorCode.INVALID_RESPONSE,
                ) from second_error

    def close(self) -> None:
        self._client.close()

    # -- 内部 -------------------------------------------------------------------

    def _chat(self, messages: list[dict], schema: dict) -> tuple[str, ModelUsage]:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        if self._strategy == "native_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "strict": True,
                                "schema": schema},
            }

        last_error: ModelError | None = None
        for attempt in range(HTTP_ATTEMPTS):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                raise ModelError(f"模型请求超时: {type(exc).__name__}",
                                 code=ModelErrorCode.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                last_error = ModelError(f"模型请求网络错误: {type(exc).__name__}",
                                        code=ModelErrorCode.PROVIDER_UNAVAILABLE)
            else:
                if response.status_code < 400:
                    return self._extract_content(response)
                last_error = self._map_http_error(response)
                if last_error.code in (ModelErrorCode.AUTH_ERROR,
                                       ModelErrorCode.CONTEXT_TOO_LARGE):
                    raise last_error  # 不重试
            if attempt < HTTP_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
        assert last_error is not None
        raise last_error

    @staticmethod
    def _map_http_error(response: httpx.Response) -> ModelError:
        body = response.text[:ERROR_BODY_LIMIT]
        status = response.status_code
        if status in (401, 403):
            code = ModelErrorCode.AUTH_ERROR
        elif status == 429:
            code = ModelErrorCode.RATE_LIMIT
        elif status == 400 and any(m in body.lower() for m in CONTEXT_LIMIT_MARKERS):
            code = ModelErrorCode.CONTEXT_TOO_LARGE
        else:
            code = ModelErrorCode.PROVIDER_UNAVAILABLE
        return ModelError(f"模型服务返回 {status}: {body}", code=code)

    def _extract_content(self, response: httpx.Response) -> tuple[str, ModelUsage]:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"模型响应结构异常: {type(exc).__name__}",
                             code=ModelErrorCode.INVALID_RESPONSE) from exc
        if content is None or (isinstance(content, str) and not content.strip()):
            finish = choice.get("finish_reason", "")
            raise ModelError(f"模型返回空内容(finish_reason={finish})",
                             code=ModelErrorCode.SAFETY_REFUSAL)
        usage = data.get("usage") or {}
        return content, ModelUsage(input_tokens=usage.get("prompt_tokens"),
                                   output_tokens=usage.get("completion_tokens"))

    @staticmethod
    def _parse(content: str, response_model: type[T]) -> T:
        text = content.strip()
        fence = JSON_FENCE_PATTERN.search(text)
        if fence:
            text = fence.group(1)
        else:
            # 容忍 JSON 前后的多余文本:取第一个 { 到最后一个 }
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                text = text[start:end + 1]
        return response_model.model_validate(json.loads(text))
