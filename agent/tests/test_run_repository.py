import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from agent.domain.enums import AgentRunStatus, GateCategory, GateRoute
from agent.domain.gate import GateResult
from agent.domain.models import AgentRunRecord
from agent.repositories.fake import FakeAgentRunRepository
from agent.repositories.supabase import SupabaseAgentRunRepository


def make_run(*, status: AgentRunStatus = AgentRunStatus.CREATED) -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid4(),
        feedback_id=uuid4(),
        claim_token=uuid4(),
        trace_id="trace-123",
        status=status,
        graph_version="gate-graph-v1",
        prompt_versions={"gate": "gate-v1"},
        policy_version="gate-policy-v1",
        task_artifact_ref="artifact://run/task.redacted.json",
        artifact_path="artifact://run",
    )


def test_fake_run_repository_returns_oldest_resumable_run():
    async def scenario():
        repository = FakeAgentRunRepository()
        first = make_run(status=AgentRunStatus.GATING)
        second = make_run(status=AgentRunStatus.CREATED).model_copy(
            update={"started_at": first.started_at + timedelta(seconds=1)}
        )
        await repository.create(first)
        await repository.create(second)
        return first, await repository.find_resumable()

    first, resumable = asyncio.run(scenario())

    assert resumable is not None
    assert resumable.id == first.id


def test_supabase_run_completion_writes_only_gate_summary():
    run = make_run(status=AgentRunStatus.GATING)
    gate_result = GateResult(
        route=GateRoute.NEEDS_HUMAN,
        category=GateCategory.UNKNOWN,
        policy_reason="confidence_below_threshold",
        model_calls=1,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/agent_runs"
        if request.method == "GET":
            return httpx.Response(200, json=[run.model_dump(mode="json")])
        assert request.method == "PATCH"
        payload = json.loads(request.content)
        assert payload["classification"]["route"] == "needs_human"
        assert "markdown_content" not in json.dumps(payload)
        completed = run.model_copy(
            update={
                "status": AgentRunStatus.COMPLETED,
                "route": GateRoute.NEEDS_HUMAN,
                "category": GateCategory.UNKNOWN,
                "classification": gate_result,
                "finished_at": datetime.now(UTC),
                "model_calls": 1,
            }
        )
        return httpx.Response(200, json=[completed.model_dump(mode="json")])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseAgentRunRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.complete_gate(run.id, gate_result)

    completed = asyncio.run(scenario())

    assert completed.status is AgentRunStatus.COMPLETED
    assert completed.route is GateRoute.NEEDS_HUMAN


def test_supabase_run_completion_is_idempotent_after_database_write():
    gate_result = GateResult(
        route=GateRoute.NEEDS_HUMAN,
        policy_reason="confidence_below_threshold",
        model_calls=1,
    )
    completed = make_run(status=AgentRunStatus.COMPLETED).model_copy(
        update={
            "route": GateRoute.NEEDS_HUMAN,
            "classification": gate_result,
            "finished_at": datetime.now(UTC),
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=[completed.model_dump(mode="json")])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseAgentRunRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.complete_gate(completed.id, gate_result)

    result = asyncio.run(scenario())

    assert result.id == completed.id
    assert result.status is AgentRunStatus.COMPLETED
