"""为修复 Agent 构造两个 OpenAI-compatible ChatModel。"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agent.config import AgentConfig
from agent.domain.errors import ConfigurationError


@dataclass(frozen=True)
class ChatModelProfile:
    role: str
    model_name: str
    source: str
    max_input_tokens: int
    tool_calling: bool | None


@dataclass(frozen=True)
class ChatModelBundle:
    """修复模型集合；Summary 使用备用模型，不引入第三套 API。"""

    primary: BaseChatModel
    fallback: BaseChatModel
    summary: BaseChatModel
    primary_profile: ChatModelProfile
    fallback_profile: ChatModelProfile
    effective_context_window: int
    primary_input_cost_per_million: Decimal
    primary_output_cost_per_million: Decimal
    fallback_input_cost_per_million: Decimal
    fallback_output_cost_per_million: Decimal


def build_chat_model_bundle(config: AgentConfig) -> ChatModelBundle:
    """加载已配置模型，并拒绝无法计算比例上下文预算的未知 Profile。"""

    primary_name, primary_key, primary_url = config.require_model_settings()
    fallback_settings = config.fallback_model_settings()
    if fallback_settings is None:
        raise ConfigurationError(
            "FALLBACK_MODEL_ENABLED=true is required for the repair agent"
        )
    fallback_name, fallback_key, fallback_url = fallback_settings

    primary, primary_profile = _build_model(
        role="primary",
        model_name=primary_name,
        api_key=primary_key,
        base_url=primary_url,
        timeout_seconds=config.reproduction_model_timeout_seconds,
        configured_context_window=config.model_context_window,
    )
    fallback, fallback_profile = _build_model(
        role="fallback",
        model_name=fallback_name,
        api_key=fallback_key,
        base_url=fallback_url,
        timeout_seconds=config.reproduction_model_timeout_seconds,
        configured_context_window=config.fallback_model_context_window,
    )
    # 任一模型都可能接管下一次调用，按较小窗口管理上下文才不会在 failover 时溢出。
    effective_window = min(
        primary_profile.max_input_tokens,
        fallback_profile.max_input_tokens,
    )
    return ChatModelBundle(
        primary=primary,
        fallback=fallback,
        summary=fallback,
        primary_profile=primary_profile,
        fallback_profile=fallback_profile,
        effective_context_window=effective_window,
        primary_input_cost_per_million=config.model_input_cost_per_million,
        primary_output_cost_per_million=config.model_output_cost_per_million,
        fallback_input_cost_per_million=config.fallback_model_input_cost_per_million,
        fallback_output_cost_per_million=config.fallback_model_output_cost_per_million,
    )


def _build_model(
    *,
    role: str,
    model_name: str,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    configured_context_window: int | None,
) -> tuple[BaseChatModel, ChatModelProfile]:
    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        max_retries=0,
        stream_usage=True,
        use_responses_api=False,
    )
    existing_profile = dict(model.profile or {})
    profile_source = "langchain"
    if configured_context_window is not None:
        existing_profile["max_input_tokens"] = configured_context_window
        # 自定义 OpenAI-compatible 模型通常不在 models.dev；真实 smoke 仍会验证工具协议。
        existing_profile.setdefault("tool_calling", True)
        profile_source = "configured"
        model = model.model_copy(update={"profile": existing_profile})

    max_input_tokens = _positive_int(existing_profile.get("max_input_tokens"))
    if max_input_tokens is None:
        variable = (
            "MODEL_CONTEXT_WINDOW"
            if role == "primary"
            else "FALLBACK_MODEL_CONTEXT_WINDOW"
        )
        raise ConfigurationError(
            f"model profile is missing max_input_tokens; configure {variable}"
        )
    tool_calling = existing_profile.get("tool_calling")
    return model, ChatModelProfile(
        role=role,
        model_name=model_name,
        source=profile_source,
        max_input_tokens=max_input_tokens,
        tool_calling=tool_calling if isinstance(tool_calling, bool) else None,
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None
