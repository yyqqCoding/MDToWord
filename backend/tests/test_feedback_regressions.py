def test_feedback_892ff98b_mermaid_not_rendered(tmp_path):
    from pathlib import Path

    from app.pandoc_runner import convert_markdown_to_docx
    from docx_assertions import assert_minimum_drawing_count

    fixture = Path(__file__).parent / "fixtures" / "feedback" / "test_feedback_892ff98b_mermaid_not_rendered.md"
    markdown = fixture.read_text(encoding="utf-8")
    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    assert_minimum_drawing_count(docx_bytes, 1)


def test_feedback_41d6c497_aligned_notag(tmp_path):
    from pathlib import Path

    from app.pandoc_runner import convert_markdown_to_docx
    from docx_assertions import assert_minimum_math_count

    fixture = Path(__file__).parent / "fixtures" / "feedback" / "test_feedback_41d6c497_aligned_notag.md"
    markdown = fixture.read_text(encoding="utf-8")
    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    assert_minimum_math_count(docx_bytes, 1)


def test_feedback_9eb8eddf_conversion_probe(tmp_path):
    from pathlib import Path

    from app.pandoc_runner import convert_markdown_to_docx

    fixture = Path(__file__).parent / "fixtures" / "feedback" / "test_feedback_9eb8eddf_conversion_probe.md"
    markdown = fixture.read_text(encoding="utf-8")
    convert_markdown_to_docx(markdown, tmp_path)


def test_feedback_367cfa18_conversion_probe(tmp_path):
    from pathlib import Path

    from app.pandoc_runner import convert_markdown_to_docx

    fixture = Path(__file__).parent / "fixtures" / "feedback" / "test_feedback_367cfa18_conversion_probe.md"
    markdown = fixture.read_text(encoding="utf-8")
    convert_markdown_to_docx(markdown, tmp_path)
