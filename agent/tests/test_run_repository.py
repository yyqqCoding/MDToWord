import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx

from agent.domain.enums import AgentRunStatus, GateCategory, GateRoute
from agent.domain.gate import GateResult
from agent.domain.models import AgentRunRecord
from agent.domain.repair import RepairDisposition, RepairReport
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


def test_supabase_repair_dependency_result_persists_needs_human_route():
    run = make_run(status=AgentRunStatus.REPAIRING).model_copy(
        update={"route": GateRoute.ACCEPTED_BACKEND_BUG}
    )
    report = RepairReport(
        disposition=RepairDisposition.NEEDS_HUMAN,
        round=1,
        failure_code="external_dependency_required",
        failure_summary="repair requires a deployment change",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[run.model_dump(mode="json")])
        payload = json.loads(request.content)
        assert payload["status"] == "completed"
        assert payload["route"] == "needs_human"
        assert payload["repair"]["disposition"] == "needs_human"
        completed = run.model_copy(
            update={
                "status": AgentRunStatus.COMPLETED,
                "route": GateRoute.NEEDS_HUMAN,
                "repair": report.model_dump(mode="json"),
                "finished_at": datetime.now(UTC),
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
            return await repository.complete_repair_failure(
                run.id,
                report,
                model_calls=4,
                tool_calls=11,
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                estimated_cost=Decimal("0.012"),
            )

    completed = asyncio.run(scenario())

    assert completed.status is AgentRunStatus.COMPLETED
    assert completed.route is GateRoute.NEEDS_HUMAN


def test_supabase_failure_persists_latest_usage_totals():
    run = make_run(status=AgentRunStatus.REPAIRING)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[run.model_dump(mode="json")])
        payload = json.loads(request.content)
        assert payload["status"] == "failed"
        assert payload["model_calls"] == 4
        assert payload["tool_calls"] == 11
        assert payload["input_tokens"] == 120
        assert payload["output_tokens"] == 30
        assert payload["total_tokens"] == 150
        assert payload["estimated_cost"] == "0.012"
        failed = run.model_copy(
            update={
                "status": AgentRunStatus.FAILED,
                "model_calls": 4,
                "tool_calls": 11,
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "estimated_cost": Decimal("0.012"),
                "finished_at": datetime.now(UTC),
            }
        )
        return httpx.Response(200, json=[failed.model_dump(mode="json")])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            repository = SupabaseAgentRunRepository(
                "https://example.supabase.co",
                "agent-secret",
                client=client,
            )
            return await repository.fail(
                run.id,
                error_code="timeout",
                error_message="ModelTimeoutError",
                model_calls=4,
                tool_calls=11,
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                estimated_cost=Decimal("0.012"),
            )

    failed = asyncio.run(scenario())

    assert failed.status is AgentRunStatus.FAILED
    assert failed.total_tokens == 150
