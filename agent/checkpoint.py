import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agent.domain.errors import CheckpointConfigurationError


_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


def build_checkpoint_conninfo(database_url: str, schema: str) -> str:
    """校验 Schema 名称并规范化 DSN；search_path 在连接建立后显式设置。"""

    if not _SCHEMA_NAME.fullmatch(schema):
        raise ValueError("checkpoint schema must be a lowercase SQL identifier")
    return make_conninfo(database_url)


async def configure_checkpoint_connection(connection, schema: str) -> None:
    """在实际数据库会话内固定并验证 Schema，兼容忽略启动参数的 Pooler。"""

    if not _SCHEMA_NAME.fullmatch(schema):
        raise ValueError("checkpoint schema must be a lowercase SQL identifier")
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            select table_name
            from information_schema.tables
            where table_schema = 'public' and table_name = any(%s)
            """,
            (list(_CHECKPOINT_TABLES),),
        )
        public_tables = [_first_value(row) for row in await cursor.fetchall()]
        if public_tables:
            # 发现误建表时必须先人工检查和清理，不能由启动流程自动删除数据。
            raise CheckpointConfigurationError(
                "public checkpoint tables must be reviewed and removed"
            )

        await cursor.execute(
            sql.SQL("set search_path to {}").format(sql.Identifier(schema))
        )
        await cursor.execute("select current_schema()")
        current_schema = _first_value(await cursor.fetchone())
        if current_schema != schema:
            raise CheckpointConfigurationError(
                "checkpoint connection did not activate the private schema"
            )


def _first_value(row):
    if isinstance(row, Mapping):
        return next(iter(row.values()))
    return row[0]


@asynccontextmanager
async def open_postgres_checkpointer(
    database_url: str,
    schema: str,
    *,
    setup: bool = False,
) -> AsyncIterator[AsyncPostgresSaver]:
    conninfo = build_checkpoint_conninfo(database_url, schema)
    async with AsyncPostgresSaver.from_conn_string(conninfo) as checkpointer:
        await configure_checkpoint_connection(checkpointer.conn, schema)
        # setup 会创建/升级第三方表，只允许显式管理命令调用。
        if setup:
            await checkpointer.setup()
        yield checkpointer
