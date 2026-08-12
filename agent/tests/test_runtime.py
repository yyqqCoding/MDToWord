import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

import agent.runtime as runtime_module
from agent.config import AgentConfig
from agent.telemetry.base import NoopTelemetry


class _FeedbackRepositorySpy:
    """保持与真实仓库一致的签名，让运行时接线错误在单元测试中立即失败。"""

    def __init__(self, supabase_url: str, agent_key: str, *, client=None) -> None:
        self.supabase_url = supabase_url
        self.agent_key = agent_key
        self.client = client


def test_configured_runtime_wires_feedback_repository_with_keyword_client(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = AgentConfig.from_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_AGENT_KEY": "supabase-secret",
            "AGENT_DATABASE_URL": "postgresql://agent:secret@example.com/postgres",
            "MODEL_NAME": "compatible-model",
            "MODEL_API_KEY": "model-secret",
            "MODEL_BASE_URL": "https://models.example/v1",
            "LANGFUSE_PUBLIC_KEY": "langfuse-public",
            "LANGFUSE_SECRET_KEY": "langfuse-secret",
        },
        project_root=tmp_path,
    )

    @asynccontextmanager
    async def fake_checkpointer(database_url: str, schema: str):
        del database_url, schema
        yield InMemorySaver()

    monkeypatch.setattr(
        runtime_module,
        "SupabaseFeedbackRepository",
        _FeedbackRepositorySpy,
    )
    monkeypatch.setattr(
        runtime_module,
        "LangfuseTelemetry",
        lambda **kwargs: NoopTelemetry(),
    )
    monkeypatch.setattr(
        runtime_module,
        "open_postgres_checkpointer",
        fake_checkpointer,
    )

    async def exercise_runtime() -> None:
        async with runtime_module.open_configured_runtime(
            config,
            stage="gate",
            dry_run=True,
        ) as configured:
            repository = configured.feedback_repository
            assert repository.supabase_url == "https://example.supabase.co"
            assert repository.agent_key == "supabase-secret"
            assert repository.client is not None

    asyncio.run(exercise_runtime())
