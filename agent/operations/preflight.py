"""在不领取反馈的前提下检查 Controller 配置和数据库待处理状态。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

import psycopg

from agent.config import AgentConfig
from agent.domain.errors import ConfigurationError
from agent.repair_agent.models import build_chat_model_bundle


_FEEDBACK_ATTENTION_STATUSES = (
    "pending",
    "claimed",
    "gating",
    "issue_required",
    "publishing_issue",
    "reproducing",
    "repairing",
    "validating",
    "validated",
    "publishing",
)
_RESUMABLE_RUN_STATUSES = (
    "created",
    "gating",
    "publishing_issue",
    "preparing_source",
    "reproducing",
    "repairing",
    "validating",
    "publishing",
)


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...]) -> None: ...

    def fetchall(self) -> list[tuple[object, int]]: ...

    def __enter__(self) -> "_Cursor": ...

    def __exit__(self, *args: object) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def __enter__(self) -> "_Connection": ...

    def __exit__(self, *args: object) -> None: ...


def validate_controller_config(config: AgentConfig) -> str:
    """一次性验证生产 D→E→F 配置，返回仅供数据库连接使用的 DSN。"""

    database_url = config.require_database_url()
    config.require_model_settings()
    build_chat_model_bundle(config)
    config.require_langfuse_settings()
    config.require_stage_c_controller_settings()
    config.require_stage_f_publisher_settings()
    return database_url


def collect_database_state(
    database_url: str,
    *,
    connect: Callable[[str], _Connection] = psycopg.connect,
) -> dict[str, dict[str, int]]:
    """只统计活动状态，不读取反馈正文、联系方式或任何 Artifact。"""

    with connect(database_url) as connection:
        with connection.cursor() as cursor:
            feedback = _count_statuses(
                cursor,
                table="public.feedback",
                statuses=_FEEDBACK_ATTENTION_STATUSES,
            )
            runs = _count_statuses(
                cursor,
                table="public.agent_runs",
                statuses=_RESUMABLE_RUN_STATUSES,
            )
    return {
        "feedback_requiring_attention": feedback,
        "resumable_runs": runs,
    }


def _count_statuses(
    cursor: _Cursor,
    *,
    table: str,
    statuses: tuple[str, ...],
) -> dict[str, int]:
    # table 只来自本模块常量；状态值仍通过参数传递，避免把外部文本拼进 SQL。
    cursor.execute(
        f"""
        select status, count(*)
        from {table}
        where status = any(%s)
        group by status
        order by status
        """,
        (list(statuses),),
    )
    return {str(status): int(count) for status, count in cursor.fetchall()}


def main() -> int:
    try:
        config = AgentConfig.from_env()
        database_url = validate_controller_config(config)
        result: dict[str, Any] = {
            "config": "controller_config_ready",
            **collect_database_state(database_url),
        }
    except ConfigurationError as exc:
        # 配置错误只包含稳定配置名；其他外部异常不输出可能携带 DSN 的消息。
        print(json.dumps({"error": exc.error_code, "message": str(exc)}, sort_keys=True))
        return 2
    except Exception as exc:  # pragma: no cover - 真实数据库边界
        print(
            json.dumps(
                {"error": "preflight_failed", "type": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
