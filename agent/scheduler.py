import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from agent.controller import GateRunOutcome
from agent.domain.models import FeedbackRecord
from agent.repositories.base import AgentRunRepository, FeedbackRepository


_LOGGER = logging.getLogger(__name__)


class GateControllerPort(Protocol):
    async def start(self, feedback: FeedbackRecord) -> GateRunOutcome: ...

    async def resume(self, run_id: UUID) -> GateRunOutcome: ...


class RunSettledListenerPort(Protocol):
    async def on_run_settled(self, outcome: GateRunOutcome) -> None: ...


class FeedbackScheduler:
    """单并发轮询器；每轮优先恢复旧运行，再领取一条新反馈。"""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        run_repository: AgentRunRepository,
        controller: GateControllerPort,
        lease_seconds: int,
        max_attempts: int,
        poll_interval_seconds: float = 5.0,
        run_settled_listener: RunSettledListenerPort | None = None,
    ) -> None:
        if lease_seconds < 1 or max_attempts < 1 or poll_interval_seconds <= 0:
            raise ValueError("scheduler limits must be positive")
        self._feedback_repository = feedback_repository
        self._run_repository = run_repository
        self._controller = controller
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds
        self._run_settled_listener = run_settled_listener
        # 锁覆盖 claim 与整次 Gate，防止同一 Controller 的并发调用绕过并发 1。
        self._run_lock = asyncio.Lock()

    async def run_once(self) -> GateRunOutcome | None:
        outcome = await self._claim_and_run()
        if outcome is not None:
            await self._notify_settled(outcome)
        return outcome

    async def _claim_and_run(self) -> GateRunOutcome | None:
        async with self._run_lock:
            resumable = await self._run_repository.find_resumable()
            if resumable is not None:
                return await self._controller.resume(resumable.id)

            claimed = await self._feedback_repository.claim_next(
                now=datetime.now(UTC),
                lease_seconds=self._lease_seconds,
                max_attempts=self._max_attempts,
            )
            if claimed is None:
                return None
            return await self._controller.start(claimed)

    async def _notify_settled(self, outcome: GateRunOutcome) -> None:
        """通知刻意放在 _run_lock 之外：外部站点不可达时不应占住领取锁。"""

        if self._run_settled_listener is None:
            return
        try:
            await self._run_settled_listener.on_run_settled(outcome)
        except Exception as exc:
            # 监听器本应自己吞掉异常；这里再兜一层，保证轮询循环不会被通知拖死。
            _LOGGER.warning("run settled listener failed: %s", type(exc).__name__)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue
