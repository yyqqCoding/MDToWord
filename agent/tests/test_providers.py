"""Provider 契约测试(阶段 03 验收清单)。全部走 httpx.MockTransport,不花真钱。"""

import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel

from agent.config import AgentConfig
from agent.exceptions import ConfigError
from agent.providers import factory
from agent.providers.base import ModelError, ModelErrorCode, ModelUsage
from agent.providers.openai_compatible_provider import (
    HTTP_ATTEMPTS,
    OpenAICompatibleProvider,
)
from agent.tests.fakes import FakeModelProvider

API_KEY = "sk-test-secret-key-do-not-log"


class SampleResult(BaseModel):
    category: Literal["table", "formula", "heading", "other"]
    confidence: float


def completion(content: str, prompt_tokens: int = 120, completion_tokens: int = 30):
    return {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens,
                  "completion_tokens": completion_tokens},
    }


def make_provider(handler, **kwargs):
    calls = {"count": 0, "requests": []}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        calls["requests"].append(request)
        return handler(request)

    provider = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key=API_KEY,
        model="test-model",
        transport=httpx.MockTransport(counting_handler),
        sleep=lambda seconds: None,
        **kwargs,
    )
    return provider, calls


def generate(provider):
    return provider.generate_structured(
        system_prompt="你是分类器",
        user_payload={"markdown": "# 表格"},
        response_model=SampleResult,
    )


VALID_JSON = '{"category": "table", "confidence": 0.9}'


# -- 正常与容错解析 -------------------------------------------------------------

def test_plain_json_response():
    provider, calls = make_provider(
        lambda request: httpx.Response(200, json=completion(VALID_JSON)))
    result, usage = generate(provider)
    assert result.category == "table"
    assert usage == ModelUsage(input_tokens=120, output_tokens=30)
    assert calls["count"] == 1


def test_fenced_json_response():
    provider, _ = make_provider(lambda request: httpx.Response(
        200, json=completion(f"```json\n{VALID_JSON}\n```")))
    result, _ = generate(provider)
    assert result.category == "table"


def test_json_with_surrounding_prose():
    provider, _ = make_provider(lambda request: httpx.Response(
        200, json=completion(f"好的,以下是结果:\n{VALID_JSON}\n希望有帮助!")))
    result, _ = generate(provider)
    assert result.confidence == 0.9


# -- 非法输出:重试一次 -----------------------------------------------------------

def test_invalid_json_retries_once_then_invalid_response():
    provider, calls = make_provider(lambda request: httpx.Response(
        200, json=completion("这不是 JSON")))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.INVALID_RESPONSE
    assert exc_info.value.error_code == "model_invalid_response"
    assert calls["count"] == 2  # 仅重试一次


def test_invalid_then_valid_json_recovers_and_sums_usage():
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state.pop("first", False):
            return httpx.Response(200, json=completion("oops", 100, 10))
        body = json.loads(request.content)
        # 重试消息包含错误摘要与上一次输出
        assert any("不符合 Schema" in m["content"] for m in body["messages"])
        return httpx.Response(200, json=completion(VALID_JSON, 200, 20))

    provider, calls = make_provider(handler)
    result, usage = generate(provider)
    assert result.category == "table"
    assert calls["count"] == 2
    assert usage == ModelUsage(input_tokens=300, output_tokens=30)


def test_missing_field_fails_validation():
    provider, calls = make_provider(lambda request: httpx.Response(
        200, json=completion('{"category": "table"}')))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.INVALID_RESPONSE
    assert calls["count"] == 2


def test_illegal_enum_fails_validation():
    provider, _ = make_provider(lambda request: httpx.Response(
        200, json=completion('{"category": "video", "confidence": 0.9}')))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.INVALID_RESPONSE


# -- HTTP 错误标准化 --------------------------------------------------------------

def test_timeout_maps_to_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    provider, calls = make_provider(handler)
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.TIMEOUT
    assert calls["count"] == 1


def test_429_maps_to_rate_limit_with_retries():
    provider, calls = make_provider(
        lambda request: httpx.Response(429, json={"error": "rate limited"}))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.RATE_LIMIT
    assert calls["count"] == HTTP_ATTEMPTS


def test_401_maps_to_auth_error_without_retry():
    provider, calls = make_provider(
        lambda request: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.AUTH_ERROR
    assert calls["count"] == 1


def test_500_maps_to_provider_unavailable_with_retries():
    provider, calls = make_provider(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.PROVIDER_UNAVAILABLE
    assert calls["count"] == HTTP_ATTEMPTS


def test_context_length_400_maps_to_context_too_large():
    provider, calls = make_provider(lambda request: httpx.Response(
        400, json={"error": {"message": "This model's maximum context length is exceeded"}}))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.CONTEXT_TOO_LARGE
    assert calls["count"] == 1


def test_empty_content_maps_to_safety_refusal():
    provider, _ = make_provider(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": ""},
                     "finish_reason": "content_filter"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert exc_info.value.code == ModelErrorCode.SAFETY_REFUSAL


# -- 安全:Key 不泄漏 -------------------------------------------------------------

def test_api_key_not_in_error_messages_or_logs(capfd):
    provider, _ = make_provider(
        lambda request: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(ModelError) as exc_info:
        generate(provider)
    assert API_KEY not in str(exc_info.value)
    captured = capfd.readouterr()
    assert API_KEY not in captured.err
    assert API_KEY not in captured.out


def test_api_key_only_in_authorization_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        body = json.loads(request.content)
        assert API_KEY not in json.dumps(body)
        return httpx.Response(200, json=completion(VALID_JSON))

    provider, _ = make_provider(handler)
    generate(provider)


# -- 请求构造 ---------------------------------------------------------------------

def test_prompt_json_embeds_schema_in_system_prompt():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["temperature"] == 0.0
        system = body["messages"][0]["content"]
        assert "JSON Schema" in system and "category" in system
        assert "response_format" not in body
        return httpx.Response(200, json=completion(VALID_JSON))

    provider, _ = make_provider(handler)
    generate(provider)


def test_native_schema_sends_response_format():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"]["properties"]["category"]
        return httpx.Response(200, json=completion(VALID_JSON))

    provider, _ = make_provider(handler, structured_strategy="native_schema")
    generate(provider)


# -- 工厂与 Fake ------------------------------------------------------------------

def test_factory_creates_openai_compatible():
    config = AgentConfig.from_env({
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_NAME": "deepseek-chat",
        "MODEL_API_KEY": "sk-x",
        "MODEL_BASE_URL": "https://api.deepseek.com/v1",
    })
    provider = factory.create(config)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "deepseek-chat"


def test_factory_rejects_unknown_provider():
    config = AgentConfig.from_env({"MODEL_PROVIDER": "carrier-pigeon"})
    with pytest.raises(ConfigError):
        factory.create(config)


def test_factory_requires_model_config():
    config = AgentConfig.from_env({"MODEL_PROVIDER": "openai_compatible"})
    with pytest.raises(ConfigError) as exc_info:
        factory.create(config)
    assert "MODEL_NAME" in exc_info.value.message


def test_fake_provider_returns_fixed_classification():
    fake = FakeModelProvider([{"category": "table", "confidence": 0.95}])
    result, usage = fake.generate_structured(
        system_prompt="s", user_payload={"markdown": "x"},
        response_model=SampleResult)
    assert result.category == "table"
    assert usage.input_tokens == 100
    assert fake.calls[0]["response_model"] == "SampleResult"
