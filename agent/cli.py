"""Agent CLI。

    python -m agent.cli fetch    --feedback-id <uuid> --output task.json
    python -m agent.cli classify --task-file task.json          # 阶段 04
    python -m agent.cli repair   --task-file task.json          # 阶段 07
    python -m agent.cli validate --task-file task.json --test-patch t.patch --fix-patch f.patch  # 阶段 07
    python -m agent.cli finalize --result-file result.json      # 阶段 08
    python -m agent.cli run      --feedback-id <uuid> --dry-run

退出码:0 成功;1 运行错误(error_code 见 stderr 日志);
2 参数错误(argparse);20 无需处理(重复反馈);21 领取失败。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID, uuid4

from agent.config import AgentConfig
from agent.domain import TaskArtifact
from agent.exceptions import AgentError, ClaimUnavailableError, FeedbackNotFoundError
from agent.feedback_repository import FeedbackRepository, SupabaseFeedbackRepository
from agent.logging_utils import get_logger

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_WORK = 20
EXIT_CLAIM_FAILED = 21


def run_fetch(repository: FeedbackRepository, config: AgentConfig,
              feedback_id: UUID, output: Path) -> TaskArtifact:
    """Job A:读取 → 指纹去重 → 原子领取 → 建 run 记录 → 输出脱敏 task。"""
    log = get_logger("agent.fetch", feedback_id=str(feedback_id),
                     workflow_run_id=config.workflow_run_id)

    feedback = repository.get_feedback(feedback_id)
    if feedback is None:
        raise FeedbackNotFoundError(f"反馈不存在: {feedback_id}")

    fingerprint = feedback.fingerprint()
    duplicate = repository.find_open_resolution(fingerprint)
    if duplicate is not None and duplicate.id != feedback.id:
        repository.update_feedback(
            feedback.id, status="duplicate", resolution_type="duplicate",
            content_fingerprint=fingerprint,
            last_error=f"duplicate of {duplicate.id}",
        )
        log.info("重复反馈,跳过", context={"duplicate_of": str(duplicate.id),
                                            "fingerprint": fingerprint})
        raise SystemExit(EXIT_NO_WORK)

    claim_token = uuid4()
    claimed = repository.claim_feedback(feedback.id, claim_token,
                                        max_attempts=config.max_claim_attempts)
    if claimed is None:
        raise ClaimUnavailableError(
            f"领取失败(已被占用/状态不可领取/超过重试上限): {feedback_id}")

    repository.update_feedback(feedback.id, content_fingerprint=fingerprint)
    run_id = repository.create_run(
        feedback.id,
        provider=config.model_provider,
        model=config.model_name or "unconfigured",
        status="created",
        workflow_run_id=config.workflow_run_id,
    )

    artifact = TaskArtifact.from_feedback(claimed, claim_token=claim_token,
                                          agent_run_id=run_id)
    artifact.write(output)
    log.info("领取成功,task 已输出", context={
        "agent_run_id": str(run_id), "fingerprint": fingerprint,
        "attempt_count": claimed.attempt_count, "output": str(output),
    })
    return artifact


def _cmd_fetch(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env().require("supabase_url", "supabase_key")
    repository = SupabaseFeedbackRepository(
        config.supabase_url, config.supabase_key.get_secret_value())
    try:
        run_fetch(repository, config, args.feedback_id, Path(args.output))
    finally:
        repository.close()
    return EXIT_OK


def _cmd_not_implemented(stage: str):
    def handler(args: argparse.Namespace) -> int:
        print(f"该子命令在阶段 {stage} 实现,当前为阶段 02 骨架。", file=sys.stderr)
        return EXIT_ERROR
    return handler


def _cmd_check_model(args: argparse.Namespace) -> int:
    """真实调用一次模型验证配置(阶段 03 验收 / 阶段 10 排查用)。"""
    from pydantic import BaseModel

    from agent.providers import factory

    class Ping(BaseModel):
        ok: bool
        echo: str

    config = AgentConfig.from_env()
    provider = factory.create(config)
    result, usage = provider.generate_structured(
        system_prompt="你是连通性自检工具。",
        user_payload={"instruction": "返回 ok=true,echo 原样返回下方 text",
                      "text": "md-to-word-agent"},
        response_model=Ping,
    )
    get_logger("agent.check_model").info("模型连通性验证成功", context={
        "provider": config.model_provider, "model": config.model_name,
        "ok": result.ok, "echo": result.echo,
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
    })
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env(dry_run=args.dry_run).require(
        "supabase_url", "supabase_key")
    repository = SupabaseFeedbackRepository(
        config.supabase_url, config.supabase_key.get_secret_value())
    try:
        run_fetch(repository, config, args.feedback_id, Path(args.output))
    finally:
        repository.close()
    print("fetch 完成;分类及后续流程在阶段 04+ 实现。", file=sys.stderr)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.cli",
        description="MD To Word 反馈自动修复 Agent(spec: docs/AgentRequirements/)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="领取反馈并输出脱敏 task.json(Job A)")
    fetch.add_argument("--feedback-id", type=UUID, required=True,
                       help="Supabase feedback UUID")
    fetch.add_argument("--output", default="task.json")
    fetch.set_defaults(handler=_cmd_fetch)

    classify = sub.add_parser("classify", help="分类(阶段 04)")
    classify.add_argument("--task-file", required=True)
    classify.set_defaults(handler=_cmd_not_implemented("04"))

    repair = sub.add_parser("repair", help="生成测试与修复(阶段 06/07)")
    repair.add_argument("--task-file", required=True)
    repair.set_defaults(handler=_cmd_not_implemented("06/07"))

    validate = sub.add_parser("validate", help="独立复验(阶段 07)")
    validate.add_argument("--task-file", required=True)
    validate.add_argument("--test-patch", required=True)
    validate.add_argument("--fix-patch", required=True)
    validate.set_defaults(handler=_cmd_not_implemented("07"))

    finalize = sub.add_parser("finalize", help="回写状态(阶段 08)")
    finalize.add_argument("--result-file", required=True)
    finalize.set_defaults(handler=_cmd_not_implemented("08"))

    check_model = sub.add_parser("check-model", help="真实调用一次模型验证配置")
    check_model.set_defaults(handler=_cmd_check_model)

    run = sub.add_parser("run", help="本地一键执行(当前仅 fetch)")
    run.add_argument("--feedback-id", type=UUID, required=True)
    run.add_argument("--output", default="task.json")
    run.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    run.set_defaults(handler=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = get_logger("agent.cli")
    try:
        return args.handler(args)
    except ClaimUnavailableError as exc:
        log.error(exc.message, context={"error_code": exc.error_code})
        return EXIT_CLAIM_FAILED
    except AgentError as exc:
        log.error(exc.message, context={"error_code": exc.error_code})
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
