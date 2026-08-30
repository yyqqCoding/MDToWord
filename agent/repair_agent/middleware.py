"""Repair Agent 的重试、阶段授权、并行与上下文 Middleware。"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from importlib.resources import files
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    SummarizationMiddleware,
    ToolRetryMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ConfigDict

from agent.domain.errors import (
    InvalidModelResponseError,
    ModelAuthError,
    ModelContextTooLargeError,
    ModelProviderError,
    ModelRateLimitError,
    ModelSafetyRefusalError,
    ModelTimeoutError,
    SandboxUnavailableError,
)
from agent.domain.failures import (
    FailureEvent,
    FailureHandling,
    FailureRecorder,
    LocatedFailure,
    failure_cause_from_exception,
)
from agent.providers.base import StructuredModelResponse
from agent.repair_agent.state import RepairAgentState
from agent.telemetry.base import (
    GenerationTrace,
    NoopTelemetry,
    Telemetry,
    ToolTrace,
)


READ_ONLY_TOOLS = frozenset({"read_source_file", "search_source"})
SANDBOX_TOOLS = frozenset({"run_sandbox"})
PATCH_TOOLS = frozenset({"submit_test_edits", "submit_fix_edits"})
TERMINAL_TOOLS = frozenset(
    {"complete_reproduction", "complete_repair", "report_blocked", "write_todos"}
)


class ModelResilienceMiddleware(AgentMiddleware):
    """一个模型轮次严格执行主、主、备三个总 attempt。"""

    def __init__(
        self,
        fallback_model: Any,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        failure_recorder: FailureRecorder | None = None,
    ) -> None:
        self.fallback_model = fallback_model
        self.sleep = sleep
        self.failure_recorder = failure_recorder or FailureRecorder()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        last_error: Exception | None = None
        for index, model in enumerate((request.model, request.model, self.fallback_model)):
            try:
                selected = request if model is request.model else request.override(model=model)
                return await handler(selected)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                mapped = _model_error(exc, attempt=index + 1)
                if mapped is None:
                    raise
                last_error = mapped
                retrying = index < 2 and _is_transient_model_error(mapped)
                delay = (1.0 if index == 0 else 2.0) if retrying else None
                self._record_model_failure(
                    request,
                    mapped,
                    handling=(
                        FailureHandling.TRANSPORT_RETRY
                        if retrying
                        else FailureHandling.STOP
                    ),
                    delay_seconds=delay,
                )
                if not retrying:
                    raise mapped from exc
                assert delay is not None
                await self.sleep(delay)
        assert last_error is not None
        raise last_error

    def _record_model_failure(
        self,
        request: ModelRequest,
        error: Exception,
        *,
        handling: FailureHandling,
        delay_seconds: float | None,
    ) -> None:
        phase = str(request.state.get("phase", "repairing"))
        self.failure_recorder.record(
            FailureEvent(
                failure=LocatedFailure(
                    cause=failure_cause_from_exception(
                        error,
                        operation="repair_model",
                    ),
                    phase=phase,
                    node="repair_agent",
                ),
                attempt=int(getattr(error, "attempt", 1)),
                max_attempts=3,
                handling=handling,
                delay_seconds=delay_seconds,
            )
        )


class RecordingToolRetryMiddleware(ToolRetryMiddleware):
    """沿用官方配置契约，并补齐每个 Sandbox attempt 的 FailureEvent。"""

    def __init__(
        self,
        failure_recorder: FailureRecorder,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(
            max_retries=2,
            tools=["run_sandbox"],
            retry_on=(SandboxUnavailableError,),
            on_failure="error",
            initial_delay=1.0,
            backoff_factor=2.0,
            max_delay=2.0,
            jitter=False,
        )
        self.failure_recorder = failure_recorder
        self.sleep = sleep

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if request.tool_call["name"] != "run_sandbox":
            return await handler(request)
        for index in range(3):
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise
            except SandboxUnavailableError as exc:
                attempt = index + 1
                exc.attempt = attempt
                exc.max_attempts = 3
                retrying = attempt < 3
                delay = float(2**index) if retrying else None
                phase = str(request.state.get("phase", "repairing"))
                self.failure_recorder.record(
                    FailureEvent(
                        failure=LocatedFailure(
                            cause=failure_cause_from_exception(
                                exc,
                                operation="run_sandbox",
                            ),
                            phase=phase,
                            node="repair_agent",
                        ),
                        attempt=attempt,
                        max_attempts=3,
                        handling=(
                            FailureHandling.TRANSPORT_RETRY
                            if retrying
                            else FailureHandling.STOP
                        ),
                        delay_seconds=delay,
                    )
                )
                if not retrying:
                    raise
                assert delay is not None
                await self.sleep(delay)


class PhaseToolPolicyMiddleware(AgentMiddleware):
    """只把当前阶段允许的工具 Schema 发给模型。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        state = request.state
        allowed = _allowed_tool_names(state)
        tools = [tool for tool in request.tools if _tool_name(tool) in allowed]
        return await handler(request.override(tools=tools))


class _ObservedAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_calls: tuple[str, ...]
    content_bytes: int


class RepairTelemetryMiddleware(AgentMiddleware):
    """把 create_agent 调用投影到既有脱敏 Langfuse Telemetry。"""

    def __init__(self, telemetry: Telemetry | None = None) -> None:
        self.telemetry = telemetry or NoopTelemetry()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model_name = str(
            getattr(request.model, "model_name", None)
            or getattr(request.model, "model", None)
            or "openai-compatible"
        )
        phase = str(request.state.get("phase", "repairing"))
        with self.telemetry.start_generation(
            GenerationTrace(
                operation="repair_agent_model",
                prompt_version="repair-agent-v1",
                provider="openai_compatible",
                model=model_name,
                input_summary={
                    "phase": phase,
                    "message_count": len(request.messages),
                    "available_tools": sorted(_tool_name(tool) for tool in request.tools),
                },
            )
        ) as observation:
            try:
                response = await handler(request)
            except Exception as exc:
                observation.fail(
                    error_code=getattr(exc, "error_code", "provider_unavailable"),
                    error_type=type(exc).__name__,
                )
                raise
            message = next(
                (item for item in response.result if isinstance(item, AIMessage)),
                None,
            )
            if message is None:
                observation.fail(
                    error_code="invalid_response",
                    error_type="MissingAIMessage",
                )
                return response
            usage = message.usage_metadata or {}
            details = usage.get("input_token_details") or {}
            output = _ObservedAgentOutput(
                tool_calls=tuple(
                    str(call.get("name", "")) for call in message.tool_calls
                ),
                content_bytes=len(message.text.encode("utf-8")),
            )
            observation.succeed(
                StructuredModelResponse(
                    output=output,
                    provider="openai_compatible",
                    model=model_name,
                    provider_request_id=str(message.id or "unavailable")[:200],
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    cached_input_tokens=int(details.get("cache_read", 0)),
                    reasoning_tokens=int(
                        (usage.get("output_token_details") or {}).get("reasoning", 0)
                    ),
                    total_tokens=int(usage.get("total_tokens", 0)),
                    estimated_cost=Decimal("0"),
                    tool_calls=output.tool_calls,
                )
            )
            return response

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        name = str(request.tool_call.get("name", "unknown"))
        round_number = request.state.get(
            "reproduction_round"
            if request.state.get("phase") == "reproducing"
            else "repair_round"
        )
        with self.telemetry.start_tool(
            ToolTrace(
                operation=name.replace("_", "-"),
                round=int(round_number) if isinstance(round_number, int) else None,
                input_summary={
                    "phase": str(request.state.get("phase", "unknown")),
                    "argument_names": sorted(
                        str(key) for key in request.tool_call.get("args", {})
                    ),
                },
            )
        ) as observation:
            try:
                result = await handler(request)
            except Exception as exc:
                observation.fail(
                    error_code=getattr(exc, "error_code", "tool_error"),
                    error_type=type(exc).__name__,
                )
                raise
            status = getattr(result, "status", "success")
            observation.succeed(
                {
                    "status": str(status),
                    "result_type": type(result).__name__,
                }
            )
            return result


class ParallelToolPolicyMiddleware(AgentMiddleware):
    """在 ToolNode 并发执行前拒绝有副作用冲突的一批调用。"""

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: RepairAgentState, runtime: Any) -> dict[str, Any] | None:
        message = _last_ai_message(state)
        if message is None or not message.tool_calls:
            return None
        calls = message.tool_calls
        names = [str(item.get("name", "")) for item in calls]
        allowed = _allowed_tool_names(state)
        reason = _parallel_rejection_reason(names, allowed)
        if reason is None:
            return None
        return {
            "messages": [
                ToolMessage(
                    content=reason,
                    tool_call_id=str(call.get("id") or f"invalid-{index}"),
                    name=str(call.get("name") or "unknown"),
                    status="error",
                )
                for index, call in enumerate(calls)
            ],
            "jump_to": "model",
            "diagnostics": {"parallel_policy": reason},
        }


class CompletionGuardMiddleware(AgentMiddleware):
    """禁止模型用普通文本绕过完成工具。"""

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: RepairAgentState, runtime: Any) -> dict[str, Any] | None:
        if state.get("terminal") is not None:
            return None
        message = _last_ai_message(state)
        if message is None or message.tool_calls:
            return None
        count = int(state.get("premature_final_count", 0)) + 1
        if count > 2:
            raise InvalidModelResponseError(
                "repair agent repeatedly answered without a completion tool"
            )
        return {
            "messages": [
                HumanMessage(
                    content=(
                        "普通文本不能结束任务。请继续使用工具；完成时必须调用当前阶段的"
                        " complete 工具，无法继续则调用 report_blocked。"
                    )
                )
            ],
            "premature_final_count": 1,
            "jump_to": "model",
        }


class UsageAccountingMiddleware(AgentMiddleware):
    """把成功响应 usage 单调累计进 checkpoint，不依赖被 Summary 保留的消息。"""

    async def aafter_model(self, state: RepairAgentState, runtime: Any) -> dict[str, Any] | None:
        message = _last_ai_message(state)
        if message is None:
            return None
        usage = message.usage_metadata or {}
        details = usage.get("input_token_details") or {}
        return {
            "model_calls": 1,
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
            "cache_read_tokens": int(details.get("cache_read", 0)),
        }


class RepairSummarizationMiddleware(SummarizationMiddleware):
    """65% 尝试总结；低于 85% 时允许一次总结故障继续。"""

    def __init__(self, model: Any, *, effective_context_window: int) -> None:
        profile = dict(getattr(model, "profile", None) or {})
        profile["max_input_tokens"] = effective_context_window
        summary_model = model.model_copy(update={"profile": profile})
        summary_prompt = (
            files("agent.prompts").joinpath("repair_summary.md").read_text("utf-8")
        )
        super().__init__(
            summary_model,
            trigger=("fraction", 0.65),
            keep=("fraction", 0.20),
            summary_prompt=summary_prompt,
            trim_tokens_to_summarize=None,
        )
        self.hard_tokens = int(effective_context_window * 0.85)

    async def abefore_model(self, state: RepairAgentState, runtime: Any) -> dict[str, Any] | None:
        total_tokens = self.token_counter(state["messages"])
        try:
            return await super().abefore_model(state, runtime)
        except Exception as exc:
            if total_tokens >= self.hard_tokens:
                error = ModelContextTooLargeError(
                    "summary failed at the hard context limit",
                    safe_details={"context_tokens": total_tokens},
                )
                raise error from exc
            return {
                "summary_failures": 1,
                "diagnostics": {"summary_error_type": type(exc).__name__[:120]},
            }


class HardContextLimitMiddleware(AgentMiddleware):
    """Summary 执行后仍超过 85% 时 fail closed。"""

    def __init__(self, summarizer: RepairSummarizationMiddleware) -> None:
        self.summarizer = summarizer

    async def abefore_model(self, state: RepairAgentState, runtime: Any) -> None:
        tokens = self.summarizer.token_counter(state["messages"])
        if tokens >= self.summarizer.hard_tokens:
            raise ModelContextTooLargeError(
                "repair context remains above the hard limit after summarization",
                safe_details={"context_tokens": tokens},
            )
        return None


def safe_tool_error(exc: Exception, request: Any) -> str | None:
    """只把可操作的本地输入错误返回模型，永久基础设施错误继续上抛。"""

    from agent.domain.errors import InvalidEditError

    if isinstance(
        exc,
        (
            InvalidEditError,
            ValueError,
        ),
    ):
        detail = str(exc).replace("\n", " ")[:600]
        return f"工具请求未通过本地校验：{detail}"
    return None


def _allowed_tool_names(state: RepairAgentState) -> frozenset[str]:
    phase = state.get("phase")
    common = set(READ_ONLY_TOOLS) | {"write_todos", "report_blocked"}
    if phase == "reproducing":
        common |= {"submit_test_edits", "run_sandbox"}
        if state.get("reproduction_confirmed"):
            common.add("complete_reproduction")
    elif phase == "repairing":
        common |= {"submit_fix_edits", "run_sandbox"}
        if state.get("repair_confirmed"):
            common.add("complete_repair")
    return frozenset(common)


def _parallel_rejection_reason(
    names: Sequence[str],
    allowed: frozenset[str],
) -> str | None:
    unknown = sorted(set(names) - allowed)
    if unknown:
        return "当前阶段未授权工具：" + ", ".join(unknown)
    if sum(name in SANDBOX_TOOLS for name in names) > 1:
        return "同一批次最多调用一个 run_sandbox；请分轮执行。"
    exclusive = PATCH_TOOLS | TERMINAL_TOOLS
    if len(names) > 1 and any(name in exclusive for name in names):
        return "patch、完成、阻塞或 Todo 工具必须单独调用，不能与其他工具并行。"
    return None


def _last_ai_message(state: RepairAgentState) -> AIMessage | None:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage):
            return message
    return None


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function") or {}
        return str(tool.get("name") or function.get("name") or "")
    return str(getattr(tool, "name", ""))


def _is_transient_model_error(exc: Exception) -> bool:
    return isinstance(exc, (ModelTimeoutError, ModelRateLimitError, ModelProviderError)) and not isinstance(
        exc,
        (ModelAuthError, ModelContextTooLargeError, ModelSafetyRefusalError),
    )


def _model_error(exc: Exception, *, attempt: int) -> Exception | None:
    """标准化 OpenAI-compatible 异常；未知编程错误不伪装成上游故障。"""

    import openai

    kwargs = {"attempt": attempt, "max_attempts": 3, "operation": "repair_model"}
    if isinstance(exc, ModelProviderError):
        exc.attempt = attempt
        exc.max_attempts = 3
        return exc
    if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException)):
        return ModelTimeoutError("repair model request timed out", **kwargs)
    if isinstance(exc, (openai.APIConnectionError, httpx.NetworkError)):
        return ModelProviderError("repair model connection failed", **kwargs)
    if isinstance(exc, openai.AuthenticationError):
        return ModelAuthError("repair model authentication failed", **kwargs)
    if isinstance(exc, openai.PermissionDeniedError):
        return ModelAuthError("repair model permission denied", **kwargs)
    if isinstance(exc, openai.RateLimitError):
        return ModelRateLimitError("repair model rate limited", **kwargs)
    if isinstance(exc, openai.BadRequestError):
        text = str(exc).lower()
        if "context" in text and any(word in text for word in ("length", "token", "window")):
            return ModelContextTooLargeError("repair model context is too large", **kwargs)
        if "safety" in text or "content policy" in text:
            return ModelSafetyRefusalError("repair model safety refusal", **kwargs)
        return None
    if isinstance(exc, openai.APIStatusError):
        status = int(exc.status_code)
        if status in {408, 429, 500, 502, 503, 504}:
            if status == 429:
                return ModelRateLimitError("repair model rate limited", **kwargs)
            return ModelProviderError(
                "repair model upstream unavailable",
                safe_details={"http_status": status},
                **kwargs,
            )
    return None
