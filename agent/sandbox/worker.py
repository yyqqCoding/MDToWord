"""认证、校验并幂等执行 Sandbox Job 的独立 Worker 核心。"""

import hmac
import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.errors import (
    AgentError,
    PatchPolicyError,
    SandboxAuthenticationError,
    SandboxExecutionError,
    SandboxJobConflictError,
    SourceSnapshotError,
)
from agent.sandbox.contracts import (
    SandboxArtifacts,
    SandboxJob,
    SandboxResult,
    SandboxStatus,
)


class SandboxRunner(Protocol):
    def execute(self, job: SandboxJob, artifacts: SandboxArtifacts) -> SandboxResult: ...


class _StoredJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: SandboxResult


class FileJobStore:
    """按 job_id 原子保存结果，使 Worker 重启后仍能幂等返回。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def read(self, job_id: UUID) -> _StoredJob | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return _StoredJob.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise SandboxExecutionError("stored sandbox result is invalid") from exc

    def write(self, job_id: UUID, stored: _StoredJob) -> None:
        path = self._path(job_id)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._root,
                prefix=f".{job_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(stored.model_dump_json())
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
            path.chmod(0o600)
        except OSError as exc:
            raise SandboxExecutionError("sandbox result could not be stored") from exc
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def _path(self, job_id: UUID) -> Path:
        return self._root / f"{job_id}.json"


class SandboxWorker:
    """在认证边界内串行领取同一 Job，并持久化所有终态结果。"""

    def __init__(
        self,
        *,
        credential: str,
        runner: SandboxRunner,
        store: FileJobStore,
    ) -> None:
        if not credential:
            raise SandboxAuthenticationError("sandbox worker credential is empty")
        self._expected_authorization = f"Bearer {credential}"
        self._runner = runner
        self._store = store
        self._lock = threading.RLock()

    def authenticate(self, authorization: str | None) -> None:
        """在解析请求体前也可调用，避免未认证请求消耗大对象解析资源。"""

        supplied = authorization or ""
        if not hmac.compare_digest(supplied, self._expected_authorization):
            raise SandboxAuthenticationError("sandbox worker authentication failed")

    def execute(
        self,
        authorization: str | None,
        payload: object,
        *,
        idempotency_key: str | None = None,
    ) -> SandboxResult:
        # 必须先认证再解析大体积 JSON/Base64，避免未授权请求消耗解析资源。
        self.authenticate(authorization)
        artifacts = SandboxArtifacts.from_wire(payload)
        if idempotency_key is not None and idempotency_key != str(artifacts.job.job_id):
            raise SandboxJobConflictError("idempotency key does not match job id")
        fingerprint = artifacts.request_fingerprint()

        # 锁覆盖“查询、执行、写入”，防止并发相同 Job 被实际执行两次。
        with self._lock:
            stored = self._store.read(artifacts.job.job_id)
            if stored is not None:
                if stored.request_fingerprint != fingerprint:
                    raise SandboxJobConflictError(
                        "job id was already used for a different request"
                    )
                return stored.result
            # expires_at 只阻止新的副作用；已执行结果必须能在响应丢失后幂等取回。
            if artifacts.job.expires_at <= datetime.now(UTC):
                raise ValueError("sandbox job has expired")
            try:
                result = self._runner.execute(artifacts.job, artifacts)
                if result.job_id != artifacts.job.job_id:
                    raise SandboxExecutionError("runner returned a mismatched job id")
            except (PatchPolicyError, SourceSnapshotError) as exc:
                # 输入违反源码/补丁边界属于安全拒绝，不混同普通测试失败。
                now = datetime.now(UTC)
                result = SandboxResult(
                    job_id=artifacts.job.job_id,
                    status=SandboxStatus.SECURITY_REJECTED,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    error_code=exc.error_code,
                )
            except AgentError as exc:
                now = datetime.now(UTC)
                result = SandboxResult(
                    job_id=artifacts.job.job_id,
                    status=SandboxStatus.FAILED,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    error_code=exc.error_code,
                )
            except Exception:  # pragma: no cover - Worker 最外层安全边界
                now = datetime.now(UTC)
                result = SandboxResult(
                    job_id=artifacts.job.job_id,
                    status=SandboxStatus.FAILED,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    error_code="sandbox_internal_error",
                )
            self._store.write(
                artifacts.job.job_id,
                _StoredJob(request_fingerprint=fingerprint, result=result),
            )
            return result
