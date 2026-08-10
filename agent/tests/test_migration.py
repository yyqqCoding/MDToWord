from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "migrations" / "001_agent_foundation.sql"


def test_migration_is_additive_and_protects_claim_rpc():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "add column if not exists" in sql
    assert "create table if not exists public.agent_runs" in sql
    assert "for update skip locked" in sql
    assert "security definer" in sql
    assert "revoke execute" in sql
    assert "grant execute" in sql
    assert "feedback_agent_status_check" in sql
    assert "feedback_agent_counts_check" in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "delete from public.feedback" not in sql


def test_migration_reconciles_the_previous_agent_runs_shape():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "alter table public.agent_runs" in sql
    assert "add column if not exists validated_patch_sha256" in sql
    assert "add column if not exists langfuse_trace_id" in sql


def test_stage_b_migration_creates_private_checkpoint_schema():
    sql = (
        Path(__file__).parents[1] / "migrations" / "002_gate_runtime.sql"
    ).read_text(encoding="utf-8").lower()

    assert "create schema if not exists agent_runtime" in sql
    assert "revoke all on schema agent_runtime from public" in sql
    assert "claim_agent_feedback" in sql
    assert "claim_token" in sql
    assert "task_artifact_ref" in sql
