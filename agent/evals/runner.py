"""阶段 G Gate 离线评估入口；输出只包含用例 ID 和聚合指标。"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agent.config import AgentConfig
from agent.domain.enums import (
    FeedbackType,
    GateArea,
    GateCategory,
    GateIntent,
    GateRoute,
)
from agent.domain.gate import GateClassification
from agent.domain.models import TaskArtifact
from agent.gate import (
    GATE_PROMPT_VERSION,
    GATE_TIMEOUT_SECONDS,
    execute_feedback_gate,
)
from agent.graph import GRAPH_VERSION, POLICY_VERSION
from agent.providers.base import ModelProvider
from agent.providers.fake import FakeModelProvider
from agent.providers.observed import ObservedModelProvider
from agent.providers.openai_compatible import OpenAICompatibleProvider
from agent.telemetry.base import NoopTelemetry, RunTrace, Telemetry
from agent.telemetry.langfuse import LangfuseTelemetry


_PROMPT_RESOURCES = {
    "gate-v9": "prompts/gate-v9.md",
    "gate-v10": "prompts/gate-v10.md",
}


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
    sample: int = Field(ge=1)
    route: GateRoute | None
    category: GateCategory | None
    raw_intent: GateIntent | None = None
    raw_area: GateArea | None = None
    raw_category: GateCategory | None = None
    raw_sufficient_information: bool | None = None
    raw_classification_correct: bool | None = None
    raw_category_correct: bool | None = None
    raw_sufficiency_correct: bool | None = None
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
    prompt_version: str
    total_cases: int
    total_samples: int
    repeats: int = Field(ge=1)
    raw_classification_accuracy: float | None
    raw_category_accuracy: float | None
    raw_sufficiency_accuracy: float | None
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
    gate_timeout_seconds: float = GATE_TIMEOUT_SECONDS
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
    gate_timeout_seconds: float = GATE_TIMEOUT_SECONDS,
    system_prompt: str | None = None,
    prompt_version: str = GATE_PROMPT_VERSION,
    repeats: int = 1,
) -> EvaluationReport:
    if not 1 <= repeats <= 5:
        raise ValueError("evaluation repeats must be between 1 and 5")
    observations = telemetry or NoopTelemetry()
    results: list[CaseResult] = []
    provider_name = getattr(provider, "provider", "fake") if provider else "fake"
    model_name = getattr(provider, "model", "eval-fixture") if provider else "eval-fixture"

    for sample in range(1, repeats + 1):
        for case in cases:
            case_provider = provider or FakeModelProvider(
                [case.classification] if case.classification is not None else []
            )
            observed = ObservedModelProvider(
                case_provider,
                observations,
                operation="eval_gate",
                prompt_version=prompt_version,
            )
            task = TaskArtifact(
                feedback_id=uuid5(NAMESPACE_URL, f"mdtoword-eval:{case.id}"),
                feedback_type=case.feedback_type,
                markdown_content=case.markdown_content,
                description=case.description,
                content_fingerprint=hashlib.sha256(case.id.encode()).hexdigest(),
            )
            started = time.perf_counter()
            trace_id = hashlib.sha256(
                f"mdtoword-eval:{prompt_version}:{case.id}:{sample}".encode()
            ).hexdigest()[:32]
            trace = RunTrace(
                trace_id=trace_id,
                run_id=f"eval-{case.id}-{sample}",
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
                    execution = await execute_feedback_gate(
                        task,
                        observed,
                        timeout_seconds=gate_timeout_seconds,
                        system_prompt=system_prompt,
                    )
                except Exception as exc:
                    elapsed = int((time.perf_counter() - started) * 1000)
                    results.append(
                        CaseResult(
                            id=case.id,
                            sample=sample,
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
                raw = execution.classification
                elapsed = int((time.perf_counter() - started) * 1000)
                results.append(
                    CaseResult(
                        id=case.id,
                        sample=sample,
                        route=result.route,
                        category=result.category,
                        raw_intent=raw.intent if raw is not None else None,
                        raw_area=raw.area if raw is not None else None,
                        raw_category=raw.category if raw is not None else None,
                        raw_sufficient_information=(
                            raw.sufficient_information if raw is not None else None
                        ),
                        raw_classification_correct=_raw_classification_correct(
                            case.classification,
                            raw,
                        ),
                        raw_category_correct=_raw_field_correct(
                            case.classification,
                            raw,
                            "category",
                        ),
                        raw_sufficiency_correct=_raw_field_correct(
                            case.classification,
                            raw,
                            "sufficient_information",
                        ),
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

    return _report(
        cases,
        tuple(results),
        provider_name,
        model_name,
        prompt_version=prompt_version,
        repeats=repeats,
    )


def _report(
    cases: tuple[EvaluationCase, ...],
    results: tuple[CaseResult, ...],
    provider: str,
    model: str,
    *,
    prompt_version: str,
    repeats: int,
) -> EvaluationReport:
    count = len(results)
    expected_by_id = {item.id: item for item in cases}
    accepted = GateRoute.ACCEPTED_BACKEND_BUG
    predicted_accepted = [item for item in results if item.route is accepted]
    true_accepted = sum(
        item.route is accepted
        and expected_by_id[item.id].expected_route is accepted
        for item in results
    )
    injection_results = [
        item
        for item in results
        if (
            expected_by_id[item.id].classification is not None
            and expected_by_id[item.id].classification.injection_suspected
        )
    ]
    non_injection_results = [
        item
        for item in results
        if not (
            expected_by_id[item.id].classification is not None
            and expected_by_id[item.id].classification.injection_suspected
        )
    ]
    oracle_cases = [case for case in cases if case.expected_oracle is not None]
    raw_results = [item for item in results if item.raw_classification_correct is not None]
    return EvaluationReport(
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        total_cases=len(cases),
        total_samples=count,
        repeats=repeats,
        raw_classification_accuracy=_optional_ratio(
            sum(item.raw_classification_correct is True for item in raw_results),
            len(raw_results),
        ),
        raw_category_accuracy=_optional_ratio(
            sum(item.raw_category_correct is True for item in raw_results),
            len(raw_results),
        ),
        raw_sufficiency_accuracy=_optional_ratio(
            sum(item.raw_sufficiency_correct is True for item in raw_results),
            len(raw_results),
        ),
        gate_accuracy=_ratio(sum(item.route_correct for item in results), count),
        category_accuracy=_ratio(sum(item.category_correct for item in results), count),
        automatable_precision=_optional_ratio(
            true_accepted,
            len(predicted_accepted),
        ),
        schema_compliance=_ratio(sum(item.schema_compliant for item in results), count),
        injection_quarantine_recall=_optional_ratio(
            sum(item.route is GateRoute.QUARANTINED_SECURITY for item in injection_results),
            len(injection_results),
        ),
        injection_false_positive_rate=_optional_ratio(
            sum(item.route is GateRoute.QUARANTINED_SECURITY for item in non_injection_results),
            len(non_injection_results),
        ),
        average_input_tokens=_average(item.input_tokens for item in results),
        average_output_tokens=_average(item.output_tokens for item in results),
        average_total_tokens=_average(item.total_tokens for item in results),
        average_estimated_cost=_average(float(item.estimated_cost) for item in results),
        average_latency_ms=_average(item.latency_ms for item in results),
        oracle_coverage=_ratio(len(oracle_cases), len(cases)),
        cases=results,
    )


def _raw_classification_correct(
    expected: GateClassification | None,
    actual: GateClassification | None,
) -> bool | None:
    if expected is None:
        return None
    if actual is None:
        return False
    return all(
        getattr(actual, field) == getattr(expected, field)
        for field in (
            "intent",
            "area",
            "category",
            "sufficient_information",
            "injection_suspected",
            "requires_extension_change",
        )
    ) and (actual.issue_title is None) == (expected.issue_title is None) and (
        (actual.issue_summary is None) == (expected.issue_summary is None)
    )


def _raw_field_correct(
    expected: GateClassification | None,
    actual: GateClassification | None,
    field: str,
) -> bool | None:
    if expected is None:
        return None
    if actual is None:
        return False
    return getattr(actual, field) == getattr(expected, field)


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
    if mode == "evaluation":
        model_name = _required_eval_env("EVAL_MODEL_NAME")
        api_key = _required_eval_env("EVAL_MODEL_API_KEY")
        base_url = _required_eval_env("EVAL_MODEL_BASE_URL")
        timeout = _eval_timeout_seconds()
        client = httpx.AsyncClient(timeout=timeout)
        return _Runtime(
            provider=OpenAICompatibleProvider(
                api_key=api_key,
                model=model_name,
                base_url=base_url,
                client=client,
                # Prompt评测的每个sample必须对应一次真实响应，不能由重试或备用模型
                # 悄悄改变样本数量和模型身份。
                max_format_retries=0,
                max_transport_retries=0,
            ),
            telemetry=NoopTelemetry(),
            provider_name="evaluation_openai_compatible",
            model_name=model_name,
            gate_timeout_seconds=timeout,
            client=client,
        )
    config = AgentConfig.from_env()
    model_name, api_key, base_url = config.require_model_settings()
    fallback_model = config.fallback_model_settings()
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
            fallback_model=fallback_model[0] if fallback_model is not None else None,
            fallback_api_key=(
                fallback_model[1] if fallback_model is not None else None
            ),
            fallback_base_url=(
                fallback_model[2] if fallback_model is not None else None
            ),
            fallback_input_cost_per_million=(
                config.fallback_model_input_cost_per_million
            ),
            fallback_output_cost_per_million=(
                config.fallback_model_output_cost_per_million
            ),
        ),
        telemetry=LangfuseTelemetry(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            environment="evaluation",
        ),
        provider_name="openai_compatible",
        model_name=model_name,
        gate_timeout_seconds=config.gate_model_timeout_seconds,
        client=client,
    )


def _required_eval_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the evaluation provider")
    return value


def _eval_timeout_seconds() -> float:
    raw = os.getenv("EVAL_MODEL_TIMEOUT_SECONDS", "120").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("EVAL_MODEL_TIMEOUT_SECONDS must be numeric") from exc
    if not 10 <= value <= 300:
        raise ValueError("EVAL_MODEL_TIMEOUT_SECONDS must be between 10 and 300")
    return value


async def _run(
    mode: str,
    case_ids: tuple[str, ...] = (),
    *,
    prompt_selector: str = "production",
    repeats: int = 1,
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
        system_prompt, prompt_version = _load_prompt(prompt_selector)
        return await evaluate(
            cases,
            provider=runtime.provider if mode != "fake" else None,
            telemetry=runtime.telemetry,
            gate_timeout_seconds=runtime.gate_timeout_seconds,
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            repeats=repeats,
        )
    finally:
        runtime.telemetry.flush()
        if runtime.client is not None:
            await runtime.client.aclose()


def _load_prompt(selector: str) -> tuple[str | None, str]:
    if selector == "production":
        return None, GATE_PROMPT_VERSION
    resource = _PROMPT_RESOURCES.get(selector)
    if resource is None:
        raise ValueError("unknown evaluation prompt: " + selector)
    return files("agent.evals").joinpath(resource).read_text("utf-8"), selector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mdtoword-agent-evals")
    parser.add_argument(
        "--provider",
        choices=("fake", "configured", "evaluation"),
        default="fake",
    )
    parser.add_argument(
        "--prompt",
        choices=("production", *_PROMPT_RESOURCES),
        default="production",
        help="select the frozen production baseline or a candidate prompt",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="repeat each case 1..5 times to measure classification stability",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="evaluate only this stable case ID; repeat to select multiple cases",
    )
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            _run(
                args.provider,
                tuple(args.case_id),
                prompt_selector=args.prompt,
                repeats=args.repeat,
            )
        )
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
