import hashlib
from pathlib import Path

import pytest

from agent.domain.errors import PatchPolicyError
from agent.workspace.edits import Edit, EditMode, EditPhase, PatchBuilder
from agent.workspace.patch_policy import PatchPolicy
from agent.workspace.validation import compose_validated_patch, materialize_validated_files


def _snapshot(root: Path) -> Path:
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/tests").mkdir(parents=True)
    (root / "backend/app/normalizer.py").write_text(
        "def normalize(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    (root / "backend/tests/test_feedback_regressions.py").write_text(
        "def test_existing():\n    assert True\n",
        encoding="utf-8",
    )
    return root


def _patches(root: Path) -> tuple[bytes, bytes]:
    builder = PatchBuilder(PatchPolicy.load_default())
    test_patch = builder.build(
        root,
        (
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.SEARCH_REPLACE,
                search="def test_existing():\n    assert True\n",
                replace=(
                    "def test_existing():\n    assert True\n\n"
                    "def test_feedback_ab12cd34_normalize():\n    assert True\n"
                ),
            ),
        ),
        EditPhase.TEST,
        target_test_selector="test_feedback_ab12cd34_normalize",
    )
    fix_patch = builder.build(
        root,
        (
            Edit(
                path="backend/app/normalizer.py",
                mode=EditMode.SEARCH_REPLACE,
                search="    return value\n",
                replace="    return value.strip()\n",
            ),
        ),
        EditPhase.FIX,
    )
    return test_patch.content, fix_patch.content


def test_combined_validated_patch_has_stable_content_hash(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    test_patch, fix_patch = _patches(root)

    first = compose_validated_patch(root, test_patch, fix_patch)
    second = compose_validated_patch(root, test_patch, fix_patch)

    assert first == second
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert first.changed_files == (
        "backend/app/normalizer.py",
        "backend/tests/test_feedback_regressions.py",
    )


def test_combiner_rejects_fix_patch_touching_test_patch_file(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    test_patch, _ = _patches(root)

    with pytest.raises(PatchPolicyError, match="disjoint"):
        compose_validated_patch(root, test_patch, test_patch)


def test_materialized_publication_files_match_validated_patch(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    test_patch, fix_patch = _patches(root)
    validated = compose_validated_patch(root, test_patch, fix_patch)

    files = materialize_validated_files(
        root,
        validated.content,
        expected_sha256=validated.sha256,
        expected_files=validated.changed_files,
    )

    assert tuple(item.path for item in files) == validated.changed_files
    by_path = {item.path: item.content for item in files}
    assert by_path["backend/app/normalizer.py"] == (
        b"def normalize(value: str) -> str:\n    return value.strip()\n"
    )
