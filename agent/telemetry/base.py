from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from agent.providers.base import StructuredModelResponse


@dataclass(frozen=True)
class RunTrace:
    trace_id: str
    run_id: str
    session_id: str
    feedback_hash: str
    provider: str
    model: str
    graph_version: str
    policy_version: str
    environment: str


@dataclass(frozen=True)
class GenerationTrace:
    operation: str
    prompt_version: str
    provider: str
    model: str
    input_summary: dict[str, object]


class RunObservation(Protocol):
    def finish(self, *, route: str | None, status: str) -> None: ...


class GenerationObservation(Protocol):
    def succeed(self, response: StructuredModelResponse[object]) -> None: ...

    def fail(self, *, error_code: str, error_type: str) -> None: ...


class Telemetry(Protocol):
    def start_run(self, trace: RunTrace) -> AbstractContextManager[RunObservation]: ...

    def start_generation(
        self,
        trace: GenerationTrace,
    ) -> AbstractContextManager[GenerationObservation]: ...

    def flush(self) -> None: ...


class _NoopRunObservation:
    def finish(self, *, route: str | None, status: str) -> None:
        del route, status


class _NoopGenerationObservation:
    def succeed(self, response: StructuredModelResponse[object]) -> None:
        del response

    def fail(self, *, error_code: str, error_type: str) -> None:
        del error_code, error_type


class NoopTelemetry:
    @contextmanager
    def start_run(self, trace: RunTrace):
        del trace
        yield _NoopRunObservation()

    @contextmanager
    def start_generation(self, trace: GenerationTrace):
        del trace
        yield _NoopGenerationObservation()

    def flush(self) -> None:
        return None


def exclusive_usage_buckets(
    response: StructuredModelResponse[object],
) -> dict[str, int]:
    """把 OpenAI inclusive usage 转成 Langfuse 要求的互斥 bucket。"""

    cached = min(response.cached_input_tokens, response.input_tokens)
    reasoning = min(response.reasoning_tokens, response.output_tokens)
    return {
        "input": response.input_tokens - cached,
        "input_cached_tokens": cached,
        "output": response.output_tokens - reasoning,
        "output_reasoning_tokens": reasoning,
        "total": response.total_tokens,
    }


def cost_details(response: StructuredModelResponse[object]) -> dict[str, float] | None:
    if response.estimated_cost <= Decimal("0"):
        return None
    return {"total": float(response.estimated_cost)}
