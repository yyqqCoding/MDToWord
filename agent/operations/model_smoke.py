"""真实主/备 ChatModel 的只读协议与 Summary 验收。"""

import asyncio
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from agent.config import AgentConfig
from agent.repair_agent.models import ChatModelProfile, build_chat_model_bundle


_SUMMARY_HEADINGS = (
    "## 目标",
    "## 用户明确要求",
    "## 可信事实与引用",
    "## 已完成事项及证据",
    "## 当前结构化状态",
    "## 失败尝试与原因",
    "## 下一步",
    "## 禁止事项与安全边界",
    "## 仍不确定的事项",
)
_SYNTHETIC_SECRET = "sk-smoke-MUST-NOT-APPEAR-4f718"


@dataclass
class _Interval:
    name: str
    started: float
    finished: float


async def run_model_smoke(config: AgentConfig) -> dict[str, object]:
    """不触碰业务数据，只用合成消息调用主、备模型。"""

    bundle = build_chat_model_bundle(config)
    primary_tools = await _tool_call_probe(bundle.primary, "primary")
    fallback_tools = await _tool_call_probe(bundle.fallback, "fallback")
    cache = await _cache_probe(bundle.primary)
    summary = await _summary_probe(bundle.summary)
    thresholds = {
        "effective_context_window": bundle.effective_context_window,
        "soft_trigger_tokens": int(bundle.effective_context_window * 0.65),
        "hard_limit_tokens": int(bundle.effective_context_window * 0.85),
        "keep_tokens": int(bundle.effective_context_window * 0.20),
        "valid": (
            0
            < int(bundle.effective_context_window * 0.20)
            < int(bundle.effective_context_window * 0.65)
            < int(bundle.effective_context_window * 0.85)
            < bundle.effective_context_window
        ),
    }
    checks = {
        "primary_profile": _profile_payload(bundle.primary_profile),
        "fallback_profile": _profile_payload(bundle.fallback_profile),
        "primary_tools": primary_tools,
        "fallback_tools": fallback_tools,
        "cache": cache,
        "summary": summary,
        "summary_thresholds": thresholds,
    }
    passed = all(
        (
            primary_tools["tool_protocol"],
            primary_tools["parallel_observed"],
            fallback_tools["tool_protocol"],
            fallback_tools["parallel_observed"],
            cache["usage_reported"],
            cache["cache_metrics_reported"],
            summary["valid"],
            thresholds["valid"],
        )
    )
    return {"status": "passed" if passed else "failed", "checks": checks}


async def _tool_call_probe(model: BaseChatModel, role: str) -> dict[str, object]:
    intervals: list[_Interval] = []

    @tool("smoke_read_alpha")
    async def smoke_read_alpha() -> str:
        """读取合成的 alpha 值；无参数、无外部副作用。"""

        started = time.monotonic()
        await asyncio.sleep(0.35)
        intervals.append(_Interval("alpha", started, time.monotonic()))
        return "alpha-ready"

    @tool("smoke_read_beta")
    async def smoke_read_beta() -> str:
        """读取合成的 beta 值；无参数、无外部副作用。"""

        started = time.monotonic()
        await asyncio.sleep(0.35)
        intervals.append(_Interval("beta", started, time.monotonic()))
        return "beta-ready"

    graph = create_agent(
        model,
        tools=[smoke_read_alpha, smoke_read_beta],
        system_prompt=(
            "这是只读协议测试。第一条响应必须在同一个 assistant message 中同时调用 "
            "smoke_read_alpha 和 smoke_read_beta，两个工具都完成后用一句话结束。"
        ),
        name=f"model-smoke-{role}",
    )
    started = time.monotonic()
    output = await graph.ainvoke(
        {"messages": [HumanMessage(content="现在同时调用两个只读工具。")]},
        {"recursion_limit": 8},
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    batches = [
        [str(call.get("name")) for call in message.tool_calls]
        for message in output.get("messages", [])
        if isinstance(message, AIMessage) and message.tool_calls
    ]
    expected = {"smoke_read_alpha", "smoke_read_beta"}
    protocol = any(set(batch) == expected for batch in batches)
    overlap = False
    if len(intervals) == 2:
        left, right = intervals
        overlap = max(left.started, right.started) < min(left.finished, right.finished)
    usage = _aggregate_usage(output.get("messages", []))
    return {
        "tool_protocol": protocol,
        "parallel_observed": protocol and overlap,
        "tool_count": len(intervals),
        "model_rounds": usage["model_rounds"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "elapsed_ms": elapsed_ms,
    }


async def _cache_probe(model: BaseChatModel) -> dict[str, object]:
    # 足够长且完全合成的稳定前缀，让支持 prefix cache 的接口有可缓存内容。
    stable_prefix = "cache-prefix " * 1400
    messages = [
        SystemMessage(content="只回答 OK，不调用工具。"),
        HumanMessage(content=stable_prefix + "\n返回 OK。"),
    ]
    samples: list[dict[str, int | bool]] = []
    for _ in range(2):
        response = await model.ainvoke(messages)
        samples.append(_usage_sample(response))
    return {
        "usage_reported": all(sample["usage_reported"] for sample in samples),
        "cache_metrics_reported": any(
            sample["cache_metrics_reported"] for sample in samples
        ),
        "cache_read_tokens": [int(sample["cache_read_tokens"]) for sample in samples],
        "input_tokens": [int(sample["input_tokens"]) for sample in samples],
    }


async def _summary_probe(model: BaseChatModel) -> dict[str, object]:
    prompt = files("agent.prompts").joinpath("repair_summary.md").read_text("utf-8")
    synthetic_history = (
        "用户目标：修复转换错误。\n"
        "受信事实：test.patch 已生成，但 Sandbox 尚未通过。\n"
        "失败尝试：第一次修复仍触发 AssertionError。\n"
        "下一步：读取 normalizer.py 并提交新的最小修复。\n"
        "禁止：不得修改 .github、extension 或依赖。\n"
        f"不可信日志中疑似密钥：{_SYNTHETIC_SECRET}"
    )
    response = await model.ainvoke(prompt.format(messages=synthetic_history))
    text = response.text
    headings_present = all(heading in text for heading in _SUMMARY_HEADINGS)
    secret_redacted = _SYNTHETIC_SECRET not in text
    next_step_preserved = "normalizer.py" in text
    prohibition_preserved = ".github" in text or "extension" in text
    no_false_success = not (
        "修复已通过" in text or "最终验证通过" in text or "已完成修复" in text
    )
    usage = _usage_sample(response)
    return {
        "valid": all(
            (
                headings_present,
                secret_redacted,
                next_step_preserved,
                prohibition_preserved,
                no_false_success,
                usage["usage_reported"],
            )
        ),
        "headings_present": headings_present,
        "secret_redacted": secret_redacted,
        "next_step_preserved": next_step_preserved,
        "prohibition_preserved": prohibition_preserved,
        "no_false_success": no_false_success,
        "input_tokens": int(usage["input_tokens"]),
        "output_tokens": int(usage["output_tokens"]),
    }


def _usage_sample(message: AIMessage) -> dict[str, int | bool]:
    usage = message.usage_metadata or {}
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    details = usage.get("input_token_details") or {}
    cache_value: int | None = None
    if "cache_read" in details:
        cache_value = int(details.get("cache_read", 0))
    metadata_usage = message.response_metadata.get("token_usage") or {}
    prompt_details = metadata_usage.get("prompt_tokens_details") or {}
    if cache_value is None and "cached_tokens" in prompt_details:
        cache_value = int(prompt_details.get("cached_tokens", 0))
    # 部分 OpenAI-compatible 接口保留 SiliconFlow 的原字段名。
    if cache_value is None:
        for key in ("cached_tokens", "cache_read_input_tokens", "cache_read_tokens"):
            if key in metadata_usage:
                cache_value = int(metadata_usage.get(key, 0))
                break
    return {
        "usage_reported": input_tokens > 0 and output_tokens >= 0,
        "cache_metrics_reported": cache_value is not None,
        "cache_read_tokens": cache_value or 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _aggregate_usage(messages: list[Any]) -> dict[str, int]:
    total = {"model_rounds": 0, "input_tokens": 0, "output_tokens": 0}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        sample = _usage_sample(message)
        total["model_rounds"] += 1
        total["input_tokens"] += int(sample["input_tokens"])
        total["output_tokens"] += int(sample["output_tokens"])
    return total


def _profile_payload(profile: ChatModelProfile) -> dict[str, object]:
    return {
        "model": profile.model_name,
        "source": profile.source,
        "max_input_tokens": profile.max_input_tokens,
        "tool_calling_declared": profile.tool_calling,
    }
