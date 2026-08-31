import asyncio

import httpx
import pytest
from types import SimpleNamespace
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai.chat_models.base import OpenAIAPIError

from agent.domain.errors import (
    ModelAuthError,
    ModelContextTooLargeError,
    ModelProviderError,
    ModelTimeoutError,
    SourceAccessError,
    SourceRequestError,
    ToolAuthorizationError,
)
from agent.domain.errors import SandboxUnavailableError
from agent.domain.failures import FailureRecorder
from agent.repair_agent.middleware import (
    CompletionGuardMiddleware,
    HardContextLimitMiddleware,
    ModelResilienceMiddleware,
    ParallelToolPolicyMiddleware,
    RepairSummarizationMiddleware,
    RecordingToolRetryMiddleware,
    _allowed_tool_names,
    safe_tool_error,
)


def _request(primary: FakeListChatModel) -> ModelRequest:
    return ModelRequest(
        model=primary,
        messages=[HumanMessage(content="synthetic")],
        tools=[],
        state={"messages": []},
    )


def test_model_resilience_uses_primary_primary_fallback_with_1_2_backoff():
    async def scenario():
        primary = FakeListChatModel(responses=["unused"])
        fallback = FakeListChatModel(responses=["unused"])
        sleeps: list[float] = []
        calls: list[object] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            calls.append(request.model)
            if len(calls) < 3:
                raise ModelTimeoutError("temporary")
            return ModelResponse(result=[AIMessage(content="ok")])

        middleware = ModelResilienceMiddleware(
            fallback,
            sleep=lambda delay: _record_sleep(sleeps, delay),
        )
        response = await middleware.awrap_model_call(_request(primary), handler)
        return primary, fallback, calls, sleeps, response

    primary, fallback, calls, sleeps, response = asyncio.run(scenario())

    assert calls == [primary, primary, fallback]
    assert sleeps == [1.0, 2.0]
    assert response.result[0].content == "ok"


def test_model_resilience_does_not_retry_permanent_error():
    async def scenario():
        primary = FakeListChatModel(responses=["unused"])
        fallback = FakeListChatModel(responses=["unused"])
        calls = 0

        async def handler(request: ModelRequest) -> ModelResponse:
            nonlocal calls
            calls += 1
            raise ModelAuthError("permanent")

        middleware = ModelResilienceMiddleware(fallback)
        with pytest.raises(ModelAuthError):
            await middleware.awrap_model_call(_request(primary), handler)
        return calls

    assert asyncio.run(scenario()) == 1


def test_model_resilience_maps_all_upstream_5xx_and_uses_fallback():
    async def scenario():
        primary = FakeListChatModel(responses=["unused"])
        fallback = FakeListChatModel(responses=["unused"])
        calls: list[object] = []
        sleeps: list[float] = []

        async def handler(request: ModelRequest) -> ModelResponse:
            calls.append(request.model)
            response = httpx.Response(
                520,
                request=httpx.Request(
                    "POST",
                    "https://provider.example/v1/chat/completions",
                ),
            )
            raise OpenAIAPIError(
                message="upstream failed",
                response=response,
                body=None,
            )

        middleware = ModelResilienceMiddleware(
            fallback,
            sleep=lambda delay: _record_sleep(sleeps, delay),
        )
        with pytest.raises(ModelProviderError) as raised:
            await middleware.awrap_model_call(_request(primary), handler)
        return primary, fallback, calls, sleeps, raised.value

    primary, fallback, calls, sleeps, error = asyncio.run(scenario())

    assert calls == [primary, primary, fallback]
    assert sleeps == [1.0, 2.0]
    assert error.attempt == 3
    assert error.max_attempts == 3
    assert error.safe_details == {"http_status": 520}


def test_parallel_policy_rejects_two_sandboxes_before_execution():
    state = {
        "phase": "repairing",
        "fix_patch_ref": "run://synthetic/fix.patch",
        "repair_result_ref": None,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "run_sandbox", "args": {"reason": "a"}, "id": "a"},
                    {"name": "run_sandbox", "args": {"reason": "b"}, "id": "b"},
                ],
            )
        ],
    }

    update = asyncio.run(ParallelToolPolicyMiddleware().aafter_model(state, None))

    assert update is not None
    assert update["jump_to"] == "model"
    assert len(update["messages"]) == 2
    assert all(isinstance(item, ToolMessage) for item in update["messages"])
    assert all(item.status == "error" for item in update["messages"])


def test_repair_tool_visibility_follows_trusted_substate():
    editing = {"phase": "repairing"}
    pending_sandbox = {
        "phase": "repairing",
        "fix_patch_ref": "run://synthetic/fix.patch",
        "repair_result_ref": None,
    }
    failed_sandbox = {
        **pending_sandbox,
        "repair_result_ref": "run://synthetic/repair.json",
        "repair_confirmed": False,
    }
    confirmed = {**failed_sandbox, "repair_confirmed": True}

    assert "submit_fix_edits" in _allowed_tool_names(editing)
    assert "run_sandbox" not in _allowed_tool_names(editing)
    assert _allowed_tool_names(pending_sandbox) == frozenset(
        {"run_sandbox", "report_blocked"}
    )
    assert "submit_fix_edits" in _allowed_tool_names(failed_sandbox)
    assert "run_sandbox" not in _allowed_tool_names(failed_sandbox)
    assert _allowed_tool_names(confirmed) == frozenset(
        {"complete_repair", "report_blocked"}
    )


def test_premature_sandbox_call_returns_actionable_tool_message():
    state = {
        "phase": "repairing",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "run_sandbox", "args": {"reason": "early"}, "id": "a"}
                ],
            )
        ],
    }

    update = asyncio.run(ParallelToolPolicyMiddleware().aafter_model(state, None))

    assert update is not None and update["jump_to"] == "model"
    message = update["messages"][0]
    assert message.status == "error"
    assert '"error_code": "tool_precondition_failed"' in message.content
    assert '"required_action": "submit_fix_edits"' in message.content


def test_cross_phase_tool_call_remains_security_error():
    state = {
        "phase": "repairing",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "submit_test_edits", "args": {}, "id": "a"}
                ],
            )
        ],
    }

    with pytest.raises(ToolAuthorizationError):
        asyncio.run(ParallelToolPolicyMiddleware().aafter_model(state, None))


def test_tool_precondition_error_preserves_required_action_for_model():
    from agent.domain.errors import ToolPreconditionError

    message = safe_tool_error(
        ToolPreconditionError(
            "submit_fix_edits must succeed before run_sandbox",
            safe_details={"required_action": "submit_fix_edits"},
        ),
        None,
    )

    assert message is not None
    assert '"error_code": "tool_precondition_failed"' in message
    assert '"required_action": "submit_fix_edits"' in message


def test_source_request_error_returns_safe_correction_to_model():
    message = safe_tool_error(
        SourceRequestError(
            "start_line is after the end of the file",
            safe_details={
                "reason": "line_after_eof",
                "path": "backend/app/normalizer.py",
                "start_line": 500,
                "total_lines": 120,
            },
        ),
        None,
    )

    assert message is not None
    assert '"error_code": "source_request_invalid"' in message
    assert '"required_action": "correct_source_request"' in message
    assert '"path": "backend/app/normalizer.py"' in message


def test_source_access_error_is_not_returned_to_model():
    assert (
        safe_tool_error(
            SourceAccessError(
                "source path is outside the read allowlist",
                safe_details={"reason": "outside_allowlist"},
            ),
            None,
        )
        is None
    )


def test_sandbox_tool_retry_is_three_total_attempts_with_1_2_backoff():
    async def scenario():
        calls = 0
        sleeps: list[float] = []

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise SandboxUnavailableError("temporary")
            return ToolMessage(content="ok", tool_call_id="call")

        middleware = RecordingToolRetryMiddleware(
            FailureRecorder(),
            sleep=lambda delay: _record_sleep(sleeps, delay),
        )
        result = await middleware.awrap_tool_call(
            SimpleNamespace(
                tool_call={"name": "run_sandbox", "id": "call"},
                state={"phase": "repairing"},
            ),
            handler,
        )
        return calls, sleeps, result

    calls, sleeps, result = asyncio.run(scenario())

    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert result.content == "ok"


def test_parallel_policy_allows_multiple_read_only_tools():
    state = {
        "phase": "repairing",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_source_file", "args": {}, "id": "a"},
                    {"name": "search_source", "args": {}, "id": "b"},
                ],
            )
        ],
    }

    assert (
        asyncio.run(ParallelToolPolicyMiddleware().aafter_model(state, None)) is None
    )


def test_completion_guard_requires_explicit_completion_tool():
    state = {
        "phase": "repairing",
        "terminal": None,
        "premature_final_count": 0,
        "messages": [AIMessage(content="I am done")],
    }

    update = asyncio.run(CompletionGuardMiddleware().aafter_model(state, None))

    assert update is not None
    assert update["jump_to"] == "model"
    assert update["premature_final_count"] == 1


def test_summary_uses_fractional_profile_and_hard_limit_fails_closed():
    model = FakeListChatModel(responses=["summary"]).model_copy(
        update={"profile": {"max_input_tokens": 1000}}
    )
    summarizer = RepairSummarizationMiddleware(
        model,
        effective_context_window=1000,
    )
    summarizer.token_counter = lambda messages: 850
    hard_limit = HardContextLimitMiddleware(summarizer)

    with pytest.raises(ModelContextTooLargeError):
        asyncio.run(
            hard_limit.abefore_model(
                {"messages": [HumanMessage(content="synthetic")]},
                None,
            )
        )

    assert summarizer.trigger == ("fraction", 0.65)
    assert summarizer.keep == ("fraction", 0.20)
    assert summarizer.trim_tokens_to_summarize is None


async def _record_sleep(target: list[float], delay: float) -> None:
    target.append(delay)
