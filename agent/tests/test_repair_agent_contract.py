from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent.config import AgentConfig
from agent.controller import GateController
from agent.domain.errors import ConfigurationError
from agent.graph import RepairDependencies, ReproductionDependencies
from agent.providers.fake import FakeModelProvider
from agent.repositories.fake import FakeAgentRunRepository, FakeFeedbackRepository
from agent.repair_agent.models import (
    ChatModelBundle,
    ChatModelProfile,
    build_chat_model_bundle,
)
from agent.repair_agent.runtime import RepairAgentRuntime
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import Edit, EditMode
from agent.repair_agent.tools import _validate_model_test_append


def _config(tmp_path: Path, **updates: object) -> AgentConfig:
    values = {
        "supabase_url": "https://example.supabase.co",
        "supabase_agent_key": SecretStr("database-secret"),
        "artifact_root": tmp_path / "artifacts",
        "source_workspace_root": tmp_path / "source",
        "extension_manifest_path": tmp_path / "manifest.json",
        "model_name": "custom-primary",
        "model_api_key": SecretStr("primary-secret"),
        "model_base_url": "https://primary.example/v1",
        "model_context_window": 128_000,
        "fallback_model_enabled": True,
        "fallback_model_name": "custom-fallback",
        "fallback_model_api_key": SecretStr("fallback-secret"),
        "fallback_model_base_url": "https://fallback.example/v1",
        "fallback_model_context_window": 64_000,
    }
    values.update(updates)
    return AgentConfig(**values)


def test_chat_model_bundle_uses_smaller_context_window_and_disables_sdk_retry(
    tmp_path: Path,
):
    bundle = build_chat_model_bundle(_config(tmp_path))

    assert bundle.effective_context_window == 64_000
    assert bundle.primary.max_retries == 0
    assert bundle.fallback.max_retries == 0
    assert bundle.primary_profile.source == "configured"
    assert bundle.fallback_profile.source == "configured"
    assert bundle.primary_input_cost_per_million == Decimal("0")


def test_chat_model_bundle_requires_fallback(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="FALLBACK_MODEL_ENABLED"):
        build_chat_model_bundle(
            _config(
                tmp_path,
                fallback_model_enabled=False,
                fallback_model_name=None,
                fallback_model_api_key=None,
                fallback_model_base_url=None,
            )
        )


def test_summary_prompt_has_all_durable_sections():
    prompt = Path("agent/prompts/repair_summary.md").read_text("utf-8")

    for heading in (
        "目标",
        "用户明确要求",
        "可信事实与引用",
        "已完成事项及证据",
        "当前结构化状态",
        "失败尝试与原因",
        "下一步",
        "禁止事项与安全边界",
        "仍不确定的事项",
    ):
        assert f"## {heading}" in prompt
    assert "不得宣称" in prompt
    assert "[REDACTED]" in prompt


def test_production_graph_registers_repair_agent_without_legacy_model_nodes(
    tmp_path: Path,
):
    model = FakeListChatModel(responses=["unused"])
    profile = ChatModelProfile(
        role="test",
        model_name="fake",
        source="configured",
        max_input_tokens=8_000,
        tool_calling=True,
    )
    bundle = ChatModelBundle(
        primary=model,
        fallback=model,
        summary=model,
        primary_profile=profile,
        fallback_profile=profile,
        effective_context_window=8_000,
        primary_input_cost_per_million=Decimal("0"),
        primary_output_cost_per_million=Decimal("0"),
        fallback_input_cost_per_million=Decimal("0"),
        fallback_output_cost_per_million=Decimal("0"),
    )
    checkpointer = MemorySaver()
    runtime = RepairAgentRuntime(
        bundle,
        checkpointer=checkpointer,
        max_model_calls=12,
        max_tool_calls=30,
    )
    reproduction = ReproductionDependencies(
        plan_provider=FakeModelProvider([]),
        test_provider=FakeModelProvider([]),
        source_workspace=object(),
        edit_tools=object(),
        sandbox_client=object(),
        agent_runtime=runtime,
    )
    controller = GateController(
        feedback_repository=FakeFeedbackRepository([]),
        run_repository=FakeAgentRunRepository(),
        provider=FakeModelProvider([]),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        checkpointer=checkpointer,
        reproduction=reproduction,
        repair=RepairDependencies(fix_provider=FakeModelProvider([])),
    )

    nodes = set(controller.graph.nodes)

    assert "repair_agent" in nodes
    assert "plan_reproduction" not in nodes
    assert "generate_test_edit" not in nodes
    assert "generate_fix_edit" not in nodes


def test_model_test_edit_cannot_overwrite_existing_regressions(tmp_path: Path):
    target = tmp_path / "backend/tests/test_feedback_regressions.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_existing():\n    assert True\n", "utf-8")

    with pytest.raises(ValueError, match="search_replace append"):
        _validate_model_test_append(
            tmp_path,
            (
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.FULL_FILE,
                    content="def test_replacement():\n    assert True\n",
                ),
            ),
        )
