from decimal import Decimal
from pathlib import Path

import pytest

from agent.config import AgentConfig
from agent.domain.errors import ConfigurationError


def test_config_reports_missing_names_without_secret_values(tmp_path: Path):
    with pytest.raises(ConfigurationError) as exc_info:
        AgentConfig.from_env({"MODEL_API_KEY": "do-not-print"}, project_root=tmp_path)

    message = str(exc_info.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_AGENT_KEY" in message
    assert "do-not-print" not in message


def test_config_builds_paths_from_project_root(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
        },
        project_root=tmp_path,
    )

    assert config.artifact_root == tmp_path / "var" / "agent-artifacts"
    assert config.extension_manifest_path == tmp_path / "extension" / "dist" / "manifest.json"
    assert config.supabase_agent_key.get_secret_value() == "secret"


def test_relative_config_paths_are_rooted_at_project(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
            "ARTIFACT_ROOT": "runtime/artifacts",
            "EXTENSION_MANIFEST_PATH": "release/manifest.json",
        },
        project_root=tmp_path,
    )

    assert config.artifact_root == tmp_path / "runtime" / "artifacts"
    assert config.extension_manifest_path == tmp_path / "release" / "manifest.json"


def test_gate_confidence_can_be_configured(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
            "MIN_GATE_CONFIDENCE": "0.9",
        },
        project_root=tmp_path,
    )

    assert config.min_gate_confidence == 0.9


def test_gate_confidence_rejects_out_of_range_value(tmp_path: Path):
    with pytest.raises(ValueError):
        AgentConfig.from_env(
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_AGENT_KEY": "secret",
                "MIN_GATE_CONFIDENCE": "1.1",
            },
            project_root=tmp_path,
        )


def test_checkpoint_database_url_is_optional_until_runtime_needs_it(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
        },
        project_root=tmp_path,
    )

    with pytest.raises(ConfigurationError) as exc_info:
        config.require_database_url()

    assert "AGENT_DATABASE_URL" in str(exc_info.value)
    assert config.checkpoint_schema == "agent_runtime"


def test_checkpoint_database_url_is_secret_and_schema_is_validated(tmp_path: Path):
    database_url = "postgresql://agent:db-secret@example.com/postgres"
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
            "AGENT_DATABASE_URL": database_url,
            "AGENT_CHECKPOINT_SCHEMA": "agent_runtime",
        },
        project_root=tmp_path,
    )

    assert config.require_database_url() == database_url
    assert "db-secret" not in repr(config)

    with pytest.raises(ValueError):
        AgentConfig.from_env(
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_AGENT_KEY": "secret",
                "AGENT_CHECKPOINT_SCHEMA": "public; drop schema public",
            },
            project_root=tmp_path,
        )


def test_real_provider_and_langfuse_settings_are_optional_until_requested(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "secret",
        },
        project_root=tmp_path,
    )

    with pytest.raises(ConfigurationError) as model_error:
        config.require_model_settings()
    with pytest.raises(ConfigurationError) as langfuse_error:
        config.require_langfuse_settings()

    assert "MODEL_NAME" in str(model_error.value)
    assert "LANGFUSE_PUBLIC_KEY" in str(langfuse_error.value)


def test_real_provider_and_langfuse_secrets_are_never_in_repr(tmp_path: Path):
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "supabase-secret",
            "MODEL_PROVIDER": "openai_compatible",
            "MODEL_NAME": "compatible-model",
            "MODEL_API_KEY": "model-secret",
            "MODEL_BASE_URL": "https://models.example/v1",
            "MODEL_INPUT_COST_PER_MILLION": "1.25",
            "MODEL_OUTPUT_COST_PER_MILLION": "5",
            "LANGFUSE_HOST": "https://cloud.langfuse.com",
            "LANGFUSE_PUBLIC_KEY": "langfuse-public",
            "LANGFUSE_SECRET_KEY": "langfuse-secret",
            "AGENT_ENVIRONMENT": "staging",
            "TRACE_CONTENT": "false",
        },
        project_root=tmp_path,
    )

    assert config.require_model_settings()[0] == "compatible-model"
    assert config.require_langfuse_settings()[0] == "https://cloud.langfuse.com"
    assert config.model_input_cost_per_million == Decimal("1.25")
    assert config.trace_content is False
    rendered = repr(config)
    assert "model-secret" not in rendered
    assert "langfuse-secret" not in rendered
