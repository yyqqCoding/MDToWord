import asyncio
from decimal import Decimal

import pytest

from agent.domain.errors import InvalidModelResponseError, ModelProviderError
from agent.domain.gate import GateClassification
from agent.providers.base import ModelMessage, StructuredModelResponse
from agent.providers.fake import FakeModelProvider
from agent.providers.observed import ObservedModelProvider
from agent.telemetry.base import (
    GenerationTrace,
    RunTrace,
    ToolTrace,
    exclusive_usage_buckets,
)
from agent.telemetry.langfuse import LangfuseTelemetry
from agent.telemetry.masking import mask_sensitive


def _classification() -> GateClassification:
    return GateClassification(
        intent="bug_report",
        category="table_parsing",
        relevance=0.98,
        sufficient_information=True,
        injection_suspected=False,
        requires_extension_change=False,
        reason="user@example.com said token=secret",
    )


class _RawObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(kwargs)


class _ObservationContext:
    def __init__(self, raw: _RawObservation) -> None:
        self.raw = raw

    def __enter__(self):
        return self.raw

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.starts: list[dict[str, object]] = []
        self.observations: list[_RawObservation] = []
        self.flushed = False

    def start_as_current_observation(self, **kwargs: object):
        self.starts.append(kwargs)
        raw = _RawObservation()
        self.observations.append(raw)
        return _ObservationContext(raw)

    def flush(self) -> None:
        self.flushed = True


def test_masking_removes_contact_secrets_and_long_content():
    masked = mask_sensitive(
        data={
            "contact": "user@example.com",
            "header": "Bearer top-secret",
            "note": "call +86 138 0013 8000 or user@example.com",
            "output": "MODEL_API_KEY=do-not-leak",
            "markdown": "x" * 500,
        }
    )

    assert masked["contact"] == "[REDACTED]"
    assert "top-secret" not in masked["header"]
    assert "user@example.com" not in masked["note"]
    assert "138 0013 8000" not in masked["note"]
    assert "do-not-leak" not in masked["output"]
    assert len(masked["markdown"]) == 300


@pytest.mark.parametrize(
    "identifier",
    [
        # 都含 9 位以上连续数字段，旧手机号正则会从中间吃掉一截
        "7902699be42c8a8e46fbbb4501726517e86b22c56a189f7625a6da49081b2451",
        "sha256:2c624232cdd221771294dfbb310aca000a0df6ac8b66b696d90ef06fdefb64a3",
        "4a44dc15364204a80fe80e9039455cc1608281820fe2b24f1e5233ade6af1dd5",
        "1dc85749-cfe2-53ee-9936-1a791daa5356",
    ],
)
def test_masking_keeps_hashes_and_uuids_intact(identifier: str) -> None:
    """哈希与 UUID 是站点上可核对的证据，被脱敏截断等于伪造证据。

    不是泄露风险，但会让 Trace 里的指纹、镜像 digest 和 job_id 无法对账。
    """

    masked = mask_sensitive(data={"sha256": identifier, "job_id": identifier})

    assert masked["sha256"] == identifier
    assert masked["job_id"] == identifier


@pytest.mark.parametrize(
    "text",
    [
        "13800138000",
        "联系电话：13800138000",
        "手机 138-0013-8000 谢谢",
        "+1 (415) 555-0132",
        "请打 +86-138-0013-8000",
    ],
)
def test_masking_still_redacts_phone_numbers(text: str) -> None:
    masked = mask_sensitive(data={"note": text})

    assert "[REDACTED_PHONE]" in masked["note"]
    assert "0013" not in masked["note"]


def test_usage_buckets_are_mutually_exclusive():
    response = StructuredModelResponse(
        output=_classification(),
        provider="compatible",
        model="model",
        provider_request_id="request",
        input_tokens=100,
        output_tokens=40,
        cached_input_tokens=30,
        reasoning_tokens=10,
        total_tokens=140,
    )

    assert exclusive_usage_buckets(response) == {
        "input": 70,
        "input_cached_tokens": 30,
        "output": 30,
        "output_reasoning_tokens": 10,
        "total": 140,
    }


def test_langfuse_records_only_summaries_usage_cost_and_route():
    client = _FakeLangfuseClient()
    telemetry = LangfuseTelemetry(
        public_key="public",
        secret_key="secret",
        host="https://cloud.langfuse.com",
        environment="development",
        client=client,
    )
    run = RunTrace(
        trace_id="a" * 32,
        run_id="run-1",
        session_id="session-1",
        feedback_hash="b" * 64,
        provider="openai_compatible",
        model="model",
        graph_version="graph-v1",
        policy_version="policy-v1",
        environment="development",
    )
    generation = GenerationTrace(
        operation="gate",
        prompt_version="gate-v1",
        provider="openai_compatible",
        model="model",
        input_summary={"bytes": 123, "sha256": "c" * 64},
    )
    response = StructuredModelResponse(
        output=_classification(),
        provider="openai_compatible",
        model="model",
        provider_request_id="request-1",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        estimated_cost=Decimal("0.001"),
    )

    with telemetry.start_run(run) as root:
        with telemetry.start_generation(generation) as observed:
            observed.succeed(response)
        root.finish(route="accepted_backend_bug", status="completed")
    telemetry.flush()

    rendered = repr((client.starts, [item.updates for item in client.observations]))
    assert "user@example.com" not in rendered
    assert "token=secret" not in rendered
    assert "[REDACTED_SUMMARY]" in rendered
    assert client.starts[0]["trace_context"] == {"trace_id": "a" * 32}
    assert client.observations[1].updates[0]["usage_details"]["total"] == 15
    assert client.observations[1].updates[0]["cost_details"]["total"] == 0.001
    assert client.flushed is True


def test_observed_provider_never_sends_message_content_to_telemetry():
    client = _FakeLangfuseClient()
    telemetry = LangfuseTelemetry(
        public_key="public",
        secret_key="secret",
        host="https://cloud.langfuse.com",
        environment="development",
        client=client,
    )
    provider = ObservedModelProvider(
        FakeModelProvider([_classification()]),
        telemetry,
        operation="gate",
        prompt_version="gate-v1",
    )

    asyncio.run(
        provider.generate_structured(
            (ModelMessage(role="user", content="private markdown user@example.com"),),
            GateClassification,
            tools=(),
            timeout_seconds=5,
        )
    )

    rendered = repr(client.starts)
    assert "private markdown" not in rendered
    assert "user@example.com" not in rendered


class _RejectingProvider:
    """按 provider 层失败契约抛错，用于验证 Trace 上的失败留痕。"""

    provider = "openai_compatible"
    model = "model"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def generate_structured(
        self,
        messages,
        response_schema,
        *,
        tools,
        timeout_seconds,
    ):
        del messages, response_schema, tools, timeout_seconds
        raise self._error


def _observed_failure_update(error: Exception) -> dict[str, object]:
    client = _FakeLangfuseClient()
    telemetry = LangfuseTelemetry(
        public_key="public",
        secret_key="secret",
        host="https://cloud.langfuse.com",
        environment="development",
        client=client,
    )
    provider = ObservedModelProvider(
        _RejectingProvider(error),
        telemetry,
        operation="generate-test",
        prompt_version="reproduction-v1",
    )

    with pytest.raises(type(error)):
        asyncio.run(
            provider.generate_structured(
                (ModelMessage(role="user", content="bounded input"),),
                GateClassification,
                tools=(),
                timeout_seconds=5,
            )
        )
    return client.observations[0].updates[0]


def test_langfuse_records_schema_error_paths_on_invalid_response():
    """只有 error_code 无法判断卡在哪一项，不合规字段必须上 Trace。"""

    update = _observed_failure_update(
        InvalidModelResponseError(
            "model returned invalid structured output",
            schema_errors="edits.0.content:string_too_long",
        )
    )

    assert update["level"] == "ERROR"
    assert update["status_message"] == "invalid_response"
    assert update["output"]["error_type"] == "InvalidModelResponseError"
    assert update["output"]["schema_errors"] == "edits.0.content:string_too_long"


def test_langfuse_omits_schema_errors_when_the_failure_has_no_field_detail():
    update = _observed_failure_update(ModelProviderError("model transport failed"))

    assert update["output"]["error_code"] == "provider_unavailable"
    assert "schema_errors" not in update["output"]


def test_langfuse_records_reproduction_tool_name_round_and_bounded_summary():
    client = _FakeLangfuseClient()
    telemetry = LangfuseTelemetry(
        public_key="public",
        secret_key="secret",
        host="https://cloud.langfuse.com",
        environment="development",
        client=client,
    )

    with telemetry.start_tool(
        ToolTrace(
            operation="run-reproduction",
            round=2,
            input_summary={"job_id": "safe-job", "contact": "user@example.com"},
        )
    ) as observed:
        observed.succeed({"status": "completed", "exit_code": 1})

    assert client.starts[0]["name"] == "run-reproduction"
    assert client.starts[0]["metadata"] == {"round": 2}
    assert client.starts[0]["input"]["contact"] == "[REDACTED]"
    assert client.observations[0].updates[0]["output"]["status"] == "completed"


def test_langfuse_start_failure_is_fail_open():
    class BrokenClient:
        def start_as_current_observation(self, **kwargs: object):
            del kwargs
            raise RuntimeError("contains-secret")

        def flush(self) -> None:
            raise RuntimeError("contains-secret")

    telemetry = LangfuseTelemetry(
        public_key="public",
        secret_key="secret",
        host="https://cloud.langfuse.com",
        environment="development",
        client=BrokenClient(),
    )
    trace = RunTrace(
        trace_id="a" * 32,
        run_id="run",
        session_id="session",
        feedback_hash="b" * 64,
        provider="provider",
        model="model",
        graph_version="graph",
        policy_version="policy",
        environment="development",
    )

    with telemetry.start_run(trace) as observation:
        observation.finish(route="needs_human", status="completed")
    telemetry.flush()
