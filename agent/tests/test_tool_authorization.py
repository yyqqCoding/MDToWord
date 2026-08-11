from pathlib import Path
from uuid import uuid4

import pytest

from agent.domain.errors import ToolAuthorizationError
from agent.tools.authorization import ToolName, ToolNode, authorize_tool
from agent.tools.edits import StructuredEditTools
from agent.workspace.artifacts import ArtifactStore
from agent.workspace.edits import Edit, EditMode, PatchBuilder
from agent.workspace.patch_policy import PatchPolicy


def test_gate_and_unregistered_tools_are_never_authorized():
    with pytest.raises(ToolAuthorizationError):
        authorize_tool(ToolNode.GATE, "read_source_file")
    with pytest.raises(ToolAuthorizationError):
        authorize_tool(ToolNode.REPRODUCTION_INSPECT, "shell")
    assert (
        authorize_tool(ToolNode.REPRODUCTION_INSPECT, "read_source_file")
        is ToolName.READ_SOURCE_FILE
    )


def test_structured_edit_tool_writes_only_validated_patch_artifact(tmp_path: Path):
    snapshot = tmp_path / "snapshot"
    (snapshot / "backend/tests").mkdir(parents=True)
    target = snapshot / "backend/tests/test_feedback_regressions.py"
    target.write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    run_id = uuid4()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    tools = StructuredEditTools(
        PatchBuilder(PatchPolicy.load_default()),
        artifacts,
    )

    submitted = tools.submit_test_edits(
        run_id,
        snapshot,
        (
            Edit(
                path="backend/tests/test_feedback_regressions.py",
                mode=EditMode.SEARCH_REPLACE,
                search="assert True",
                replace=(
                    "assert True\n\n\n"
                    "def test_feedback_ab12cd_table():\n    assert False"
                ),
            ),
        ),
    )

    assert submitted.artifact_ref == f"artifact://{run_id}/test.patch"
    assert artifacts.read_patch(submitted.artifact_ref)
