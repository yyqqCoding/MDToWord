from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from agent.domain.enums import FeedbackStatus, GateCategory, RiskLevel
from agent.domain.gate import GateResult
from agent.domain.models import AgentRunRecord, FeedbackRecord
from agent.domain.reproduction import ReproductionReport
from agent.domain.repair import RepairReport, ValidationResult


class FeedbackRepository(Protocol):
    """Controller 依赖的持久化端口；Fake 与 Supabase 必须保持相同语义。"""

    async def get(self, feedback_id: UUID) -> FeedbackRecord | None: ...

    async def claim_next(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None: ...

    async def claim_by_id(
        self,
        feedback_id: UUID,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> FeedbackRecord | None: ...

    async def transition(
        self,
        feedback_id: UUID,
        *,
        claim_token: UUID,
        target: FeedbackStatus,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        category: GateCategory | None = None,
        risk: RiskLevel | None = None,
    ) -> FeedbackRecord: ...

    async def find_open_by_fingerprint(
        self,
        content_fingerprint: str,
        *,
        excluding_feedback_id: UUID | None = None,
    ) -> FeedbackRecord | None: ...


class AgentRunRepository(Protocol):
    async def create(self, run: AgentRunRecord) -> AgentRunRecord: ...

    async def get(self, run_id: UUID) -> AgentRunRecord | None: ...

    async def find_resumable(self) -> AgentRunRecord | None: ...

    async def mark_gating(self, run_id: UUID) -> AgentRunRecord: ...

    async def complete_gate(
        self,
        run_id: UUID,
        result: GateResult,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost: Decimal = Decimal("0"),
    ) -> AgentRunRecord: ...

    async def mark_preparing_source(
        self,
        run_id: UUID,
        result: GateResult,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost: Decimal = Decimal("0"),
    ) -> AgentRunRecord: ...

    async def mark_reproducing(
        self,
        run_id: UUID,
        *,
        base_sha: str,
    ) -> AgentRunRecord: ...

    async def complete_reproduction(
        self,
        run_id: UUID,
        report: ReproductionReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
        reproduction_confirmed: bool = False,
        security_rejected: bool = False,
    ) -> AgentRunRecord: ...

    async def mark_validating(
        self,
        run_id: UUID,
        report: RepairReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord: ...

    async def complete_repair_failure(
        self,
        run_id: UUID,
        report: RepairReport,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
        security_rejected: bool = False,
    ) -> AgentRunRecord: ...

    async def complete_validation(
        self,
        run_id: UUID,
        result: ValidationResult,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord: ...

    async def exhaust_budget(
        self,
        run_id: UUID,
        *,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord: ...

    async def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
        model_calls: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        estimated_cost: Decimal,
    ) -> AgentRunRecord: ...
