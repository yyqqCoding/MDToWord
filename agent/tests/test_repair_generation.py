import asyncio
from uuid import UUID

import pytest

from agent.domain.enums import FeedbackType, RiskLevel
from agent.domain.errors import InvalidModelResponseError
from agent.domain.models import TaskArtifact
from agent.domain.repair import FixGenerationResult
from agent.domain.reproduction import (
    ExpectedFailureKind,
    OracleKind,
    OracleSpec,
    ReproductionDisposition,
    ReproductionPlan,
    ReproductionReport,
    SourceReadRequest,
)
from agent.providers.fake import FakeModelProvider
from agent.repair import generate_fix
from agent.tools.source import SourceFileResult
from agent.workspace.edits import Edit, EditMode


FEEDBACK_ID = UUID("a257a846-1728-4d39-81bf-75a388041215")


def _task() -> TaskArtifact:
    return TaskArtifact(
        feedback_id=FEEDBACK_ID,
        feedback_type=FeedbackType.BUG,
        markdown_content="graph TD\nA --> B",
        description="flowchart remains source text in exported Word",
        content_fingerprint="a" * 64,
    )


def _plan() -> ReproductionPlan:
    return ReproductionPlan(
        hypothesis="Mermaid source is not converted to a drawing",
        oracle=OracleSpec(
            kind=OracleKind.DOCX_XPATH,
            parameters={"validator": "minimum_drawing_count", "minimum": 1},
        ),
        target_test_selector="test_feedback_a257a846_mermaid_drawing",
        expected_failure_kind=ExpectedFailureKind.ASSERTION,
        files_to_read=(SourceReadRequest(path="backend/app/normalizer.py"),),
    )


def _fix(*, extension_sync_required: bool = False) -> FixGenerationResult:
    return FixGenerationResult(
        edits=(
            Edit(
                path="backend/app/normalizer.py",
                mode=EditMode.SEARCH_REPLACE,
                search="    return markdown\n",
                replace="    return render_mermaid(markdown)\n",
            ),
        ),
        summary="render Mermaid blocks before Pandoc conversion",
        behavior_changes=("Mermaid blocks become DOCX drawings",),
        risk_level=RiskLevel.MEDIUM,
        manual_review_notes=("verify drawing readability",),
        extension_sync_required=extension_sync_required,
    )


def _run(provider: FakeModelProvider):
    return asyncio.run(
        generate_fix(
            _task(),
            plan=_plan(),
            reproduction_report=ReproductionReport(
                disposition=ReproductionDisposition.REPRODUCED,
                round=1,
                target_test_selector=_plan().target_test_selector,
                expected_failure_kind=ExpectedFailureKind.ASSERTION,
                failure_code="target_assertion_failure",
                failure_summary="trusted drawing assertion failed",
            ),
            source_files=(
                SourceFileResult(
                    path="backend/app/normalizer.py",
                    start_line=1,
                    end_line=2,
                    total_lines=2,
                    content="def normalize(markdown):\n    return markdown\n",
                ),
            ),
            test_patch_summary={"sha256": "b" * 64, "changed_files": ["backend/tests/test_feedback_regressions.py"]},
            previous_report=None,
            previous_fix_summary=None,
            provider=provider,
        )
    )


def test_generate_fix_uses_no_model_tools_and_returns_structured_edits() -> None:
    provider = FakeModelProvider([_fix()])

    execution = _run(provider)

    assert execution.output.summary.startswith("render Mermaid")
    assert provider.requests[0].tools == ()
    assert provider.requests[0].response_schema is FixGenerationResult
    assert "<untrusted-repair-context>" in provider.requests[0].messages[-1].content


def test_extension_dependent_fix_gets_one_bounded_policy_correction() -> None:
    provider = FakeModelProvider([_fix(extension_sync_required=True), _fix()])

    execution = _run(provider)

    assert execution.model_calls == 2
    assert len(provider.requests) == 2
    assert "extension_sync_required 必须为 false" in provider.requests[1].messages[-1].content


def test_two_policy_violations_are_rejected() -> None:
    provider = FakeModelProvider(
        [_fix(extension_sync_required=True), _fix(extension_sync_required=True)]
    )

    with pytest.raises(InvalidModelResponseError):
        _run(provider)
