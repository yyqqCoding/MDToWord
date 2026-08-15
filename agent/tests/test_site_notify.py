"""展示站点完成回调的行为约束。

核心约束只有一条：**推送失败绝不能影响修复流程**。
下面的用例分别覆盖 HTTP 错误、传输异常与监听器自身抛错三种失败面。
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from agent.controller import GateRunOutcome
from agent.domain.enums import AgentRunStatus, FeedbackType, GateRoute
from agent.domain.models import FeedbackRecord
from agent.operations.site_notify import TraceSiteNotifier, build_trace_site_notifier
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.scheduler import FeedbackScheduler


class RecordingTelemetry:
    def __init__(self) -> None:
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def make_outcome(status: AgentRunStatus) -> GateRunOutcome:
    return GateRunOutcome(
        run_id=uuid4(),
        feedback_id=uuid4(),
        route=GateRoute.ACCEPTED_BACKEND_BUG,
        completed=status is AgentRunStatus.COMPLETED,
        status=status,
    )


def make_feedback() -> FeedbackRecord:
    now = datetime.now(UTC)
    return FeedbackRecord(
        id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="$x$",
        description="公式导出错误",
        created_at=now,
        updated_at=now,
    )


def build_notifier(telemetry: RecordingTelemetry, client: httpx.AsyncClient):
    return TraceSiteNotifier(
        endpoint="https://trace.example.com/api/hooks/run-finished",
        secret="shared-secret",
        client=client,
        telemetry=telemetry,
    )


def test_terminal_run_is_pushed_with_secret_and_flushed_first():
    seen: list[tuple[str, dict, str | None]] = []
    telemetry = RecordingTelemetry()
    flushes_at_request: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        flushes_at_request.append(telemetry.flushes)
        seen.append(
            (
                str(request.url),
                json.loads(request.content.decode()),
                request.headers.get("x-webhook-secret"),
            )
        )
        return httpx.Response(202, json={"accepted": True})

    outcome = make_outcome(AgentRunStatus.COMPLETED)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await build_notifier(telemetry, client).on_run_settled(outcome)

    asyncio.run(scenario())

    assert len(seen) == 1
    url, payload, secret = seen[0]
    assert url == "https://trace.example.com/api/hooks/run-finished"
    assert payload == {"run_id": str(outcome.run_id), "status": "completed"}
    assert secret == "shared-secret"
    # 必须先 flush 再 POST：根节点最后关闭，不 flush 站点会拿到不完整的树。
    assert flushes_at_request == [1]


def test_only_run_id_and_status_are_sent():
    """推送体不得携带任何内容，站点自己去 Langfuse 取。"""

    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode()))
        return httpx.Response(200)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(AgentRunStatus.FAILED))

    asyncio.run(scenario())
    assert set(payloads[0]) == {"run_id", "status"}


@pytest.mark.parametrize(
    "status",
    [
        AgentRunStatus.GATING,
        AgentRunStatus.REPRODUCING,
        AgentRunStatus.REPAIRING,
        AgentRunStatus.VALIDATING,
        AgentRunStatus.PUBLISHING,
    ],
)
def test_non_terminal_run_is_not_pushed(status: AgentRunStatus):
    """恢复中的运行 Trace 还不完整，推了只会让站点存下半截快照。"""

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(status))

    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("status_code", [200, 202])
def test_accepted_response_is_not_logged_as_failure(status_code: int, caplog):
    """站点先应答再抓取，正常返回 202。2xx 一律视为成功，不得记 warning。"""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"accepted": True})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(AgentRunStatus.COMPLETED))

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("status_code", [401, 404, 500, 503])
def test_site_error_response_does_not_raise(status_code: int):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "boom"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(AgentRunStatus.COMPLETED))

    asyncio.run(scenario())  # 不抛错即通过


def test_transport_failure_does_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("site unreachable", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(AgentRunStatus.COMPLETED))

    asyncio.run(scenario())


def test_failure_log_does_not_leak_endpoint_or_secret(caplog):
    """httpx 的异常文本会带完整 URL，日志只允许记异常类型。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed to connect to trace.example.com", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            notifier = build_notifier(RecordingTelemetry(), client)
            await notifier.on_run_settled(make_outcome(AgentRunStatus.COMPLETED))

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "trace.example.com" not in logged
    assert "shared-secret" not in logged
    assert "ConnectError" in logged


def test_notifier_is_disabled_when_not_configured():
    async def scenario():
        async with httpx.AsyncClient() as client:
            return build_trace_site_notifier(
                None,
                client=client,
                telemetry=RecordingTelemetry(),
            )

    assert asyncio.run(scenario()) is None


def test_notifier_rejects_non_http_endpoint():
    async def scenario():
        async with httpx.AsyncClient() as client:
            build_trace_site_notifier(
                ("trace.example.com", "secret"),
                client=client,
                telemetry=RecordingTelemetry(),
            )

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_scheduler_hands_terminal_outcome_to_listener():
    outcome = make_outcome(AgentRunStatus.COMPLETED)

    class StubController:
        async def start(self, feedback):
            return outcome

        async def resume(self, run_id):
            raise AssertionError("no resumable run expected")

    class RecordingListener:
        def __init__(self) -> None:
            self.seen: list[GateRunOutcome] = []

        async def on_run_settled(self, value: GateRunOutcome) -> None:
            self.seen.append(value)

    listener = RecordingListener()

    async def scenario():
        scheduler = FeedbackScheduler(
            feedback_repository=FakeFeedbackRepository([make_feedback()]),
            run_repository=FakeAgentRunRepository(),
            controller=StubController(),
            lease_seconds=60,
            max_attempts=3,
            run_settled_listener=listener,
        )
        await scheduler.run_once()

    asyncio.run(scenario())
    assert listener.seen == [outcome]


def test_scheduler_survives_a_listener_that_raises():
    """监听器本应自己吞异常；即便没吞住，轮询循环也不能被拖死。"""

    outcome = make_outcome(AgentRunStatus.COMPLETED)

    class StubController:
        async def start(self, feedback):
            return outcome

        async def resume(self, run_id):
            raise AssertionError("no resumable run expected")

    class BrokenListener:
        async def on_run_settled(self, value: GateRunOutcome) -> None:
            raise RuntimeError("listener exploded")

    async def scenario():
        scheduler = FeedbackScheduler(
            feedback_repository=FakeFeedbackRepository([make_feedback()]),
            run_repository=FakeAgentRunRepository(),
            controller=StubController(),
            lease_seconds=60,
            max_attempts=3,
            run_settled_listener=BrokenListener(),
        )
        return await scheduler.run_once()

    assert asyncio.run(scenario()) is outcome
