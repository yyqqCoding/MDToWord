"""终态运行推送给公开展示站点。

站点原先靠每日 Cron 回填 Trace 快照，导致刚跑完的运行最长 24 小时看不到调用明细。
反馈量很低时定时轮询几乎全是空跑，改成运行结束即推送。

三条边界：

1. **只推 run_id，不推内容。** 站点收到信号后自己去 Langfuse 取 Trace、自己写快照。
   投影逻辑因此只存在于站点一侧，Agent 不需要知道快照长什么样。
2. **绝不影响修复。** 站点不可达、超时、返回 5xx 都只记一行日志。推送是 at-most-once，
   丢了由站点的按需补抓自愈。
3. **推送前先 flush。** Langfuse SDK 后台批量上报，根节点是最后关闭的，
   不 flush 就通知站点，站点大概率拿到不完整的树。
"""

import asyncio
import logging
from typing import Protocol

import httpx

from agent.controller import GateRunOutcome
from agent.domain.enums import TERMINAL_RUN_STATUSES


_LOGGER = logging.getLogger(__name__)


class TelemetryFlusher(Protocol):
    def flush(self) -> None: ...


class RunSettledListener(Protocol):
    """运行进入终态后的回调；实现方自行保证不抛错。"""

    async def on_run_settled(self, outcome: GateRunOutcome) -> None: ...


class TraceSiteNotifier:
    def __init__(
        self,
        *,
        endpoint: str,
        secret: str,
        client: httpx.AsyncClient,
        telemetry: TelemetryFlusher,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError("trace site webhook endpoint must use HTTP(S)")
        if not secret:
            raise ValueError("trace site webhook secret must not be empty")
        self._endpoint = endpoint
        self._secret = secret
        self._client = client
        self._telemetry = telemetry
        self._timeout_seconds = timeout_seconds

    async def on_run_settled(self, outcome: GateRunOutcome) -> None:
        if outcome.status not in TERMINAL_RUN_STATUSES:
            return

        # flush 是同步阻塞的 SDK 调用，放到线程里避免占住事件循环。
        try:
            await asyncio.to_thread(self._telemetry.flush)
        except Exception as exc:  # pragma: no cover - SDK 相关失败
            _LOGGER.warning("telemetry flush before notify failed: %s", type(exc).__name__)

        try:
            response = await self._client.post(
                self._endpoint,
                json={"run_id": str(outcome.run_id), "status": outcome.status.value},
                headers={"x-webhook-secret": self._secret},
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            # 只记异常类型：httpx 的异常文本会带完整 URL。
            _LOGGER.warning("trace site notify failed: %s", type(exc).__name__)
            return

        if response.status_code >= 400:
            # 不回显响应体，站点的错误信息不属于 Agent 日志。
            _LOGGER.warning("trace site notify responded %s", response.status_code)
            return
        _LOGGER.info("trace site notified for run %s", outcome.run_id)


def build_trace_site_notifier(
    settings: tuple[str, str] | None,
    *,
    client: httpx.AsyncClient,
    telemetry: TelemetryFlusher,
) -> TraceSiteNotifier | None:
    """未配置回调时返回 None，调用方据此完全跳过推送。"""

    if settings is None:
        return None
    endpoint, secret = settings
    return TraceSiteNotifier(
        endpoint=endpoint,
        secret=secret,
        client=client,
        telemetry=telemetry,
    )
