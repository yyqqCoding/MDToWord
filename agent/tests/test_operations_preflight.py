from contextlib import AbstractContextManager

from agent.operations.preflight import collect_database_state


class _FakeCursor(AbstractContextManager):
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self._rows: list[tuple[object, int]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.queries.append((query, params))
        self._rows = (
            [("pending", 2), ("repairing", 1)]
            if "public.feedback" in query
            else [("publishing", 1)]
        )

    def fetchall(self) -> list[tuple[object, int]]:
        return self._rows

    def __exit__(self, *args: object) -> None:
        return None


class _FakeConnection(AbstractContextManager):
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def __exit__(self, *args: object) -> None:
        return None


def test_preflight_reports_only_status_counts() -> None:
    cursor = _FakeCursor()

    result = collect_database_state(
        "postgresql://secret-value",
        connect=lambda _: _FakeConnection(cursor),
    )

    assert result == {
        "feedback_requiring_attention": {"pending": 2, "repairing": 1},
        "resumable_runs": {"publishing": 1},
    }
    assert len(cursor.queries) == 2
    assert all("markdown_content" not in query for query, _ in cursor.queries)
    assert all("contact" not in query for query, _ in cursor.queries)
