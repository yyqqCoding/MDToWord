"""使用 XML 结构解析 pytest JUnit，禁止依赖 stdout 正则判断结果。"""

from pathlib import Path
from xml.etree import ElementTree

from agent.sandbox.contracts import JUnitSummary, TargetTestOutcome
from agent.telemetry.masking import mask_text


def parse_junit_summary(path: Path, target_selector: str | None) -> JUnitSummary:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise ValueError("junit report is missing or invalid") from exc

    cases = root.findall(".//testcase")
    failures = 0
    errors = 0
    skipped = 0
    target_collected = False
    target_outcome = TargetTestOutcome.NOT_COLLECTED
    target_failure_type: str | None = None
    target_message = ""

    for case in cases:
        failure = case.find("failure")
        error = case.find("error")
        skip = case.find("skipped")
        if failure is not None:
            failures += 1
        elif error is not None:
            errors += 1
        elif skip is not None:
            skipped += 1

        name = case.attrib.get("name", "")
        if target_selector is None or target_selector not in name:
            continue
        # 固定 selector 按策略只能命中一个测试；重复收集时按最严重结果记录。
        target_collected = True
        if error is not None:
            target_outcome = TargetTestOutcome.ERROR
            target_failure_type = error.attrib.get("type")
            target_message = _bounded_failure_text(error)
        elif failure is not None:
            target_outcome = TargetTestOutcome.FAILED
            target_failure_type = failure.attrib.get("type")
            target_message = _bounded_failure_text(failure)
        elif skip is not None:
            target_outcome = TargetTestOutcome.SKIPPED
            target_failure_type = skip.attrib.get("type")
            target_message = _bounded_failure_text(skip)
        elif target_outcome is TargetTestOutcome.NOT_COLLECTED:
            target_outcome = TargetTestOutcome.PASSED

    return JUnitSummary(
        tests=len(cases),
        failures=failures,
        errors=errors,
        skipped=skipped,
        target_collected=target_collected,
        target_outcome=target_outcome,
        target_failure_type=target_failure_type,
        target_message=target_message,
    )


def _bounded_failure_text(element: ElementTree.Element) -> str:
    raw = " ".join(
        item.strip()
        for item in (element.attrib.get("message", ""), element.text or "")
        if item.strip()
    )
    # JUnit 内容来自生成测试，进入 State/Trace 前必须有硬上限。
    return mask_text(raw, max_length=1000)
