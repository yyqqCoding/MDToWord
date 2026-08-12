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
from agent.repositories.supabase import (
    SupabaseAgentRunRepository,
    SupabaseFeedbackRepository,
)
from agent.scheduler import FeedbackScheduler
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.versioning import read_extension_version
from agent.telemetry.base import NoopTelemetry


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
    target = run_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--feedback-id", type=UUID)
    target.add_argument("--resume-run-id", type=UUID)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--reproduce",
        action="store_true",
        help="continue accepted backend feedback through the Stage D reproduction graph",
    )
    run_parser.add_argument(
        "--repair",
        action="store_true",
        help="continue a reproduced backend defect through Stage E repair and validation",
    )
    run_parser.add_argument(
        "--publish",
        action="store_true",
        help="publish a passed Stage E validation through the Stage F GitHub App",
    )
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

    scheduler_parser = commands.add_parser("scheduler")
    scheduler_mode = scheduler_parser.add_mutually_exclusive_group(required=True)
    scheduler_mode.add_argument("--once", action="store_true")
    scheduler_mode.add_argument("--forever", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run" and args.publish and args.dry_run:
            raise CliUsageError("--publish cannot be combined with --dry-run")
        if args.command == "run" and not args.dry_run and not args.publish:
            _print_json({"error": "dry_run_required"})
            return 2
        if (
            args.command == "run"
            and (
                args.reproduce
                or args.repair
                or args.publish
                or args.resume_run_id is not None
            )
            and (
                args.provider != "configured"
                or (not args.reproduce and not args.repair and not args.publish)
            )
        ):
            raise CliUsageError(
                "reproduction and resume require --reproduce, --repair or --publish with "
                "--provider configured"
            )
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
    if args.command == "checkpoint":
        database_url = config.require_database_url()
        async with open_postgres_checkpointer(
            database_url,
            config.checkpoint_schema,
            setup=True,
        ):
            pass
        _print_json({"status": "checkpoint_ready", "schema": config.checkpoint_schema})
        return 0
    if args.command == "scheduler":
        outcome = await _run_production_scheduler(config, once=args.once)
        _print_json(
            {
                "status": "idle" if outcome is None else "processed",
                "run_id": str(outcome.run_id) if outcome is not None else None,
                "feedback_id": (
                    str(outcome.feedback_id) if outcome is not None else None
                ),
                "route": (
                    outcome.route.value
                    if outcome is not None and outcome.route is not None
                    else None
                ),
                "pr_url": outcome.pr_url if outcome is not None else None,
            }
        )
        return 0

    result = await _run_dry_gate(
        args.feedback_id,
        args.fake_route,
        config,
        provider_mode=args.provider,
        reproduce=args.reproduce,
        repair=args.repair,
        publish=args.publish,
        resume_run_id=args.resume_run_id,
    )
    _print_json(
        {
            "run_id": str(result.run_id),
            "feedback_id": str(result.feedback_id),
            "route": result.route.value if result.route else None,
            "completed": result.completed,
            "dry_run": not args.publish,
            "provider": args.provider,
            "pr_url": result.pr_url,
            "stage": (
                "publication"
                if args.publish
                else (
                    "repair"
                    if args.repair
                    else ("reproduction" if args.reproduce else "gate")
                )
            ),
        }
    )
    return 0


async def _run_dry_gate(
    feedback_id: UUID | None,
    fake_route: GateRoute,
    config: AgentConfig,
    *,
    provider_mode: str = "fake",
    reproduce: bool = False,
    repair: bool = False,
    publish: bool = False,
    resume_run_id: UUID | None = None,
) -> GateRunOutcome:
    # 所有外部依赖配置在领取反馈前完成校验，避免配置错误留下无主租约。
    database_url = config.require_database_url()
    repair = repair or publish
    reproduce = reproduce or repair
    if reproduce and provider_mode != "configured":
        raise CliUsageError("--reproduce/--repair requires --provider configured")
    if provider_mode == "configured":
        from agent.runtime import open_configured_runtime

        stage = (
            "publication"
            if publish
            else ("repair" if repair else ("reproduction" if reproduce else "gate"))
        )
        async with open_configured_runtime(
            config,
            stage=stage,
            dry_run=not publish,
        ) as runtime:
            return await _start_or_resume(
                runtime.controller,
                runtime.feedback_repository,
                feedback_id=feedback_id,
                resume_run_id=resume_run_id,
                config=config,
            )

    async with httpx.AsyncClient(timeout=30) as client:
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
        async with open_postgres_checkpointer(
            database_url,
            config.checkpoint_schema,
        ) as checkpointer:
            controller = GateController(
                feedback_repository=feedback_repository,
                run_repository=run_repository,
                provider=FakeModelProvider(
                    [fake_classification_for_route(fake_route)]
                ),
                artifact_store=ArtifactStore(config.artifact_root),
                checkpointer=checkpointer,
                min_confidence=config.min_gate_confidence,
                extension_version=read_extension_version(
                    config.extension_manifest_path
                ),
                telemetry=NoopTelemetry(),
                environment=config.agent_environment,
            )
            return await _start_or_resume(
                controller,
                feedback_repository,
                feedback_id=feedback_id,
                resume_run_id=resume_run_id,
                config=config,
            )


async def _start_or_resume(
    controller,
    feedback_repository,
    *,
    feedback_id: UUID | None,
    resume_run_id: UUID | None,
    config: AgentConfig,
) -> GateRunOutcome:
    if resume_run_id is not None:
        return await controller.resume(resume_run_id)
    if feedback_id is None:
        raise CliUsageError("feedback id is required for a new run")
    claimed = await feedback_repository.claim_by_id(
        feedback_id,
        now=_utc_now(),
        lease_seconds=config.claim_lease_seconds,
        max_attempts=config.max_claim_attempts,
    )
    if claimed is None:
        raise CliUsageError("feedback is not claimable")
    return await controller.start(claimed)


async def _run_production_scheduler(
    config: AgentConfig,
    *,
    once: bool,
) -> GateRunOutcome | None:
    """生产开关只控制是否领取反馈，Graph 内仍保持全自动 D→E→F。"""

    config.require_production_scheduler_enabled()
    from agent.runtime import open_configured_runtime

    async with open_configured_runtime(
        config,
        stage="publication",
        dry_run=False,
    ) as runtime:
        scheduler = FeedbackScheduler(
            feedback_repository=runtime.feedback_repository,
            run_repository=runtime.run_repository,
            controller=runtime.controller,
            lease_seconds=config.claim_lease_seconds,
            max_attempts=config.max_claim_attempts,
            poll_interval_seconds=config.poll_interval_seconds,
        )
        if once:
            return await scheduler.run_once()
        await scheduler.run_forever(asyncio.Event())
        return None


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
