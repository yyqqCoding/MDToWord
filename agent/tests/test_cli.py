import json
from uuid import uuid4

from agent.cli import fake_classification_for_route, main
from agent.domain.enums import GateRoute


def test_b2_cli_rejects_non_dry_run_before_loading_configuration(capsys):
    exit_code = main(["run", "--feedback-id", str(uuid4())])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["error"] == "dry_run_required"


def test_fake_cli_scenarios_cover_stage_b_routes():
    for route in (
        GateRoute.ACCEPTED_BACKEND_BUG,
        GateRoute.REJECTED_IRRELEVANT,
        GateRoute.QUARANTINED_SECURITY,
        GateRoute.OUT_OF_SCOPE,
        GateRoute.NEEDS_HUMAN,
    ):
        classification = fake_classification_for_route(route)
        assert classification.reason == "B2 Fake Provider 固定场景"


def test_reproduction_cli_rejects_fake_provider_before_loading_configuration(capsys):
    exit_code = main(
        [
            "run",
            "--feedback-id",
            str(uuid4()),
            "--dry-run",
            "--reproduce",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["error"] == "cli_usage_error"
    assert "configured" in output["message"]


def test_resume_cli_requires_reproduction_mode_before_loading_configuration(capsys):
    exit_code = main(
        [
            "run",
            "--resume-run-id",
            str(uuid4()),
            "--dry-run",
            "--provider",
            "configured",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["error"] == "cli_usage_error"
    assert "--reproduce" in output["message"]


def test_cli_error_never_echoes_environment_secret(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_AGENT_KEY", "must-not-print")
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    exit_code = main(
        [
            "run",
            "--feedback-id",
            str(uuid4()),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "SUPABASE_URL" in output
    assert "must-not-print" not in output


def test_configured_provider_validates_secrets_before_claiming_feedback(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_AGENT_KEY", "supabase-secret")
    monkeypatch.setenv(
        "AGENT_DATABASE_URL",
        "postgresql://agent:database-secret@example.com/postgres",
    )
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "model-secret")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)

    exit_code = main(
        [
            "run",
            "--feedback-id",
            str(uuid4()),
            "--dry-run",
            "--provider",
            "configured",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "MODEL_NAME" in output
    assert "MODEL_BASE_URL" in output
    assert "supabase-secret" not in output
    assert "database-secret" not in output
    assert "model-secret" not in output
