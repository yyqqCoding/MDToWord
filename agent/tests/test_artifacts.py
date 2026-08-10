import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent.domain.enums import FeedbackType
from agent.domain.errors import InvalidArtifactPathError
from agent.domain.models import TaskArtifact
from agent.workspace.artifacts import ArtifactStore


def test_task_artifact_never_serializes_contact_or_secrets(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    run_id = uuid4()
    task = TaskArtifact(
        feedback_id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="$x$",
        description="导出公式失败",
        content_fingerprint="a" * 64,
    )

    artifact = store.write_task(run_id, task)
    payload = artifact.read_text(encoding="utf-8")

    assert artifact.name == "task.redacted.json"
    assert json.loads(payload)["description"] == "导出公式失败"
    assert "contact" not in payload
    assert "authorization" not in payload.lower()


def test_task_artifact_factory_drops_feedback_contact(tmp_path: Path):
    from agent.domain.models import FeedbackRecord

    feedback = FeedbackRecord(
        id=uuid4(),
        feedback_type=FeedbackType.BUG,
        markdown_content="$x$",
        description="导出公式失败",
        contact="user@example.com",
    )

    task = TaskArtifact.from_feedback(feedback)

    assert "contact" not in task.model_dump(mode="json")
    assert "user@example.com" not in repr(feedback)


def test_artifact_store_rejects_path_traversal(tmp_path: Path):
    store = ArtifactStore(tmp_path)

    with pytest.raises(InvalidArtifactPathError):
        store.path_for("../outside", "task.redacted.json")
