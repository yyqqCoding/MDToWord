import zipfile
from pathlib import Path

import pytest

from app.pandoc_runner import ConversionError, convert_markdown_to_docx


FIXTURES = Path(__file__).parent / "fixtures"


def test_convert_markdown_to_docx_creates_word_document(tmp_path):
    markdown = (FIXTURES / "sample_basic.md").read_text(encoding="utf-8")

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)

    assert docx_bytes.startswith(b"PK")
    docx_path = tmp_path / "result.docx"
    docx_path.write_bytes(docx_bytes)
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "word/document.xml" in names
    assert "示例文档" in document_xml
    assert "<m:oMath" in document_xml or "<m:oMathPara" in document_xml
    assert "<w:tbl>" in document_xml


def test_convert_invalid_formula_raises_conversion_error(tmp_path):
    markdown = (FIXTURES / "sample_invalid_formula.md").read_text(encoding="utf-8")

    with pytest.raises(ConversionError) as exc_info:
        convert_markdown_to_docx(markdown, tmp_path)

    assert exc_info.value.message
