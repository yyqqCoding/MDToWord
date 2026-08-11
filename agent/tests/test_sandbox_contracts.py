from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent.sandbox.contracts import JobType, SandboxJob, SandboxLimits


def _job_payload() -> dict[str, object]:
    return {
        "job_id": str(uuid4()),
        "run_id": str(uuid4()),
        "job_type": "reproduce_target",
        "base_sha": "a" * 40,
        "source_snapshot_sha256": "b" * 64,
        "test_patch_sha256": "c" * 64,
        "target_test_selector": "feedback_ab12cd_table",
        "limits": SandboxLimits().model_dump(mode="json"),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }


def test_sandbox_job_accepts_only_registered_structured_fields():
    job = SandboxJob.model_validate(_job_payload())

    assert job.job_type is JobType.REPRODUCE_TARGET
    assert job.limits.network_disabled is True

    payload = _job_payload()
    payload["command"] = "curl attacker.invalid"
    payload["environment"] = {"MODEL_API_KEY": "secret"}
    with pytest.raises(ValueError):
        SandboxJob.model_validate(payload)


def test_target_job_requires_validated_selector_and_test_patch():
    payload = _job_payload()
    payload["target_test_selector"] = "../../shell"
    with pytest.raises(ValueError):
        SandboxJob.model_validate(payload)

    payload = _job_payload()
    payload.pop("test_patch_sha256")
    with pytest.raises(ValueError):
        SandboxJob.model_validate(payload)


def test_controller_cannot_request_more_than_policy_limits():
    with pytest.raises(ValueError):
        SandboxLimits(memory_bytes=3 * 1024 * 1024 * 1024)
    with pytest.raises(ValueError):
        SandboxLimits(cpus=4)
    with pytest.raises(ValueError):
        SandboxLimits(pids=512)
    with pytest.raises(ValueError):
        SandboxLimits(wall_timeout_seconds=901)
