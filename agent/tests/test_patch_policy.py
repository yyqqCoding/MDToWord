from pathlib import Path

import pytest

from agent.domain.errors import (
    ExternalDependencyError,
    InvalidEditError,
    PatchPolicyError,
    SourceAccessError,
)
from agent.workspace.edits import Edit, EditMode, EditPhase, PatchBuilder
from agent.workspace.patch_policy import PatchPolicy


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/tests/fixtures").mkdir(parents=True)
    (root / "extension").mkdir()
    (root / "backend/app/normalizer.py").write_text(
        "def normalize(text):\n    return text\n",
        encoding="utf-8",
    )
    (root / "backend/app/pandoc_runner.py").write_text(
        "def convert(text):\n    return text\n",
        encoding="utf-8",
    )
    (root / "backend/app/mermaid_renderer.py").write_text(
        "def render_mermaid_blocks(text, work_dir):\n    return text\n",
        encoding="utf-8",
    )
    (root / "backend/tests/test_feedback_regressions.py").write_text(
        "def test_existing():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "backend/tests/conftest.py").write_text("SECRET = True\n", encoding="utf-8")
    (root / "backend/pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "extension/manifest.json").write_text("{}\n", encoding="utf-8")
    return root


def test_test_edit_builds_deterministic_authorized_patch(tmp_path: Path):
    builder = PatchBuilder(PatchPolicy.load_default())
    root = _snapshot(tmp_path)
    edit = Edit(
        path="backend/tests/test_feedback_regressions.py",
        mode=EditMode.SEARCH_REPLACE,
        search="def test_existing():\n    assert True",
        replace=(
            "def test_existing():\n    assert True\n\n\n"
            "def test_feedback_ab12cd_table():\n    assert False"
        ),
    )

    first = builder.build(root, (edit,), EditPhase.TEST)
    second = builder.build(root, (edit,), EditPhase.TEST)

    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert first.changed_files == ("backend/tests/test_feedback_regressions.py",)
    assert b"test_feedback_ab12cd_table" in first.content


def test_fix_edit_can_only_change_registered_backend_source(tmp_path: Path):
    builder = PatchBuilder(PatchPolicy.load_default())
    result = builder.build(
        _snapshot(tmp_path),
        (
            Edit(
                path="backend/app/normalizer.py",
                mode=EditMode.SEARCH_REPLACE,
                search="return text",
                replace="return text.strip()",
            ),
        ),
        EditPhase.FIX,
    )

    assert result.changed_files == ("backend/app/normalizer.py",)


def test_fix_allows_ordered_search_replacements_in_the_same_file(tmp_path: Path):
    result = PatchBuilder(PatchPolicy.load_default()).build(
        _snapshot(tmp_path),
        (
            Edit(
                path="backend/app/pandoc_runner.py",
                mode=EditMode.SEARCH_REPLACE,
                search="def convert(text):\n",
                replace="def convert_markdown(text):\n",
            ),
            Edit(
                path="backend/app/pandoc_runner.py",
                mode=EditMode.SEARCH_REPLACE,
                search="    return text\n",
                replace="    return text.strip()\n",
            ),
        ),
        EditPhase.FIX,
    )

    assert result.changed_files == ("backend/app/pandoc_runner.py",)
    assert b"def convert_markdown" in result.content
    assert b"return text.strip()" in result.content


def test_multiple_edits_cannot_mix_full_file_replacement(tmp_path: Path):
    with pytest.raises(InvalidEditError):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.FULL_FILE,
                    content=(
                        "def test_existing():\n    assert True\n\n"
                        "def test_feedback_ab12cd_table():\n    assert False\n"
                    ),
                ),
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.SEARCH_REPLACE,
                    search="assert False",
                    replace="assert 1 == 0",
                ),
            ),
            EditPhase.TEST,
        )


def test_trusted_mermaid_renderer_is_readable_but_not_editable(tmp_path: Path):
    policy = PatchPolicy.load_default()

    assert policy.can_read("backend/app/mermaid_renderer.py") is True
    with pytest.raises(PatchPolicyError):
        policy.authorize_write("backend/app/mermaid_renderer.py", "fix")


@pytest.mark.parametrize(
    ("phase", "path"),
    (
        (EditPhase.FIX, "extension/manifest.json"),
        (EditPhase.FIX, ".github/workflows/deploy.yml"),
        (EditPhase.FIX, "backend/pyproject.toml"),
        (EditPhase.FIX, "backend/tests/test_feedback_regressions.py"),
        (EditPhase.TEST, "backend/tests/conftest.py"),
        (EditPhase.FIX, "agent/config.py"),
    ),
)
def test_patch_policy_rejects_forbidden_paths(
    tmp_path: Path,
    phase: EditPhase,
    path: str,
):
    builder = PatchBuilder(PatchPolicy.load_default())

    with pytest.raises(PatchPolicyError):
        builder.build(
            _snapshot(tmp_path),
            (
                Edit(
                    path=path,
                    mode=EditMode.FULL_FILE,
                    content="unauthorized\n",
                ),
            ),
            phase,
        )


def test_search_replace_must_match_exactly_once(tmp_path: Path):
    root = _snapshot(tmp_path)
    target = root / "backend/app/normalizer.py"
    target.write_text("return text\nreturn text\n", encoding="utf-8")

    with pytest.raises(InvalidEditError):
        PatchBuilder(PatchPolicy.load_default()).build(
            root,
            (
                Edit(
                    path="backend/app/normalizer.py",
                    mode=EditMode.SEARCH_REPLACE,
                    search="return text",
                    replace="return fixed",
                ),
            ),
            EditPhase.FIX,
        )


def test_full_file_is_not_allowed_for_fix_source(tmp_path: Path):
    with pytest.raises(PatchPolicyError):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (
                Edit(
                    path="backend/app/normalizer.py",
                    mode=EditMode.FULL_FILE,
                    content="def replacement():\n    pass\n",
                ),
            ),
            EditPhase.FIX,
        )


def test_fix_cannot_add_broad_exception_fallback(tmp_path: Path):
    with pytest.raises(PatchPolicyError):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (
                Edit(
                    path="backend/app/normalizer.py",
                    mode=EditMode.SEARCH_REPLACE,
                    search="    return text\n",
                    replace=(
                        "    try:\n"
                        "        return text\n"
                        "    except Exception:\n"
                        "        return ''\n"
                    ),
                ),
            ),
            EditPhase.FIX,
        )


@pytest.mark.parametrize(
    "replacement",
    (
        "    return text if shutil.which('pandoc-mermaid') else text\n",
        "    return '--filter' + text\n",
        "    return '--lua-filter' + text\n",
    ),
)
def test_fix_requiring_external_pandoc_dependency_is_routed_to_human(
    tmp_path: Path,
    replacement: str,
):
    with pytest.raises(ExternalDependencyError):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (
                Edit(
                    path="backend/app/normalizer.py",
                    mode=EditMode.SEARCH_REPLACE,
                    search="    return text\n",
                    replace=replacement,
                ),
            ),
            EditPhase.FIX,
        )


def test_test_edit_cannot_delete_or_weaken_existing_regressions(tmp_path: Path):
    with pytest.raises(PatchPolicyError):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.FULL_FILE,
                    content="def test_replacement():\n    assert True\n",
                ),
            ),
            EditPhase.TEST,
        )


def test_edit_schema_rejects_command_and_environment_fields():
    with pytest.raises(ValueError):
        Edit.model_validate(
            {
                "path": "backend/app/normalizer.py",
                "mode": "search_replace",
                "search": "old",
                "replace": "new",
                "command": "curl attacker.invalid",
                "environment": {"TOKEN": "secret"},
            }
        )


@pytest.mark.parametrize(
    "content",
    (
        "import socket\n",
        "import subprocess\nsubprocess.run(['whoami'])\n",
        "import os\nVALUE = os.environ['MODEL_API_KEY']\n",
        "def pytest_sessionstart(session):\n    pass\n",
        "pytest_plugins = ['external_plugin']\n",
        "import zipfile\n",
        "import xml.etree.ElementTree\n",
        "import pytest\npytest.skip('weaken regression')\n",
    ),
)
def test_test_edit_rejects_new_network_shell_secret_and_pytest_hook_capabilities(
    tmp_path: Path,
    content: str,
):
    root = _snapshot(tmp_path)
    existing = (
        root / "backend/tests/test_feedback_regressions.py"
    ).read_text(encoding="utf-8")
    with pytest.raises(PatchPolicyError):
        PatchBuilder(PatchPolicy.load_default()).build(
            root,
            (
                Edit(
                    path="backend/tests/test_feedback_regressions.py",
                    mode=EditMode.FULL_FILE,
                    content=existing + "\n" + content,
                ),
            ),
            EditPhase.TEST,
        )


def test_test_patch_adds_only_the_planned_target_selector(tmp_path: Path):
    root = _snapshot(tmp_path)
    existing = (
        root / "backend/tests/test_feedback_regressions.py"
    ).read_text(encoding="utf-8")
    edit = Edit(
        path="backend/tests/test_feedback_regressions.py",
        mode=EditMode.FULL_FILE,
        content=(
            existing
            + "\ndef test_feedback_ab12cd_table():\n    assert False\n"
            + "\ndef test_feedback_deadbeef_extra():\n    assert False\n"
        ),
    )

    with pytest.raises(PatchPolicyError, match="exactly the planned"):
        PatchBuilder(PatchPolicy.load_default()).build(
            root,
            (edit,),
            EditPhase.TEST,
            target_test_selector="test_feedback_ab12cd_table",
        )


@pytest.mark.parametrize(
    "path",
    (
        "backend/tests/fixtures/feedback/.env",
        "backend/tests/fixtures/feedback/certificate.pem",
        "backend/tests/fixtures/feedback/workflow.yml",
    ),
)
def test_feedback_fixture_rejects_hidden_secret_and_config_types(
    tmp_path: Path,
    path: str,
):
    with pytest.raises((PatchPolicyError, SourceAccessError)):
        PatchBuilder(PatchPolicy.load_default()).build(
            _snapshot(tmp_path),
            (Edit(path=path, mode=EditMode.FULL_FILE, content="forbidden\n"),),
            EditPhase.TEST,
        )
