from uuid import uuid4

from agent.domain import Feedback, TaskArtifact, build_fingerprint


def test_fingerprint_is_stable():
    a = build_fingerprint("bug", "# 标题\n\n内容", "描述")
    b = build_fingerprint("bug", "# 标题\n\n内容", "描述")
    assert a == b
    assert len(a) == 64


def test_fingerprint_normalizes_crlf_and_whitespace():
    lf = build_fingerprint("bug", "# 标题\n内容", "描述")
    crlf = build_fingerprint("bug", "# 标题\r\n内容", "描述\r\n")
    padded = build_fingerprint(" BUG ", "  # 标题\n内容  ", "描述")
    assert lf == crlf == padded


def test_fingerprint_differs_on_any_field():
    base = build_fingerprint("bug", "md", "desc")
    assert base != build_fingerprint("suggestion", "md", "desc")
    assert base != build_fingerprint("bug", "md2", "desc")
    assert base != build_fingerprint("bug", "md", "desc2")


def test_task_artifact_never_contains_contact(tmp_path):
    feedback = Feedback(
        id=uuid4(), feedback_type="bug", markdown_content="# md",
        description="表格丢了", contact="user@example.com",
    )
    artifact = TaskArtifact.from_feedback(feedback, claim_token=uuid4())
    path = tmp_path / "task.json"
    artifact.write(path)

    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "contact" not in data
    assert "user@example.com" not in path.read_text(encoding="utf-8")
    assert data["fingerprint"] == feedback.fingerprint()

    # 回读一致
    loaded = TaskArtifact.read(path)
    assert loaded.feedback_id == feedback.id
