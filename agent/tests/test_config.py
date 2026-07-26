import pytest

from agent.config import AgentConfig
from agent.exceptions import ConfigError


def test_from_env_reads_mapping_without_touching_os_environ():
    config = AgentConfig.from_env({
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "service-role-key",
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_NAME": "deepseek-chat",
        "MAX_REPAIR_ROUNDS": "2",
    })
    assert config.supabase_url == "https://example.supabase.co"
    assert config.supabase_key.get_secret_value() == "service-role-key"
    assert config.model_name == "deepseek-chat"
    assert config.max_repair_rounds == 2


def test_missing_secrets_do_not_fail_until_required():
    config = AgentConfig.from_env({})
    assert config.supabase_url is None
    assert config.model_api_key is None


def test_require_raises_with_explicit_env_names():
    config = AgentConfig.from_env({})
    with pytest.raises(ConfigError) as exc_info:
        config.require("supabase_url", "supabase_key")
    assert "SUPABASE_URL" in exc_info.value.message
    assert "SUPABASE_KEY" in exc_info.value.message


def test_security_policy_thresholds_defaults():
    config = AgentConfig.from_env({})
    assert config.max_changed_files == 5
    assert config.max_added_lines == 300
    assert config.max_deleted_lines == 150
    assert config.max_patch_bytes == 200_000
    assert config.max_repair_rounds == 2
    assert config.min_classification_confidence == 0.75


def test_secret_values_not_exposed_in_repr():
    config = AgentConfig.from_env({"SUPABASE_KEY": "topsecret",
                                   "MODEL_API_KEY": "sk-xyz"})
    assert "topsecret" not in repr(config)
    assert "sk-xyz" not in repr(config)
