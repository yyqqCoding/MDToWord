import asyncio
import json
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
