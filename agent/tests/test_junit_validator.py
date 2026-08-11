from pathlib import Path

import pytest

from agent.sandbox.contracts import TargetTestOutcome
from agent.validators.junit import parse_junit_summary


def test_parse_junit_uses_target_testcase_failure(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="tests.test_feedback_regressions" name="test_other" />
  <testcase classname="tests.test_feedback_regressions" name="test_feedback_a257a846_table_structure">
    <failure message="assert 2 == 3" type="AssertionError">traceback</failure>
  </testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )

    summary = parse_junit_summary(report, "test_feedback_a257a846_table_structure")

    assert summary.tests == 2
    assert summary.failures == 1
    assert summary.target_collected is True
    assert summary.target_outcome is TargetTestOutcome.FAILED
    assert summary.target_failure_type == "AssertionError"


def test_parse_junit_marks_absent_selector_not_collected(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase name="test_other" /></testsuite>',
        encoding="utf-8",
    )
    summary = parse_junit_summary(report, "test_feedback_a257a846_missing")
    assert summary.target_collected is False
    assert summary.target_outcome is TargetTestOutcome.NOT_COLLECTED


def test_parse_junit_infers_assertion_type_when_pytest_omits_type(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">
  <testcase classname="tests.test_feedback_regressions" name="test_feedback_a257a846_mermaid">
    <failure message="AssertionError: expected at least 1 drawing(s), got 0">FIXTURES / feedback / sample.md</failure>
  </testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )

    summary = parse_junit_summary(report, "test_feedback_a257a846_mermaid")

    assert summary.target_failure_type == "AssertionError"


def test_parse_junit_rejects_malformed_xml(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(ValueError, match="missing or invalid"):
        parse_junit_summary(report, "test_feedback_a257a846_missing")
