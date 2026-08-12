import json
from uuid import uuid4

from agent.cli import fake_classification_for_route, main
from agent.controller import GateRunOutcome
from agent.domain.enums import AgentRunStatus, GateRoute


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


def test_publish_cli_requires_real_mode_and_configured_provider(capsys):
    feedback_id = str(uuid4())

    dry_exit = main(
        [
            "run",
            "--feedback-id",
            feedback_id,
            "--dry-run",
            "--publish",
            "--provider",
            "configured",
        ]
    )
    dry_output = json.loads(capsys.readouterr().out)
    assert dry_exit == 2
    assert dry_output["error"] == "cli_usage_error"

    fake_exit = main(["run", "--feedback-id", feedback_id, "--publish"])
    fake_output = json.loads(capsys.readouterr().out)
    assert fake_exit == 2
    assert fake_output["error"] == "cli_usage_error"
    assert "configured" in fake_output["message"]


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


def test_scheduler_refuses_to_claim_when_production_switch_is_off(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_AGENT_KEY", "supabase-secret")
    monkeypatch.setenv("PRODUCTION_SCHEDULER_ENABLED", "false")

    exit_code = main(["scheduler", "--once"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["error"] == "configuration_error"
    assert "PRODUCTION_SCHEDULER_ENABLED" in output["message"]


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


def test_publish_cli_reports_terminal_failure_instead_of_implying_success(
    monkeypatch,
    capsys,
):
    feedback_id = uuid4()
    run_id = uuid4()

    async def fake_run(*args, **kwargs):
        del args, kwargs
        return GateRunOutcome(
            run_id=run_id,
            feedback_id=feedback_id,
            route=GateRoute.ACCEPTED_BACKEND_BUG,
            completed=True,
            pr_url=None,
            status=AgentRunStatus.COMPLETED,
            error_code="invalid_fix_edit",
        )

    monkeypatch.setattr(
        "agent.cli.AgentConfig.from_env",
        lambda: object(),
    )
    monkeypatch.setattr("agent.cli._run_dry_gate", fake_run)

    exit_code = main(
        [
            "run",
            "--feedback-id",
            str(feedback_id),
            "--provider",
            "configured",
            "--publish",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["completed"] is True
    assert output["status"] == "completed"
    assert output["error_code"] == "invalid_fix_edit"
    assert output["published"] is False
