import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from agent.controller import GateRunOutcome
from agent.domain.models import FeedbackRecord
from agent.repositories.base import AgentRunRepository, FeedbackRepository


class GateControllerPort(Protocol):
    async def start(self, feedback: FeedbackRecord) -> GateRunOutcome: ...

    async def resume(self, run_id: UUID) -> GateRunOutcome: ...


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
    ) -> None:
        if lease_seconds < 1 or max_attempts < 1 or poll_interval_seconds <= 0:
            raise ValueError("scheduler limits must be positive")
        self._feedback_repository = feedback_repository
        self._run_repository = run_repository
        self._controller = controller
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds
        # 锁覆盖 claim 与整次 Gate，防止同一 Controller 的并发调用绕过并发 1。
        self._run_lock = asyncio.Lock()

    async def run_once(self) -> GateRunOutcome | None:
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
