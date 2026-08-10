import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import httpx

from agent.checkpoint import open_postgres_checkpointer
from agent.config import AgentConfig
from agent.controller import GateController, GateRunOutcome
from agent.domain.enums import GateCategory, GateIntent, GateRoute
from agent.domain.errors import AgentError, ConfigurationError
from agent.domain.gate import GateClassification
from agent.providers.fake import FakeModelProvider
from agent.providers.openai_compatible import OpenAICompatibleProvider
from agent.repositories.supabase import (
    SupabaseAgentRunRepository,
    SupabaseFeedbackRepository,
)
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.versioning import read_extension_version
from agent.telemetry.base import NoopTelemetry
from agent.telemetry.langfuse import LangfuseTelemetry


_FAKE_ROUTES = (
    GateRoute.ACCEPTED_BACKEND_BUG,
    GateRoute.REJECTED_IRRELEVANT,
    GateRoute.QUARANTINED_SECURITY,
    GateRoute.OUT_OF_SCOPE,
    GateRoute.NEEDS_HUMAN,
)


class CliUsageError(AgentError):
    error_code = "cli_usage_error"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mdtoword-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--feedback-id", type=UUID, required=True)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--provider",
        choices=("fake", "configured"),
        default="fake",
    )
    run_parser.add_argument(
        "--fake-route",
        type=GateRoute,
        choices=_FAKE_ROUTES,
        default=GateRoute.NEEDS_HUMAN,
    )

    checkpoint_parser = commands.add_parser("checkpoint")
    checkpoint_parser.add_argument("action", choices=("setup",))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run" and not args.dry_run:
            _print_json({"error": "dry_run_required"})
            return 2
        return asyncio.run(_execute(args))
    except (ConfigurationError, CliUsageError) as exc:
        _print_json({"error": exc.error_code, "message": str(exc)})
        return 2
    except AgentError as exc:
        _print_json({"error": exc.error_code})
        return 1
    except Exception as exc:  # pragma: no cover - production safety boundary
        # 外部库异常可能带 DSN、Header 或用户内容，只输出异常类型。
        _print_json({"error": "unexpected_error", "type": type(exc).__name__})
        return 1


async def _execute(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env()
    database_url = config.require_database_url()
    if args.command == "checkpoint":
        async with open_postgres_checkpointer(
            database_url,
            config.checkpoint_schema,
            setup=True,
        ):
            pass
        _print_json({"status": "checkpoint_ready", "schema": config.checkpoint_schema})
        return 0

    result = await _run_dry_gate(
        args.feedback_id,
        args.fake_route,
        config,
        provider_mode=args.provider,
    )
    _print_json(
        {
            "run_id": str(result.run_id),
            "feedback_id": str(result.feedback_id),
            "route": result.route.value if result.route else None,
            "completed": result.completed,
            "dry_run": True,
            "provider": args.provider,
        }
    )
    return 0


async def _run_dry_gate(
    feedback_id: UUID,
    fake_route: GateRoute,
    config: AgentConfig,
    *,
    provider_mode: str = "fake",
) -> GateRunOutcome:
    # 所有外部依赖配置在领取反馈前完成校验，避免配置错误留下无主租约。
    database_url = config.require_database_url()
    async with httpx.AsyncClient(timeout=30) as client:
        telemetry = NoopTelemetry()
        if provider_mode == "configured":
            model_name, model_api_key, model_base_url = config.require_model_settings()
            langfuse_host, langfuse_public_key, langfuse_secret_key = (
                config.require_langfuse_settings()
            )
            provider = OpenAICompatibleProvider(
                api_key=model_api_key,
                model=model_name,
                base_url=model_base_url,
                client=client,
                input_cost_per_million=config.model_input_cost_per_million,
                output_cost_per_million=config.model_output_cost_per_million,
            )
            telemetry = LangfuseTelemetry(
                public_key=langfuse_public_key,
                secret_key=langfuse_secret_key,
                host=langfuse_host,
                environment=config.agent_environment,
            )
        else:
            provider = FakeModelProvider([fake_classification_for_route(fake_route)])

        feedback_repository = SupabaseFeedbackRepository(
            config.supabase_url,
            config.supabase_agent_key.get_secret_value(),
            client=client,
        )
        run_repository = SupabaseAgentRunRepository(
            config.supabase_url,
            config.supabase_agent_key.get_secret_value(),
            client=client,
        )
        claimed = await feedback_repository.claim_by_id(
            feedback_id,
            now=_utc_now(),
            lease_seconds=config.claim_lease_seconds,
            max_attempts=config.max_claim_attempts,
        )
        if claimed is None:
            raise CliUsageError("feedback is not claimable")

        async with open_postgres_checkpointer(
            database_url,
            config.checkpoint_schema,
        ) as checkpointer:
            controller = GateController(
                feedback_repository=feedback_repository,
                run_repository=run_repository,
                provider=provider,
                artifact_store=ArtifactStore(config.artifact_root),
                checkpointer=checkpointer,
                min_confidence=config.min_gate_confidence,
                extension_version=read_extension_version(
                    config.extension_manifest_path
                ),
                telemetry=telemetry,
                environment=config.agent_environment,
            )
            try:
                return await controller.start(claimed)
            finally:
                telemetry.flush()


def fake_classification_for_route(route: GateRoute) -> GateClassification:
    values = {
        GateRoute.ACCEPTED_BACKEND_BUG: {
            "intent": GateIntent.BUG_REPORT,
            "category": GateCategory.BACKEND_NORMALIZATION,
            "relevance": 0.99,
            "sufficient_information": True,
        },
        GateRoute.REJECTED_IRRELEVANT: {
            "intent": GateIntent.UNRELATED,
            "category": GateCategory.UNKNOWN,
            "relevance": 0.01,
            "sufficient_information": False,
        },
        GateRoute.QUARANTINED_SECURITY: {
            "intent": GateIntent.UNKNOWN,
            "category": GateCategory.UNKNOWN,
            "relevance": 0.50,
            "sufficient_information": False,
            "injection_suspected": True,
        },
        GateRoute.OUT_OF_SCOPE: {
            "intent": GateIntent.BUG_REPORT,
            "category": GateCategory.EXTENSION_UI,
            "relevance": 0.99,
            "sufficient_information": True,
            "requires_extension_change": True,
        },
        GateRoute.NEEDS_HUMAN: {
            "intent": GateIntent.UNKNOWN,
            "category": GateCategory.UNKNOWN,
            "relevance": 0.50,
            "sufficient_information": False,
        },
    }
    if route not in values:
        raise CliUsageError(f"unsupported fake route: {route.value}")
    payload = {
        "injection_suspected": False,
        "requires_extension_change": False,
        "reason": "B2 Fake Provider 固定场景",
        **values[route],
    }
    return GateClassification.model_validate(payload)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
