"""阶段 G Gate 离线评估入口；输出只包含用例 ID 和聚合指标。"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent.config import AgentConfig
from agent.domain.enums import FeedbackType, GateCategory, GateRoute
from agent.domain.gate import GateClassification
from agent.domain.models import TaskArtifact
from agent.gate import GATE_PROMPT_VERSION, execute_feedback_gate
from agent.graph import GRAPH_VERSION, POLICY_VERSION
from agent.providers.base import ModelProvider
from agent.providers.fake import FakeModelProvider
from agent.providers.observed import ObservedModelProvider
from agent.providers.openai_compatible import OpenAICompatibleProvider
from agent.telemetry.base import NoopTelemetry, RunTrace, Telemetry
from agent.telemetry.langfuse import LangfuseTelemetry


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    feedback_type: FeedbackType
    markdown_content: str
    description: str = Field(min_length=1, max_length=1000)
    classification: GateClassification | None
    expected_route: GateRoute
    expected_category: GateCategory
    expected_oracle: str | None


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    route: GateRoute | None
    category: GateCategory | None
    route_correct: bool
    category_correct: bool
    schema_compliant: bool
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0)
    latency_ms: int = Field(ge=0)
    policy_reason: str | None = None
    error_code: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    total_cases: int
    gate_accuracy: float
    category_accuracy: float
    automatable_precision: float | None
    schema_compliance: float
    injection_quarantine_recall: float | None
    injection_false_positive_rate: float | None
    average_input_tokens: float
    average_output_tokens: float
    average_total_tokens: float
    average_estimated_cost: float
    average_latency_ms: float
    oracle_coverage: float
    # Gate-only Runner 不伪造后续 Sandbox/修复指标；Fake 全链路由 pytest 验收。
    reproduction_success: float | None = None
    patch_policy_pass_rate: float | None = None
    validated_repair_rate: float | None = None
    cases: tuple[CaseResult, ...]


@dataclass(frozen=True)
class _Runtime:
    provider: ModelProvider
    telemetry: Telemetry
    provider_name: str
    model_name: str
    client: httpx.AsyncClient | None = None


def load_cases() -> tuple[EvaluationCase, ...]:
    raw = files("agent.evals").joinpath("cases.json").read_text("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("evaluation dataset must be a JSON list")
    cases = tuple(EvaluationCase.model_validate(item) for item in payload)
    if not 10 <= len(cases) <= 20 or len({item.id for item in cases}) != len(cases):
        raise ValueError("evaluation dataset must contain 10-20 unique cases")
    return cases


async def evaluate(
    cases: tuple[EvaluationCase, ...],
    *,
    provider: ModelProvider | None = None,
    telemetry: Telemetry | None = None,
) -> EvaluationReport:
    observations = telemetry or NoopTelemetry()
    results: list[CaseResult] = []
    provider_name = getattr(provider, "provider", "fake") if provider else "fake"
    model_name = getattr(provider, "model", "eval-fixture") if provider else "eval-fixture"

    for case in cases:
        case_provider = provider or FakeModelProvider(
            [case.classification] if case.classification is not None else []
        )
        observed = ObservedModelProvider(
            case_provider,
            observations,
            operation="eval_gate",
            prompt_version=GATE_PROMPT_VERSION,
        )
        task = TaskArtifact(
            feedback_id=uuid5(NAMESPACE_URL, f"mdtoword-eval:{case.id}"),
            feedback_type=case.feedback_type,
            markdown_content=case.markdown_content,
            description=case.description,
            content_fingerprint=hashlib.sha256(case.id.encode()).hexdigest(),
        )
        started = time.perf_counter()
        trace_id = hashlib.sha256(f"mdtoword-eval:{case.id}".encode()).hexdigest()[:32]
        trace = RunTrace(
            trace_id=trace_id,
            run_id=f"eval-{case.id}",
            session_id="offline-evaluation",
            feedback_hash=task.content_fingerprint,
            provider=getattr(case_provider, "provider", "fake"),
            model=getattr(case_provider, "model", "eval-fixture"),
            graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION,
            environment="evaluation",
        )
        with observations.start_run(trace) as run_observation:
            try:
                execution = await execute_feedback_gate(task, observed)
            except Exception as exc:
                elapsed = int((time.perf_counter() - started) * 1000)
                results.append(
                    CaseResult(
                        id=case.id,
                        route=None,
                        category=None,
                        route_correct=False,
                        category_correct=False,
                        schema_compliant=False,
                        model_calls=0,
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_cost=Decimal("0"),
                        latency_ms=elapsed,
                        error_code=getattr(exc, "error_code", "evaluation_error"),
                    )
                )
                run_observation.finish(route=None, status="failed")
                continue
            result = execution.result
            elapsed = int((time.perf_counter() - started) * 1000)
            results.append(
                CaseResult(
                    id=case.id,
                    route=result.route,
                    category=result.category,
                    route_correct=result.route is case.expected_route,
                    category_correct=result.category is case.expected_category,
                    schema_compliant=True,
                    model_calls=result.model_calls,
                    input_tokens=execution.input_tokens,
                    output_tokens=execution.output_tokens,
                    total_tokens=execution.total_tokens,
                    estimated_cost=execution.estimated_cost,
                    latency_ms=elapsed,
                    policy_reason=result.policy_reason,
                )
            )
            run_observation.finish(route=result.route.value, status="completed")

    return _report(cases, tuple(results), provider_name, model_name)


def _report(
    cases: tuple[EvaluationCase, ...],
    results: tuple[CaseResult, ...],
    provider: str,
    model: str,
) -> EvaluationReport:
    count = len(cases)
    by_id = {item.id: item for item in results}
    accepted = GateRoute.ACCEPTED_BACKEND_BUG
    predicted_accepted = [item for item in results if item.route is accepted]
    true_accepted = sum(
        by_id[case.id].route is accepted and case.expected_route is accepted
        for case in cases
    )
    injection_cases = [case for case in cases if case.id == "prompt-injection"]
    non_injection = [case for case in cases if case.id != "prompt-injection"]
    oracle_cases = [case for case in cases if case.expected_oracle is not None]
    return EvaluationReport(
        provider=provider,
        model=model,
        total_cases=count,
        gate_accuracy=_ratio(sum(item.route_correct for item in results), count),
        category_accuracy=_ratio(sum(item.category_correct for item in results), count),
        automatable_precision=_optional_ratio(
            true_accepted,
            len(predicted_accepted),
        ),
        schema_compliance=_ratio(sum(item.schema_compliant for item in results), count),
        injection_quarantine_recall=_optional_ratio(
            sum(by_id[item.id].route is GateRoute.QUARANTINED_SECURITY for item in injection_cases),
            len(injection_cases),
        ),
        injection_false_positive_rate=_optional_ratio(
            sum(by_id[item.id].route is GateRoute.QUARANTINED_SECURITY for item in non_injection),
            len(non_injection),
        ),
        average_input_tokens=_average(item.input_tokens for item in results),
        average_output_tokens=_average(item.output_tokens for item in results),
        average_total_tokens=_average(item.total_tokens for item in results),
        average_estimated_cost=_average(float(item.estimated_cost) for item in results),
        average_latency_ms=_average(item.latency_ms for item in results),
        oracle_coverage=_ratio(len(oracle_cases), count),
        cases=results,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _optional_ratio(numerator: int, denominator: int) -> float | None:
    """没有适用样本时指标未定义，避免把空集合错误报告成 0%。"""

    return round(numerator / denominator, 6) if denominator else None


def _average(values) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 0.0


async def _runtime(mode: str) -> _Runtime:
    if mode == "fake":
        return _Runtime(
            provider=FakeModelProvider([]),
            telemetry=NoopTelemetry(),
            provider_name="fake",
            model_name="eval-fixture",
        )
    config = AgentConfig.from_env()
    model_name, api_key, base_url = config.require_model_settings()
    host, public_key, secret_key = config.require_langfuse_settings()
    client = httpx.AsyncClient(timeout=30)
    return _Runtime(
        provider=OpenAICompatibleProvider(
            api_key=api_key,
            model=model_name,
            base_url=base_url,
            client=client,
            input_cost_per_million=config.model_input_cost_per_million,
            output_cost_per_million=config.model_output_cost_per_million,
        ),
        telemetry=LangfuseTelemetry(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            environment="evaluation",
        ),
        provider_name="openai_compatible",
        model_name=model_name,
        client=client,
    )


async def _run(
    mode: str,
    case_ids: tuple[str, ...] = (),
) -> EvaluationReport:
    runtime = await _runtime(mode)
    try:
        cases = load_cases()
        if case_ids:
            requested = set(case_ids)
            known = {case.id for case in cases}
            unknown = sorted(requested - known)
            if unknown:
                raise ValueError(
                    "unknown evaluation case IDs: " + ", ".join(unknown)
                )
            # 数据集原始顺序是稳定报告顺序；参数顺序不影响结果。
            cases = tuple(case for case in cases if case.id in requested)
        return await evaluate(
            cases,
            provider=runtime.provider if mode == "configured" else None,
            telemetry=runtime.telemetry,
        )
    finally:
        runtime.telemetry.flush()
        if runtime.client is not None:
            await runtime.client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mdtoword-agent-evals")
    parser.add_argument("--provider", choices=("fake", "configured"), default="fake")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="evaluate only this stable case ID; repeat to select multiple cases",
    )
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(args.provider, tuple(args.case_id)))
    except Exception as exc:
        print(
            json.dumps(
                {"error": getattr(exc, "error_code", "evaluation_failed")},
                sort_keys=True,
            ),
            file=sys.stdout,
        )
        return 1
    print(report.model_dump_json(indent=2), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
