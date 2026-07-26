"""fetch 服务流程测试(FakeFeedbackRepository,不触网)。"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest

from agent.cli import EXIT_NO_WORK, build_parser, run_fetch
from agent.config import AgentConfig
from agent.domain import build_fingerprint
from agent.exceptions import ClaimUnavailableError, FeedbackNotFoundError
from agent.tests.fakes import FakeFeedbackRepository


def make_config(**overrides):
    return AgentConfig.from_env({}, model_name="test-model", **overrides)


def test_cli_help_and_missing_feedback_id():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["fetch"])  # 缺 --feedback-id
    assert exc_info.value.code == 2


def test_fetch_writes_redacted_task_and_creates_run(tmp_path):
    repository = FakeFeedbackRepository()
    feedback_id = repository.add_feedback(
        markdown_content="# 表格丢了", description="导出后表格变文本",
        contact="user@example.com")
    output = tmp_path / "task.json"

    artifact = run_fetch(repository, make_config(), feedback_id, output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert "contact" not in data
    assert "user@example.com" not in output.read_text(encoding="utf-8")
    assert data["feedback_id"] == str(feedback_id)

    # 领取生效 + run 记录已创建 + 指纹已回写
    row = repository.feedback[feedback_id]
    assert row["status"] == "claimed"
    assert row["content_fingerprint"] == artifact.fingerprint
    assert len(repository.runs) == 1
    run = next(iter(repository.runs.values()))
    assert run["feedback_id"] == feedback_id
    assert run["model"] == "test-model"
    assert str(artifact.agent_run_id) == str(run["id"])


def test_second_claim_fails(tmp_path):
    repository = FakeFeedbackRepository()
    feedback_id = repository.add_feedback()
    run_fetch(repository, make_config(), feedback_id, tmp_path / "a.json")
    with pytest.raises(ClaimUnavailableError):
        run_fetch(repository, make_config(), feedback_id, tmp_path / "b.json")


def test_stale_claim_recoverable(tmp_path):
    repository = FakeFeedbackRepository()
    feedback_id = repository.add_feedback(
        status="claimed",
        claimed_at=repository.now - timedelta(hours=3))
    run_fetch(repository, make_config(), feedback_id, tmp_path / "task.json")
    assert repository.feedback[feedback_id]["attempt_count"] == 1


def test_attempt_cap_blocks_claim(tmp_path):
    repository = FakeFeedbackRepository()
    feedback_id = repository.add_feedback(attempt_count=3)
    with pytest.raises(ClaimUnavailableError):
        run_fetch(repository, make_config(), feedback_id, tmp_path / "task.json")


def test_duplicate_fingerprint_marks_duplicate_and_exits(tmp_path):
    repository = FakeFeedbackRepository()
    fingerprint = build_fingerprint("bug", "# 相同内容", "相同描述")
    repository.add_feedback(
        markdown_content="# 相同内容", description="相同描述",
        status="pr_opened", content_fingerprint=fingerprint)
    new_id = repository.add_feedback(
        markdown_content="# 相同内容", description="相同描述")

    with pytest.raises(SystemExit) as exc_info:
        run_fetch(repository, make_config(), new_id, tmp_path / "task.json")

    assert exc_info.value.code == EXIT_NO_WORK
    assert repository.feedback[new_id]["status"] == "duplicate"
    assert not (tmp_path / "task.json").exists()


def test_missing_feedback_raises(tmp_path):
    repository = FakeFeedbackRepository()
    with pytest.raises(FeedbackNotFoundError):
        run_fetch(repository, make_config(), uuid4(), tmp_path / "task.json")
