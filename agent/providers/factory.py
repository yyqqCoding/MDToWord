"""Provider 工厂:业务代码创建 Provider 的唯一入口。

MVP 只注册 openai_compatible(阶段 03);Anthropic 原生 Provider 在
阶段 10 增加时,只改本文件与新 Provider 文件,业务代码零改动。
"""

from __future__ import annotations

from agent.config import AgentConfig
from agent.exceptions import ConfigError
from agent.providers.base import ModelProvider
from agent.providers.openai_compatible_provider import OpenAICompatibleProvider

KNOWN_PROVIDERS = ("openai_compatible",)


def create(config: AgentConfig) -> ModelProvider:
    if config.model_provider == "openai_compatible":
        config.require("model_name", "model_api_key", "model_base_url")
        return OpenAICompatibleProvider(
            base_url=config.model_base_url,
            api_key=config.model_api_key.get_secret_value(),
            model=config.model_name,
            timeout_seconds=config.model_timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
        )
    raise ConfigError(
        f"未知 MODEL_PROVIDER: {config.model_provider}(可选: {', '.join(KNOWN_PROVIDERS)})")
