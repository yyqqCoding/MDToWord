from pathlib import Path

import pytest

from agent.domain.errors import SourceAccessError
from agent.tools.source import PathScope, SourceReader


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/tests").mkdir(parents=True)
    (root / "extension").mkdir()
    (root / "backend/app/normalizer.py").write_text(
        "def normalize_markdown(text):\n    return text\n",
        encoding="utf-8",
    )
    (root / "backend/app/pandoc_runner.py").write_text(
        "def convert_markdown_to_docx(text):\n    return text\n",
        encoding="utf-8",
    )
    (root / "backend/tests/test_normalizer.py").write_text(
        "def test_normalize_markdown():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "backend/pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("trusted rules\n", encoding="utf-8")
    (root / "README.md").write_text("project summary\n", encoding="utf-8")
    (root / "extension/secret.txt").write_text("must not leak\n", encoding="utf-8")
    return root


def test_read_source_file_returns_only_requested_allowed_lines(tmp_path: Path):
    reader = SourceReader(_snapshot(tmp_path))

    result = reader.read_source_file(
        "backend/app/normalizer.py",
        start_line=1,
        end_line=1,
    )

    assert result.path == "backend/app/normalizer.py"
    assert result.start_line == 1
    assert result.end_line == 1
    assert result.content == "def normalize_markdown(text):"


def test_list_readable_paths_contains_only_existing_allowlisted_files(tmp_path: Path):
    paths = SourceReader(_snapshot(tmp_path)).list_readable_paths()

    assert paths == (
        "AGENTS.md",
        "README.md",
        "backend/app/normalizer.py",
        "backend/app/pandoc_runner.py",
        "backend/pyproject.toml",
        "backend/tests/test_normalizer.py",
    )
    assert "extension/secret.txt" not in paths


@pytest.mark.parametrize(
    "path",
    (
        "../.env",
        "/etc/passwd",
        r"C:\\Users\\secret.txt",
        "extension/secret.txt",
        ".env",
        "backend/tests/../app/normalizer.py",
    ),
)
def test_read_source_file_rejects_untrusted_paths(tmp_path: Path, path: str):
    reader = SourceReader(_snapshot(tmp_path))

    with pytest.raises(SourceAccessError):
        reader.read_source_file(path, start_line=1, end_line=20)


def test_read_source_file_rejects_symlink_even_when_target_is_allowed(tmp_path: Path):
    root = _snapshot(tmp_path)
    link = root / "backend/tests/test_link.py"
    try:
        link.symlink_to(root / "backend/app/normalizer.py")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(SourceAccessError):
        SourceReader(root).read_source_file(
            "backend/tests/test_link.py",
            start_line=1,
            end_line=20,
        )


def test_search_source_is_literal_scoped_and_bounded(tmp_path: Path):
    reader = SourceReader(_snapshot(tmp_path))

    results = reader.search_source(
        "normalize_markdown",
        path_scope=PathScope.BACKEND,
        max_results=10,
    )

    assert [(item.path, item.line) for item in results] == [
        ("backend/app/normalizer.py", 1),
        ("backend/tests/test_normalizer.py", 1),
    ]
    assert all("must not leak" not in item.snippet for item in results)


def test_search_source_does_not_interpret_regular_expressions(tmp_path: Path):
    reader = SourceReader(_snapshot(tmp_path))

    assert reader.search_source(
        ".*",
        path_scope=PathScope.BACKEND,
        max_results=10,
    ) == ()
