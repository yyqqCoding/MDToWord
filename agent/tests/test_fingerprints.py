from agent.domain.enums import FeedbackType
from agent.domain.fingerprints import feedback_fingerprint


def test_feedback_fingerprint_is_stable_across_platform_line_endings():
    first = feedback_fingerprint(
        FeedbackType.BUG,
        "# 标题\r\n\r\n| A | B |\r\n",
        "  导出的表格不是三线表。  ",
    )
    second = feedback_fingerprint(
        FeedbackType.BUG,
        "# 标题\n\n| A | B |",
        "导出的表格不是三线表。",
    )

    assert first == second
    assert len(first) == 64


def test_feedback_fingerprint_keeps_semantically_different_input_distinct():
    bug = feedback_fingerprint(FeedbackType.BUG, "$x$", "公式导出失败")
    changed_markdown = feedback_fingerprint(FeedbackType.BUG, "$y$", "公式导出失败")
    feature = feedback_fingerprint(FeedbackType.FEATURE, "$x$", "公式导出失败")

    assert len({bug, changed_markdown, feature}) == 3
