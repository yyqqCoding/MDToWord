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


def test_stage_d_migration_indexes_all_resumable_reproduction_states():
    sql = (
        Path(__file__).parents[1] / "migrations" / "003_reproduction_runtime.sql"
    ).read_text(encoding="utf-8").lower()

    assert "preparing_source" in sql
    assert "reproducing" in sql
    assert "agent_runs_resumable_idx" in sql


def test_stage_e_migration_adds_repair_summary_and_resumable_states():
    sql = (
        Path(__file__).parents[1] / "migrations" / "004_repair_runtime.sql"
    ).read_text(encoding="utf-8").lower()

    assert "add column if not exists repair jsonb" in sql
    assert "repairing" in sql
    assert "validating" in sql
    assert "agent_runs_resumable_idx" in sql


def test_stage_f_migration_makes_publication_resumable():
    sql = (
        Path(__file__).parents[1] / "migrations" / "005_publication_runtime.sql"
    ).read_text(encoding="utf-8").lower()

    assert "publishing" in sql
    assert "agent_runs_resumable_idx" in sql
    assert "delete" not in sql


def test_stage_i_migration_adds_issue_state_and_fail_closed_public_projection():
    sql = (
        Path(__file__).parents[1]
        / "migrations"
        / "007_issue_routing_and_public_projection.sql"
    ).read_text(encoding="utf-8").lower()

    assert "add column if not exists area" in sql
    assert "add column if not exists issue_url" in sql
    assert "publishing_issue" in sql
    assert "issue_opened" in sql
    assert "security_invoker = true" in sql
    assert "r.issue_url" in sql
    assert "grant select on public.agent_run_public to service_role" in sql
    assert "'{classification,area}'" in sql
    assert "'{classification,issue_title}'" not in sql
    assert "'{classification,issue_summary}'" not in sql
    assert "update public.feedback" not in sql
    assert "delete from" not in sql


def test_stage_j_migration_adds_private_failure_and_public_whitelist():
    sql = (
        Path(__file__).parents[1] / "migrations" / "008_failure_handling.sql"
    ).read_text(encoding="utf-8").lower()

    assert "add column if not exists failure jsonb" in sql
    assert "agent_runs_failure_shape_check" in sql
    assert "failure ?& array[" in sql
    assert "status in ('failed', 'security_rejected', 'budget_exhausted')" in sql
    assert "security_invoker = true" in sql
    assert "'handling', r.failure -> 'handling'" in sql
    assert "'safe_details', r.failure" not in sql
    assert "grant select on public.agent_run_public to service_role" in sql
    assert "update public.agent_runs" not in sql
    assert "delete from" not in sql
