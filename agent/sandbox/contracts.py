"""定义 Controller 与 Sandbox Worker 之间唯一允许的结构化数据契约。"""

import base64
import binascii
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_PATCH_BYTES = 200_000


class JobType(StrEnum):
    REPRODUCE_TARGET = "reproduce_target"
    VALIDATE_TARGET = "validate_target"
    VALIDATE_FULL = "validate_full"
    COMPILE_PATCH = "compile_patch"


class SandboxStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SECURITY_REJECTED = "security_rejected"


class SandboxLimits(BaseModel):
    """固定沙箱资源上限；调用方只能在安全范围内收紧，不能关闭隔离。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=128 * 1024 * 1024,
        le=2 * 1024 * 1024 * 1024,
    )
    cpus: float = Field(default=2.0, ge=0.25, le=2.0)
    pids: int = Field(default=256, ge=16, le=256)
    wall_timeout_seconds: int = Field(default=900, ge=1, le=900)
    network_disabled: Literal[True] = True
    stdout_bytes: int = Field(default=4096, ge=256, le=4096)
    stderr_bytes: int = Field(default=4096, ge=256, le=4096)


class SandboxJob(BaseModel):
    """不包含命令或环境变量的任务描述，由 JobType 映射到固定执行命令。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    run_id: UUID
    job_type: JobType
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fix_patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    target_test_selector: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]{1,80}$",
    )
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_job_artifacts(self) -> "SandboxJob":
        if self.job_type is JobType.REPRODUCE_TARGET:
            if self.test_patch_sha256 is None or self.fix_patch_sha256 is not None:
                raise ValueError("reproduce_target requires only a test patch")
            if self.target_test_selector is None:
                raise ValueError("target job requires a test selector")
        elif self.job_type in {JobType.VALIDATE_TARGET, JobType.VALIDATE_FULL}:
            if self.test_patch_sha256 is None or self.fix_patch_sha256 is None:
                raise ValueError("validation requires test and fix patches")
            if self.target_test_selector is None:
                raise ValueError("validation requires a test selector")
        elif self.test_patch_sha256 is None and self.fix_patch_sha256 is None:
            raise ValueError("compile_patch requires at least one patch")
        return self


class JUnitSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tests: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class ResourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_limit_bytes: int = Field(ge=0)
    cpu_limit: float = Field(ge=0)
    pids_limit: int = Field(ge=0)


class SandboxResult(BaseModel):
    """Worker 返回的有界执行摘要，不携带任意文件或无界日志。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    status: SandboxStatus
    exit_code: int | None = None
    timed_out: bool = False
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: int = Field(ge=0)
    junit_summary: JUnitSummary | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    docx_summary: dict[str, object] = Field(default_factory=dict)
    workspace_diff_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resource_summary: ResourceSummary | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,80}$")

    @field_validator("stdout_tail", "stderr_tail")
    @classmethod
    def limit_output_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("sandbox output exceeds 4 KiB")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> "SandboxResult":
        if self.finished_at < self.started_at:
            raise ValueError("sandbox result times are invalid")
        if self.status is SandboxStatus.TIMED_OUT and not self.timed_out:
            raise ValueError("timed_out status requires timed_out=true")
        return self


class _WireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: SandboxJob
    source_archive_b64: str = Field(min_length=1, max_length=70_000_000)
    test_patch_b64: str | None = Field(default=None, max_length=300_000)
    fix_patch_b64: str | None = Field(default=None, max_length=300_000)


class SandboxArtifacts(BaseModel):
    """任务元数据及经 SHA-256 绑定的源码、测试和修复 Artifact。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job: SandboxJob
    source_archive: bytes
    test_patch: bytes | None = None
    fix_patch: bytes | None = None

    @model_validator(mode="after")
    def validate_artifact_hashes(self) -> "SandboxArtifacts":
        if not self.source_archive or len(self.source_archive) > _MAX_ARCHIVE_BYTES:
            raise ValueError("source archive size is invalid")
        _match_hash(
            self.source_archive,
            self.job.source_snapshot_sha256,
            "source archive",
        )
        _validate_optional_patch(
            self.test_patch,
            self.job.test_patch_sha256,
            "test patch",
        )
        _validate_optional_patch(
            self.fix_patch,
            self.job.fix_patch_sha256,
            "fix patch",
        )
        return self

    def to_wire(self) -> dict[str, object]:
        return {
            "job": self.job.model_dump(mode="json"),
            "source_archive_b64": base64.b64encode(self.source_archive).decode("ascii"),
            "test_patch_b64": _encode_optional(self.test_patch),
            "fix_patch_b64": _encode_optional(self.fix_patch),
        }

    @classmethod
    def from_wire(cls, payload: object) -> "SandboxArtifacts":
        request = _WireRequest.model_validate(payload)
        return cls(
            job=request.job,
            source_archive=_decode(request.source_archive_b64, "source archive"),
            test_patch=_decode_optional(request.test_patch_b64, "test patch"),
            fix_patch=_decode_optional(request.fix_patch_b64, "fix patch"),
        )

    def request_fingerprint(self) -> str:
        # 指纹只绑定规范化 Job；Artifact 内容已由 Job 中的 SHA-256 间接绑定。
        canonical = json.dumps(
            self.job.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def _validate_optional_patch(
    content: bytes | None,
    expected_hash: str | None,
    label: str,
) -> None:
    if (content is None) != (expected_hash is None):
        raise ValueError(f"{label} presence does not match job")
    if content is None or expected_hash is None:
        return
    if not content or len(content) > _MAX_PATCH_BYTES:
        raise ValueError(f"{label} size is invalid")
    _match_hash(content, expected_hash, label)


def _match_hash(content: bytes, expected_hash: str, label: str) -> None:
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError(f"{label} hash does not match job")


def _decode(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} is not valid base64") from exc


def _decode_optional(value: str | None, label: str) -> bytes | None:
    return None if value is None else _decode(value, label)


def _encode_optional(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")
