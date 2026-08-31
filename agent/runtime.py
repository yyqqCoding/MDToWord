"""统一装配真实 D→E→F Controller，CLI 与生产 Scheduler 共享同一配置边界。"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import AsyncIterator, Literal

import httpx

from agent.checkpoint import open_postgres_checkpointer
from agent.config import AgentConfig
from agent.controller import GateController
from agent.domain.failures import FailureRecorder
from agent.graph import (
    PublishingDependencies,
    RepairDependencies,
    ReproductionDependencies,
)
from agent.operations.site_notify import TraceSiteNotifier, build_trace_site_notifier
from agent.repair_agent.models import build_chat_model_bundle
from agent.repair_agent.runtime import RepairAgentRuntime
from agent.providers.openai_compatible import OpenAICompatibleProvider
from agent.publishing.github import (
    GitHubAppTokenProvider,
    GitHubIssuePublisher,
    GitHubPullRequestPublisher,
)
from agent.repositories.supabase import (
    SupabaseAgentRunRepository,
    SupabaseFeedbackRepository,
)
from agent.sandbox.client import HttpSandboxClient
from agent.telemetry.langfuse import LangfuseTelemetry
from agent.tools.edits import StructuredEditTools
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import PatchBuilder
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.preparation import GitHubSourceWorkspace
from agent.workspace.source_repository import GitHubSourceRepository
from agent.workspace.versioning import GitHubMainRevisionReader, read_extension_version


RuntimeStage = Literal["gate", "reproduction", "repair", "publication"]


@dataclass(frozen=True)
class ConfiguredRuntime:
    controller: GateController
    feedback_repository: SupabaseFeedbackRepository
    run_repository: SupabaseAgentRunRepository
    # 未配置展示站点回调时为 None，Scheduler 据此完全跳过推送。
    trace_site_notifier: TraceSiteNotifier | None = None


class _SilentFailureRecorder(FailureRecorder):
    """Agent 内 Sandbox attempt 由 Middleware 统一记录，避免 Client 误记为 STOP。"""

    def record(self, event: object) -> None:
        return None


@asynccontextmanager
async def open_configured_runtime(
    config: AgentConfig,
    *,
    stage: RuntimeStage,
    dry_run: bool,
) -> AsyncIterator[ConfiguredRuntime]:
    """在领取反馈前校验并隔离所有真实凭据，退出时统一 flush/close。"""

    database_url = config.require_database_url()
    model_name, model_api_key, model_base_url = config.require_model_settings()
    fallback_model = config.fallback_model_settings()
    langfuse_host, langfuse_public_key, langfuse_secret_key = (
        config.require_langfuse_settings()
    )
    shared_client = httpx.AsyncClient(timeout=30)
    source_client: httpx.AsyncClient | None = None
    publisher_client: httpx.AsyncClient | None = None
    notifier_client: httpx.AsyncClient | None = None
    telemetry = LangfuseTelemetry(
        public_key=langfuse_public_key,
        secret_key=langfuse_secret_key,
        host=langfuse_host,
        environment=config.agent_environment,
    )
    failure_recorder = FailureRecorder(telemetry)
    try:
        provider = OpenAICompatibleProvider(
            api_key=model_api_key,
            model=model_name,
            base_url=model_base_url,
            client=shared_client,
            input_cost_per_million=config.model_input_cost_per_million,
            output_cost_per_million=config.model_output_cost_per_million,
            fallback_model=fallback_model[0] if fallback_model is not None else None,
            fallback_api_key=(
                fallback_model[1] if fallback_model is not None else None
            ),
            fallback_base_url=(
                fallback_model[2] if fallback_model is not None else None
            ),
            fallback_input_cost_per_million=(
                config.fallback_model_input_cost_per_million
            ),
            fallback_output_cost_per_million=(
                config.fallback_model_output_cost_per_million
            ),
            failure_recorder=failure_recorder,
        )
        artifacts = ArtifactStore(config.artifact_root)
        reproduction = None
        repair = None
        publishing = None
        chat_models = None

        if stage in {"reproduction", "repair", "publication"}:
            # 只有进入复现/修复阶段才装配 ChatModel、源码读取和 Sandbox；Gate-only
            # 运行保持轻量，也不会在不需要时加载更多凭据。
            chat_models = build_chat_model_bundle(config)
            repository, read_token, worker_url, worker_credential = (
                config.require_stage_c_controller_settings()
            )
            # Read token 与 App 私钥分属不同 Client，避免默认 Header 跨边界传播。
            source_client = httpx.AsyncClient(
                timeout=30,
                headers={"Authorization": f"Bearer {read_token}"},
            )
            reproduction = ReproductionDependencies(
                plan_provider=provider,
                test_provider=provider,
                source_workspace=GitHubSourceWorkspace(
                    config.source_workspace_root,
                    GitHubMainRevisionReader(
                        repository,
                        client=source_client,
                        failure_recorder=failure_recorder,
                    ),
                    GitHubSourceRepository(
                        repository,
                        client=source_client,
                        failure_recorder=failure_recorder,
                    ),
                ),
                edit_tools=StructuredEditTools(
                    PatchBuilder(PatchPolicy.load_default()),
                    artifacts,
                ),
                sandbox_client=HttpSandboxClient(
                    worker_url,
                    credential=worker_credential,
                    client=shared_client,
                    failure_recorder=failure_recorder,
                ),
                agent_sandbox_client=HttpSandboxClient(
                    worker_url,
                    credential=worker_credential,
                    client=shared_client,
                    max_transport_retries=0,
                    failure_recorder=_SilentFailureRecorder(),
                ),
                telemetry=telemetry,
                failure_recorder=failure_recorder,
                model_timeout_seconds=config.reproduction_model_timeout_seconds,
            )

        if stage in {"repair", "publication"}:
            repair = RepairDependencies(
                fix_provider=provider,
                telemetry=telemetry,
                model_timeout_seconds=config.reproduction_model_timeout_seconds,
                max_model_calls=config.max_model_calls_per_run,
                max_tool_calls=config.max_tool_calls_per_run,
                max_sandbox_seconds=config.max_sandbox_seconds_per_run,
                baseline_skipped=config.backend_baseline_skipped,
            )

        if stage == "publication":
            assert config.github_repository is not None
            (
                app_id,
                private_key,
                api_url,
                main_branch,
                trace_url_template,
            ) = config.require_stage_f_publisher_settings()
            publisher_client = httpx.AsyncClient(timeout=30)
            token_provider = GitHubAppTokenProvider(
                config.github_repository,
                app_id=app_id,
                private_key=private_key,
                client=publisher_client,
                api_url=api_url,
            )
            issue_token_provider = GitHubAppTokenProvider(
                config.github_repository,
                app_id=app_id,
                private_key=private_key,
                client=publisher_client,
                api_url=api_url,
                permissions={"issues": "write"},
            )
            publishing = PublishingDependencies(
                publisher=GitHubPullRequestPublisher(
                    config.github_repository,
                    token_provider=token_provider,
                    client=publisher_client,
                    api_url=api_url,
                    main_branch=main_branch,
                ),
                issue_publisher=GitHubIssuePublisher(
                    config.github_repository,
                    token_provider=issue_token_provider,
                    client=publisher_client,
                    api_url=api_url,
                ),
                trace_url_template=trace_url_template,
                telemetry=telemetry,
            )

        feedback_repository = SupabaseFeedbackRepository(
            config.supabase_url,
            config.supabase_agent_key.get_secret_value(),
            client=shared_client,
        )
        run_repository = SupabaseAgentRunRepository(
            config.supabase_url,
            config.supabase_agent_key.get_secret_value(),
            client=shared_client,
        )
        # 展示站点是又一个凭据边界，沿用「一凭据一 Client」的既有约定单独建连接。
        webhook_settings = config.trace_site_webhook_settings()
        if webhook_settings is not None:
            notifier_client = httpx.AsyncClient(timeout=30)
        trace_site_notifier = build_trace_site_notifier(
            webhook_settings,
            client=notifier_client or shared_client,
            telemetry=telemetry,
        )
        async with open_postgres_checkpointer(
            database_url,
            config.checkpoint_schema,
        ) as checkpointer:
            if reproduction is not None:
                assert chat_models is not None
                # Runtime 与外层 Graph 共享同一个 Postgres checkpoint，但使用
                # repair:<run_id> 独立 thread，内层消息压缩不会污染外层业务状态。
                reproduction = replace(
                    reproduction,
                    agent_runtime=RepairAgentRuntime(
                        chat_models,
                        checkpointer=checkpointer,
                        max_model_calls=config.max_model_calls_per_run,
                        max_tool_calls=config.max_tool_calls_per_run,
                        failure_recorder=failure_recorder,
                        telemetry=telemetry,
                    ),
                )
            yield ConfiguredRuntime(
                controller=GateController(
                    feedback_repository=feedback_repository,
                    run_repository=run_repository,
                    provider=provider,
                    artifact_store=artifacts,
                    checkpointer=checkpointer,
                    min_confidence=config.min_gate_confidence,
                    gate_timeout_seconds=config.gate_model_timeout_seconds,
                    extension_version=read_extension_version(
                        config.extension_manifest_path
                    ),
                    telemetry=telemetry,
                    environment=config.agent_environment,
                    dry_run=dry_run,
                    reproduction=reproduction,
                    repair=repair,
                    publishing=publishing,
                    failure_recorder=failure_recorder,
                ),
                feedback_repository=feedback_repository,
                run_repository=run_repository,
                trace_site_notifier=trace_site_notifier,
            )
    finally:
        telemetry.flush()
        await shared_client.aclose()
        if source_client is not None:
            await source_client.aclose()
        if publisher_client is not None:
            await publisher_client.aclose()
        if notifier_client is not None:
            await notifier_client.aclose()
