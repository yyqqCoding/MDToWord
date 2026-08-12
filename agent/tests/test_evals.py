import asyncio
import json

from agent.evals.runner import evaluate, load_cases, main


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

    assert report.total_cases == 12
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
    assert payload["total_cases"] == 12
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
