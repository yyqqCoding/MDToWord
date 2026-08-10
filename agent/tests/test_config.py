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
