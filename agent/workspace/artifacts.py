import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from agent.domain.errors import InvalidArtifactPathError
from agent.domain.models import TaskArtifact


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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
