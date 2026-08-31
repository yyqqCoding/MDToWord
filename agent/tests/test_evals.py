import asyncio
import json
from importlib.resources import files

from agent.domain.enums import GateArea, GateCategory, GateIntent, GateRoute
from agent.domain.gate import GateClassification
from agent.evals.runner import EvaluationCase, _runtime, evaluate, load_cases, main
from agent.providers.fake import FakeModelProvider


def test_offline_eval_dataset_has_required_breadth_and_no_contact_field():
    cases = load_cases()

    assert 10 <= len(cases) <= 20
    assert len({item.id for item in cases}) == len(cases)
    assert {item.id for item in cases} >= {
        "table-structure",
        "formula-structure",
        "heading-style",
        "conversion-crash",
        "extension-ui",
        "feature-request",
        "irrelevant-content",
        "insufficient-information",
        "prompt-injection",
    }
    assert all("contact" not in type(item).model_fields for item in cases)


def test_fake_offline_eval_is_deterministic_and_perfect():
    report = asyncio.run(evaluate(load_cases()))

    assert report.total_cases == 20
    assert report.total_samples == 20
    assert report.repeats == 1
    assert report.raw_classification_accuracy == 1.0
    assert report.raw_category_accuracy == 1.0
    assert report.raw_sufficiency_accuracy == 1.0
    assert report.gate_accuracy == 1.0
    assert report.category_accuracy == 1.0
    assert report.automatable_precision == 1.0
    assert report.schema_compliance == 1.0
    assert report.injection_quarantine_recall == 1.0
    assert report.injection_false_positive_rate == 0.0
    assert all(item.policy_reason is not None for item in report.cases)
    assert report.reproduction_success is None
    assert report.patch_policy_pass_rate is None
    assert report.validated_repair_rate is None


def test_eval_cli_prints_only_structured_results(capsys):
    exit_code = main(["--provider", "fake"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["total_cases"] == 20
    rendered = json.dumps(payload)
    assert "markdown_content" not in rendered
    assert "description" not in rendered


def test_eval_cli_can_select_stable_case_ids(capsys):
    exit_code = main(
        [
            "--provider",
            "fake",
            "--case-id",
            "conversion-crash",
            "--case-id",
            "visual-preference",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["total_cases"] == 2
    assert [item["id"] for item in payload["cases"]] == [
        "conversion-crash",
        "visual-preference",
    ]
    assert payload["injection_quarantine_recall"] is None


def test_eval_can_compare_frozen_prompt_with_repeated_samples(capsys):
    exit_code = main(
        [
            "--provider",
            "fake",
            "--prompt",
            "gate-v10",
            "--repeat",
            "3",
            "--case-id",
            "conversion-failed-prescript",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["prompt_version"] == "gate-v10"
    assert payload["total_cases"] == 1
    assert payload["total_samples"] == 3
    assert payload["raw_classification_accuracy"] == 1.0
    assert [item["sample"] for item in payload["cases"]] == [1, 2, 3]


def test_raw_metrics_expose_model_error_even_when_policy_route_is_correct():
    expected = GateClassification(
        intent=GateIntent.BUG_REPORT,
        area=GateArea.BACKEND,
        category=GateCategory.CONVERSION_CRASH,
        relevance=0.99,
        sufficient_information=True,
        injection_suspected=False,
        requires_extension_change=False,
        reason="expected conversion crash",
    )
    unstable = expected.model_copy(
        update={
            "category": GateCategory.UNKNOWN,
            "sufficient_information": False,
        }
    )
    case = EvaluationCase(
        id="raw-policy-separation",
        feedback_type="bug",
        markdown_content="$$x$$",
        description="后端转换直接报错",
        classification=expected,
        expected_route=GateRoute.ACCEPTED_BACKEND_BUG,
        expected_category=GateCategory.CONVERSION_CRASH,
        expected_oracle="conversion_succeeds",
    )

    report = asyncio.run(
        evaluate((case,), provider=FakeModelProvider([unstable]))
    )

    assert report.gate_accuracy == 1.0
    assert report.category_accuracy == 1.0
    assert report.raw_classification_accuracy == 0.0
    assert report.raw_category_accuracy == 0.0
    assert report.raw_sufficiency_accuracy == 0.0


def test_frozen_gate_v10_prompt_matches_current_production_prompt():
    production = files("agent.prompts").joinpath("gate.md").read_text("utf-8")
    frozen = files("agent.evals").joinpath("prompts/gate-v10.md").read_text("utf-8")

    assert frozen == production


def test_frozen_gate_v9_prompt_remains_available_as_ab_baseline():
    production = files("agent.prompts").joinpath("gate.md").read_text("utf-8")
    baseline = files("agent.evals").joinpath("prompts/gate-v9.md").read_text("utf-8")

    assert baseline != production


def test_evaluation_provider_is_isolated_from_production_and_has_no_retries(
    monkeypatch,
):
    monkeypatch.setenv("EVAL_MODEL_NAME", "eval-only-model")
    monkeypatch.setenv("EVAL_MODEL_API_KEY", "eval-only-secret")
    monkeypatch.setenv("EVAL_MODEL_BASE_URL", "https://eval.invalid/v1")
    monkeypatch.setenv("EVAL_MODEL_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MODEL_NAME", "production-model-must-not-be-used")
    monkeypatch.setenv("FALLBACK_MODEL_ENABLED", "true")

    runtime = asyncio.run(_runtime("evaluation"))
    try:
        assert runtime.model_name == "eval-only-model"
        assert runtime.provider_name == "evaluation_openai_compatible"
        assert runtime.gate_timeout_seconds == 45
        assert runtime.provider._fallback_target is None
        assert runtime.provider._max_transport_retries == 0
        assert runtime.provider._max_format_retries == 0
    finally:
        asyncio.run(runtime.client.aclose())
