import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from agent.domain.errors import InvalidArtifactPathError
from agent.domain.gate import GateResult
from agent.domain.models import TaskArtifact


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_REF = re.compile(
    r"^artifact://(?P<run>[A-Za-z0-9][A-Za-z0-9._-]{0,127})/"
    r"(?P<filename>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_KNOWN_ARTIFACTS = frozenset(
    {
        "task.redacted.json",
        "gate.json",
        "reproduction-plan.json",
        "test.patch",
        "fix.patch",
        "validated.patch",
        "reproduction-junit.xml",
        "validation-junit.xml",
        "validation.json",
        "result.json",
    }
)


class ArtifactStore:
    """只允许架构登记的 Artifact 名称，并使用同目录原子替换写入。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, run_id: UUID | str, filename: str) -> Path:
        run_segment = str(run_id)
        if not _SAFE_SEGMENT.fullmatch(run_segment) or run_segment in {".", ".."}:
            raise InvalidArtifactPathError("run id is not a safe artifact path segment")
        if filename not in _KNOWN_ARTIFACTS:
            raise InvalidArtifactPathError("artifact filename is not registered")
        path = (self.root / run_segment / filename).resolve()
        # resolve 后再次确认父目录，阻止 .. 或符号链接把写入引到 Artifact 根目录外。
        if self.root not in path.parents:
            raise InvalidArtifactPathError("artifact path escapes the configured root")
        return path

    def write_task(self, run_id: UUID | str, task: TaskArtifact) -> Path:
        path = self.path_for(run_id, "task.redacted.json")
        content = json.dumps(
            task.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write(path, content)
        return path

    def write_task_ref(self, run_id: UUID | str, task: TaskArtifact) -> str:
        self.write_task(run_id, task)
        return self.ref_for(run_id, "task.redacted.json")

    def write_gate_ref(self, run_id: UUID | str, result: GateResult) -> str:
        path = self.path_for(run_id, "gate.json")
        content = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write(path, content)
        return self.ref_for(run_id, "gate.json")

    def read_task(self, reference: str) -> TaskArtifact:
        return TaskArtifact.model_validate_json(self._read_reference(reference))

    def read_gate(self, reference: str) -> GateResult:
        return GateResult.model_validate_json(self._read_reference(reference))

    def ref_for(self, run_id: UUID | str, filename: str) -> str:
        # 先通过 path_for 完成同一套白名单校验，再生成不暴露主机路径的稳定引用。
        self.path_for(run_id, filename)
        return f"artifact://{run_id}/{filename}"

    def run_ref(self, run_id: UUID | str) -> str:
        run_segment = str(run_id)
        if not _SAFE_SEGMENT.fullmatch(run_segment):
            raise InvalidArtifactPathError("run id is not a safe artifact path segment")
        return f"artifact://{run_segment}"

    def _read_reference(self, reference: str) -> bytes:
        match = _ARTIFACT_REF.fullmatch(reference)
        if match is None:
            raise InvalidArtifactPathError("artifact reference is invalid")
        path = self.path_for(match.group("run"), match.group("filename"))
        try:
            return path.read_bytes()
        except OSError as exc:
            raise InvalidArtifactPathError("artifact could not be read") from exc

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            # 临时文件与目标位于同一目录，os.replace 在支持的平台上保持原子性。
            os.replace(temporary_name, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    temporary_path.unlink()
