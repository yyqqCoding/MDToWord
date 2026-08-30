import asyncio
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.domain.models import TaskArtifact
from agent.domain.enums import FeedbackType
from agent.domain.errors import BudgetExceededError
from agent.repair_agent.models import ChatModelBundle, ChatModelProfile
from agent.repair_agent.runtime import RepairAgentRuntime
from agent.repair_agent.tools import RepairAgentContext
from agent.sandbox.contracts import (
    JUnitSummary,
    SandboxArtifacts,
    SandboxResult,
    SandboxStatus,
    TargetTestOutcome,
)
from agent.tools.edits import StructuredEditTools
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import PatchBuilder
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.source_repository import SourceSnapshot


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
FEEDBACK_ID = UUID("22222222-2222-4222-8222-222222222222")


class _SourceWorkspace:
    def __init__(self, root: Path) -> None:
        snapshot_root = root / "snapshot"
        (snapshot_root / "backend/app").mkdir(parents=True)
        (snapshot_root / "backend/tests").mkdir(parents=True)
        (snapshot_root / "backend/app/normalizer.py").write_text(
            "def normalize(value):\n    return value\n",
            "utf-8",
        )
        (snapshot_root / "backend/app/pandoc_runner.py").write_text(
            "def convert_markdown_to_docx(markdown, tmp_path):\n    return b'docx'\n",
            "utf-8",
        )
        (snapshot_root / "backend/tests/test_feedback_regressions.py").write_text(
            "",
            "utf-8",
        )
        archive = root / "snapshot.tar.gz"
        archive.write_bytes(b"synthetic-source-archive")
        self.snapshot = SourceSnapshot(
            base_sha="a" * 40,
            source_snapshot_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            root=snapshot_root,
            archive_path=archive,
        )

    def resolve(self, reference: str) -> SourceSnapshot:
        assert reference == "source://synthetic"
        return self.snapshot


class _Sandbox:
    def __init__(self) -> None:
        self.jobs: list[SandboxArtifacts] = []

    async def submit(self, artifacts: SandboxArtifacts) -> SandboxResult:
        self.jobs.append(artifacts)
        now = datetime.now(UTC)
        if len(self.jobs) == 1:
            summary = JUnitSummary(
                tests=1,
                failures=0,
                errors=1,
                skipped=0,
                target_collected=True,
                target_outcome=TargetTestOutcome.ERROR,
                target_failure_type="app.pandoc_runner.ConversionError",
                target_message="ConversionError: synthetic conversion failure",
            )
            exit_code = 1
        else:
            summary = JUnitSummary(
                tests=1,
                failures=0,
                errors=0,
                skipped=0,
                target_collected=True,
                target_outcome=TargetTestOutcome.PASSED,
            )
            exit_code = 0
        return SandboxResult(
            job_id=artifacts.job.job_id,
            status=SandboxStatus.COMPLETED,
            exit_code=exit_code,
            started_at=now,
            finished_at=now,
            duration_ms=10,
            junit_summary=summary,
        )


class _ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def test_conversion_error_probe_flows_into_react_fix_and_completion(tmp_path: Path):
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "submit_fix_edits",
                    "id": "fix",
                    "args": {
                        "edits": [
                            {
                                "path": "backend/app/normalizer.py",
                                "mode": "search_replace",
                                "search": "def normalize(value):\n    return value\n",
                                "replace": "def normalize(value):\n    return value.strip()\n",
                                "content": None,
                            }
                        ],
                        "summary": "normalize the failing conversion input",
                        "risk": "low",
                    },
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "run_sandbox", "id": "sandbox", "args": {"reason": "verify fix"}}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "complete_repair",
                    "id": "complete",
                    "args": {"evidence_summary": "trusted target test passed"},
                }
            ],
        ),
        AIMessage(content="candidate ready"),
    ]
    model = _ToolCallingFakeModel(responses=responses)
    profile = ChatModelProfile(
        role="fake",
        model_name="fake",
        source="configured",
        max_input_tokens=20_000,
        tool_calling=True,
    )
    bundle = ChatModelBundle(
        primary=model,
        fallback=model,
        summary=model,
        primary_profile=profile,
        fallback_profile=profile,
        effective_context_window=20_000,
        primary_input_cost_per_million=Decimal("0"),
        primary_output_cost_per_million=Decimal("0"),
        fallback_input_cost_per_million=Decimal("0"),
        fallback_output_cost_per_million=Decimal("0"),
    )
    source = _SourceWorkspace(tmp_path / "source")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    sandbox = _Sandbox()
    runtime = RepairAgentRuntime(
        bundle,
        checkpointer=MemorySaver(),
        max_model_calls=12,
        max_tool_calls=30,
    )
    context = RepairAgentContext(
        run_id=RUN_ID,
        feedback_id=FEEDBACK_ID,
        task=TaskArtifact(
            feedback_id=FEEDBACK_ID,
            feedback_type=FeedbackType.BUG,
            description="conversion raises for this formula",
            markdown_content="$$x$$",
            content_fingerprint="b" * 64,
        ),
        source_snapshot_ref="source://synthetic",
        source_workspace=source,
        artifact_store=artifacts,
        edit_tools=StructuredEditTools(
            PatchBuilder(PatchPolicy.load_default()),
            artifacts,
        ),
        sandbox_client=sandbox,
        max_reproduction_rounds=2,
        max_repair_rounds=2,
        max_sandbox_seconds=900,
    )

    outcome = asyncio.run(runtime.run(context, category="conversion_crash"))

    assert outcome.completed is True
    assert outcome.test_patch_ref is not None
    assert outcome.fix_patch_ref is not None
    assert outcome.reproduction_result_ref is not None
    assert outcome.repair_result_ref is not None
    assert outcome.reproduction_round == 1
    assert outcome.repair_round == 1
    assert outcome.tool_calls == 5
    assert len(sandbox.jobs) == 2


def test_official_model_call_limit_becomes_resumable_budget_error(tmp_path: Path):
    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_fix_edits",
                        "id": "fix",
                        "args": {
                            "edits": [
                                {
                                    "path": "backend/app/normalizer.py",
                                    "mode": "search_replace",
                                    "search": "def normalize(value):\n    return value\n",
                                    "replace": "def normalize(value):\n    return value.strip()\n",
                                    "content": None,
                                }
                            ],
                            "summary": "normalize the failing conversion input",
                            "risk": "low",
                        },
                    }
                ],
            )
        ]
    )
    profile = ChatModelProfile(
        role="fake",
        model_name="fake",
        source="configured",
        max_input_tokens=20_000,
        tool_calling=True,
    )
    bundle = ChatModelBundle(
        primary=model,
        fallback=model,
        summary=model,
        primary_profile=profile,
        fallback_profile=profile,
        effective_context_window=20_000,
        primary_input_cost_per_million=Decimal("0"),
        primary_output_cost_per_million=Decimal("0"),
        fallback_input_cost_per_million=Decimal("0"),
        fallback_output_cost_per_million=Decimal("0"),
    )
    source = _SourceWorkspace(tmp_path / "source")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    sandbox = _Sandbox()
    runtime = RepairAgentRuntime(
        bundle,
        checkpointer=MemorySaver(),
        max_model_calls=1,
        max_tool_calls=30,
    )
    context = RepairAgentContext(
        run_id=RUN_ID,
        feedback_id=FEEDBACK_ID,
        task=TaskArtifact(
            feedback_id=FEEDBACK_ID,
            feedback_type=FeedbackType.BUG,
            description="conversion raises for this formula",
            markdown_content="$$x$$",
            content_fingerprint="b" * 64,
        ),
        source_snapshot_ref="source://synthetic",
        source_workspace=source,
        artifact_store=artifacts,
        edit_tools=StructuredEditTools(PatchBuilder(PatchPolicy.load_default()), artifacts),
        sandbox_client=sandbox,
        max_reproduction_rounds=2,
        max_repair_rounds=2,
        max_sandbox_seconds=900,
    )

    with pytest.raises(BudgetExceededError) as exc_info:
        asyncio.run(runtime.run(context, category="conversion_crash"))

    error = exc_info.value
    assert error.phase == "repairing"
    assert error.node == "repair_agent"
    assert error.safe_details == {
        "budget_type": "model_calls",
        "model_calls": 1,
        "tool_calls": 3,
    }
    assert error.additional_model_calls == 1
    assert artifacts.path_for(RUN_ID, "fix.patch").read_bytes()
