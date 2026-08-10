import asyncio

import pytest
from psycopg.conninfo import conninfo_to_dict

from agent.checkpoint import build_checkpoint_conninfo, configure_checkpoint_connection
from agent.domain.errors import CheckpointConfigurationError


def test_checkpoint_conninfo_does_not_rely_on_pooler_startup_options():
    conninfo = build_checkpoint_conninfo(
        "postgresql://agent:secret@localhost:5432/postgres",
        "agent_runtime",
    )
    parsed = conninfo_to_dict(conninfo)

    assert "options" not in parsed
    assert parsed["dbname"] == "postgres"


class FakeCursor:
    def __init__(self, *, public_tables=()):
        self.public_tables = list(public_tables)
        self.executed = []
        self._result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, query, parameters=None):
        self.executed.append((query, parameters))
        query_text = str(query).lower()
        if "information_schema.tables" in query_text:
            self._result = [{"table_name": name} for name in self.public_tables]
        elif "current_schema" in query_text:
            self._result = [{"current_schema": "agent_runtime"}]

    async def fetchall(self):
        return list(self._result or [])

    async def fetchone(self):
        return self._result[0]


class FakeConnection:
    def __init__(self, *, public_tables=()):
        self.fake_cursor = FakeCursor(public_tables=public_tables)

    def cursor(self):
        return self.fake_cursor


def test_checkpoint_connection_sets_and_verifies_schema_after_connect():
    connection = FakeConnection()

    asyncio.run(configure_checkpoint_connection(connection, "agent_runtime"))

    assert len(connection.fake_cursor.executed) == 3


def test_checkpoint_connection_refuses_public_checkpoint_tables():
    connection = FakeConnection(public_tables=("checkpoints",))

    with pytest.raises(CheckpointConfigurationError):
        asyncio.run(configure_checkpoint_connection(connection, "agent_runtime"))

    assert len(connection.fake_cursor.executed) == 1
