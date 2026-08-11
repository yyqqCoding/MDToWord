"""把测试/修复编辑提交为可审计 Artifact，不在工具进程中执行代码。"""

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import Edit, EditPhase, PatchBuilder


class SubmittedPatch(BaseModel):
    """返回给 Graph 的补丁引用和有界变更摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_files: tuple[str, ...]
    added_lines: int = Field(ge=0)
    deleted_lines: int = Field(ge=0)


class StructuredEditTools:
    """结构化编辑只产生受 Policy 检查的 Artifact，不直接执行生成代码。"""

    def __init__(self, builder: PatchBuilder, artifacts: ArtifactStore) -> None:
        self._builder = builder
        self._artifacts = artifacts

    def submit_test_edits(
        self,
        run_id: UUID,
        snapshot_root: Path,
        edits: tuple[Edit, ...],
    ) -> SubmittedPatch:
        return self._submit(run_id, snapshot_root, edits, EditPhase.TEST)

    def submit_fix_edits(
        self,
        run_id: UUID,
        snapshot_root: Path,
        edits: tuple[Edit, ...],
    ) -> SubmittedPatch:
        return self._submit(run_id, snapshot_root, edits, EditPhase.FIX)

    def _submit(
        self,
        run_id: UUID,
        snapshot_root: Path,
        edits: tuple[Edit, ...],
        phase: EditPhase,
    ) -> SubmittedPatch:
        patch = self._builder.build(snapshot_root, edits, phase)
        filename = "test.patch" if phase is EditPhase.TEST else "fix.patch"
        # 只返回 Artifact 引用，避免大补丁重复进入模型上下文和 Trace。
        reference = self._artifacts.write_patch_ref(run_id, filename, patch.content)
        return SubmittedPatch(
            artifact_ref=reference,
            sha256=patch.sha256,
            changed_files=patch.changed_files,
            added_lines=patch.added_lines,
            deleted_lines=patch.deleted_lines,
        )
