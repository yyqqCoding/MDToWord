import asyncio
import json
import logging
from decimal import Decimal

import httpx
import pytest

from agent.domain.errors import (
    InvalidModelResponseError,
    ModelAuthError,
    ModelRateLimitError,
)
from agent.domain.gate import GateClassification
from agent.domain.reproduction import (
    ReproductionPlan,
    TestGenerationResult as GeneratedTestResult,
)
from agent.domain.repair import FixGenerationResult
from agent.providers.base import ModelMessage
from agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _strict_response_schema,
)


def _classification_payload() -> dict[str, object]:
    return {
        "intent": "bug_report",
        "category": "table_parsing",
        "relevance": 0.98,
        "sufficient_information": True,
        "injection_suspected": False,
        "requires_extension_change": False,
        "reason": "后端导出表格失败",
    }


def _response(content: object, *, usage: dict[str, object] | None = None):
    return {
        "id": "request-123",
        "model": "compatible-model",
        "choices": [{"message": {"content": content}}],
        "usage": usage or {},
    }


def _run(provider: OpenAICompatibleProvider):
    return asyncio.run(
        provider.generate_structured(
            (ModelMessage(role="user", content="bounded input"),),
            GateClassification,
            tools=(),
            timeout_seconds=5,
        )
    )


def test_provider_sends_strict_json_schema_and_normalizes_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer model-secret"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        return httpx.Response(
            200,
            headers={"x-request-id": "header-request-id"},
            json=_response(
                json.dumps(_classification_payload(), ensure_ascii=False),
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                    "prompt_tokens_details": {"cached_tokens": 30},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                },
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                api_key="model-secret",
                model="configured-model",
                base_url="https://models.example/v1",
                client=client,
                input_cost_per_million=Decimal("1"),
                output_cost_per_million=Decimal("2"),
            )
            return await provider.generate_structured(
                (ModelMessage(role="user", content="bounded input"),),
                GateClassification,
                tools=(),
                timeout_seconds=5,
            )

    result = asyncio.run(scenario())

    assert result.output.category.value == "table_parsing"
    assert result.provider_request_id == "header-request-id"
    assert result.input_tokens == 100
    assert result.output_tokens == 40
    assert result.cached_input_tokens == 30
    assert result.reasoning_tokens == 10
    assert result.total_tokens == 140
    assert result.estimated_cost == Decimal("0.00018")


def test_stage_d_and_e_schemas_require_every_nested_property_in_strict_mode():
    def assert_strict_objects(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert node.get("additionalProperties") is False
                assert set(node.get("required", ())) == set(properties)
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict_objects(value)

    for response_schema in (
        ReproductionPlan,
        GeneratedTestResult,
        FixGenerationResult,
    ):
        schema = _strict_response_schema(response_schema)
        assert_strict_objects(schema)

    parameters = _strict_response_schema(ReproductionPlan)["$defs"]["OracleParameters"]
    assert set(parameters["properties"]) == {"validator", "minimum", "text", "style"}


def test_invalid_structure_gets_one_bounded_format_retry():
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_response("not-json", usage={"prompt_tokens": 3, "completion_tokens": 2}),
            )
        return httpx.Response(
            200,
            json=_response(
                json.dumps(_classification_payload()),
                usage={"prompt_tokens": 4, "completion_tokens": 3},
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _run_async(
                OpenAICompatibleProvider(
                    api_key="secret",
                    model="model",
                    base_url="https://models.example/v1",
                    client=client,
                )
            )

    result = asyncio.run(scenario())

    assert result.model_calls == 2
    assert result.retry_count == 1
    assert result.input_tokens == 7
    assert result.output_tokens == 5
    assert len(requests[1]["messages"]) == 2
    assert "not-json" not in json.dumps(requests[1])
    correction = requests[1]["messages"][1]["content"]
    assert "脱敏校验摘要" in correction
    assert "json_invalid" in correction


def test_invalid_structure_is_rejected_after_single_retry():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response("still-not-json"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(InvalidModelResponseError):
                await _run_async(
                    OpenAICompatibleProvider(
                        api_key="secret",
                        model="model",
                        base_url="https://models.example/v1",
                        client=client,
                    )
                )

    asyncio.run(scenario())


def test_transient_provider_failure_uses_bounded_backoff_before_success():
    request_count = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count < 3:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(
            200,
            json=_response(json.dumps(_classification_payload())),
        )

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _run_async(
                OpenAICompatibleProvider(
                    api_key="secret",
                    model="model",
                    base_url="https://models.example/v1",
                    client=client,
                    sleep=record_sleep,
                )
            )

    result = asyncio.run(scenario())

    assert result.output.intent.value == "bug_report"
    assert request_count == 3
    assert delays == [1.0, 4.0]


def test_rate_limit_respects_bounded_retry_after_seconds():
    request_count = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        del request
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "7"},
                json={"error": {"message": "slow down"}},
            )
        return httpx.Response(
            200,
            json=_response(json.dumps(_classification_payload())),
        )

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _run_async(
                OpenAICompatibleProvider(
                    api_key="secret",
                    model="model",
                    base_url="https://models.example/v1",
                    client=client,
                    max_transport_retries=1,
                    sleep=record_sleep,
                )
            )

    result = asyncio.run(scenario())

    assert result.output.intent.value == "bug_report"
    assert request_count == 2
    assert delays == [7.0]


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [(401, ModelAuthError), (429, ModelRateLimitError)],
)
def test_provider_errors_are_stable_and_never_echo_response(status, expected_error):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "leaked-secret"}})

    async def no_sleep(seconds: float) -> None:
        del seconds

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                api_key="model-secret",
                model="model",
                base_url="https://models.example/v1",
                client=client,
                max_transport_retries=0,
                sleep=no_sleep,
            )
            with pytest.raises(expected_error) as exc_info:
                await _run_async(provider)
            assert "model-secret" not in str(exc_info.value)
            assert "leaked-secret" not in str(exc_info.value)

    asyncio.run(scenario())


async def _run_async(provider: OpenAICompatibleProvider):
    return await provider.generate_structured(
        (ModelMessage(role="user", content="bounded input"),),
        GateClassification,
        tools=(),
        timeout_seconds=5,
    )


def _reject_with(payload: dict[str, object]):
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_response(json.dumps(payload)))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _run_async(
                OpenAICompatibleProvider(
                    api_key="secret",
                    model="model",
                    base_url="https://models.example/v1",
                    client=client,
                )
            )

    with pytest.raises(InvalidModelResponseError) as exc_info:
        asyncio.run(scenario())
    return exc_info.value


def test_invalid_structure_reports_which_fields_failed(caplog):
    """invalid_response 必须能指认不合规字段。

    异常链被 `from None` 切断、Controller 只持久化异常类名、CLI 只打印 error_code，
    provider 这一层是唯一的留痕点。
    """

    # reason 用空白字符：能过 min_length，再被 field_validator 拒绝，产生 value_error
    payload = _classification_payload() | {"relevance": 4.2, "reason": "   "}

    with caplog.at_level(logging.WARNING, logger="agent.providers.openai_compatible"):
        error = _reject_with(payload)

    assert "relevance:less_than_equal" in error.schema_errors
    assert "reason:value_error" in error.schema_errors
    # 校验器文案只回传给模型（_validation_error_hint），不进 Trace 与展示站
    assert "must not be blank" not in error.schema_errors

    messages = [record.getMessage() for record in caplog.records]
    # 两次尝试都留痕，便于判断修正提示有没有起作用
    assert len(messages) == 2
    assert all("relevance:less_than_equal" in item for item in messages)
    assert all("schema=gateclassification" in item for item in messages)
    # 本机日志是维护者专属，带上校验器文案才能区分同一 validator 的不同分支
    assert all("must not be blank" in item for item in messages)


def test_model_invented_field_names_are_bounded_in_schema_errors():
    """extra="forbid" 下 loc 会带上模型自己编造的字段名，必须逐段截断。"""

    invented = "explanation_" + "x" * 200
    error = _reject_with(_classification_payload() | {invented: "很长的解释"})

    assert error.schema_errors == f"{invented[:40]}:extra_forbidden"
    assert "很长的解释" not in error.schema_errors


def test_malformed_json_reports_a_root_level_schema_error():
    # 整段 JSON 解析失败时 Pydantic 给的 loc 为空，摘要必须仍然可读
    error = _reject_with_content("still-not-json")

    assert error.schema_errors == "<root>:json_invalid"


def _reject_with_content(content: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_response(content))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _run_async(
                OpenAICompatibleProvider(
                    api_key="secret",
                    model="model",
                    base_url="https://models.example/v1",
                    client=client,
                )
            )

    with pytest.raises(InvalidModelResponseError) as exc_info:
        asyncio.run(scenario())
    return exc_info.value
