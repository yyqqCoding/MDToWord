import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from agent.domain.enums import AgentRunStatus, FeedbackType, GateRoute
from agent.domain.models import AgentRunRecord, FeedbackRecord
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.scheduler import FeedbackScheduler


def make_feedback(created_at: datetime) -> FeedbackRecord:
    return FeedbackRecord(
        id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="$x$",
        description="公式导出错误",
        created_at=created_at,
        updated_at=created_at,
    )


def test_scheduler_never_processes_more_than_one_feedback_concurrently():
    class RecordingController:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def start(self, feedback):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return type(
                "Outcome",
                (),
                {
                    "run_id": uuid4(),
                    "feedback_id": feedback.id,
                    "route": GateRoute.NEEDS_HUMAN,
                    "completed": True,
                },
            )()

        async def resume(self, run_id):
            raise AssertionError("no resumable run expected")

    async def scenario():
        now = datetime.now(UTC)
        feedback_repository = FakeFeedbackRepository(
            [make_feedback(now), make_feedback(now)]
        )
        controller = RecordingController()
        scheduler = FeedbackScheduler(
            feedback_repository=feedback_repository,
            run_repository=FakeAgentRunRepository(),
            controller=controller,
            lease_seconds=60,
            max_attempts=3,
        )
        results = await asyncio.gather(scheduler.run_once(), scheduler.run_once())
        return controller, results

    controller, results = asyncio.run(scenario())

    assert controller.max_active == 1
    assert all(result is not None for result in results)


def test_scheduler_resumes_existing_gate_run_before_claiming_new_feedback():
    class RecordingController:
        def __init__(self):
            self.resumed = []

        async def start(self, feedback):
            raise AssertionError("new feedback must not be claimed first")

        async def resume(self, run_id):
            self.resumed.append(run_id)
            return "resumed"

    async def scenario():
        now = datetime.now(UTC)
        feedback_repository = FakeFeedbackRepository([make_feedback(now)])
        run_repository = FakeAgentRunRepository()
        resumable = AgentRunRecord(
            id=uuid4(),
            feedback_id=uuid4(),
            claim_token=uuid4(),
            trace_id="trace-resume",
            status=AgentRunStatus.GATING,
            graph_version="gate-graph-v1",
            prompt_versions={"gate": "gate-v1"},
            policy_version="gate-policy-v1",
            task_artifact_ref="artifact://run/task.redacted.json",
            artifact_path="artifact://run",
        )
        await run_repository.create(resumable)
        controller = RecordingController()
        scheduler = FeedbackScheduler(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            controller=controller,
            lease_seconds=60,
            max_attempts=3,
        )
        result = await scheduler.run_once()
        return resumable, controller, result

    resumable, controller, result = asyncio.run(scenario())

    assert result == "resumed"
    assert controller.resumed == [resumable.id]


def test_scheduler_resumes_publication_before_claiming_new_feedback():
    """发布中断也必须优先恢复，不能越过旧 run 领取下一条反馈。"""

    class RecordingController:
        async def start(self, feedback):
            raise AssertionError("new feedback must not be claimed first")

        async def resume(self, run_id):
            return run_id

    async def scenario():
        now = datetime.now(UTC)
        feedback_repository = FakeFeedbackRepository([make_feedback(now)])
        run_repository = FakeAgentRunRepository()
        publishing = AgentRunRecord(
            id=uuid4(),
            feedback_id=uuid4(),
            claim_token=uuid4(),
            trace_id="trace-publishing-resume",
            status=AgentRunStatus.PUBLISHING,
            graph_version="agent-graph-v6",
            prompt_versions={"gate": "gate-v3"},
            policy_version="publication-policy-v1",
            task_artifact_ref="artifact://run/task.redacted.json",
            artifact_path="artifact://run",
        )
        await run_repository.create(publishing)
        scheduler = FeedbackScheduler(
            feedback_repository=feedback_repository,
            run_repository=run_repository,
            controller=RecordingController(),
            lease_seconds=60,
            max_attempts=3,
        )
        result = await scheduler.run_once()
        return publishing.id, result

    run_id, result = asyncio.run(scenario())

    assert result == run_id


def test_scheduler_forever_contains_single_run_failure():
    async def scenario():
        now = datetime.now(UTC)
        scheduler = FeedbackScheduler(
            feedback_repository=FakeFeedbackRepository([make_feedback(now)]),
            run_repository=FakeAgentRunRepository(),
            controller=object(),
            lease_seconds=60,
            max_attempts=3,
            poll_interval_seconds=0.001,
        )
        stop_event = asyncio.Event()
        calls = 0

        async def failing_once():
            nonlocal calls
            calls += 1
            stop_event.set()
            raise RuntimeError("secret response body")

        scheduler.run_once = failing_once
        await scheduler.run_forever(stop_event)
        return calls

    assert asyncio.run(scenario()) == 1
