import zipfile
from pathlib import Path
from unittest.mock import patch

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


def test_convert_uses_reference_docx_for_word_styles(tmp_path):
    markdown = "# 标题\n\n正文"

    def fake_run(command, **kwargs):
        (tmp_path / "result.docx").write_bytes(b"PK fake docx bytes")
        assert any(arg.startswith("--reference-doc=") for arg in command)
        return subprocess_completed()

    with patch("app.pandoc_runner.subprocess.run", side_effect=fake_run):
        docx_bytes = convert_markdown_to_docx(markdown, tmp_path)

    assert docx_bytes == b"PK fake docx bytes"


def test_reference_docx_defines_chinese_word_styles(tmp_path):
    markdown = "\n\n".join(
        [
            "# 一级标题",
            "## 二级标题",
            "### 三级标题",
            "#### 四级标题",
            "##### 五级标题",
            "###### 六级标题",
            "正文内容",
        ],
    )

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    docx_path = tmp_path / "styled.docx"
    docx_path.write_bytes(docx_bytes)

    with zipfile.ZipFile(docx_path) as archive:
        styles_xml = archive.read("word/styles.xml").decode("utf-8")

    assert 'w:styleId="Normal"' in styles_xml
    assert 'w:eastAsia="宋体"' in styles_xml
    assert 'w:sz w:val="24"' in styles_xml
    assert 'w:line="360"' in styles_xml
    assert 'w:before="0"' in styles_xml
    assert 'w:after="0"' in styles_xml
    assert 'w:styleId="Heading1"' in styles_xml
    assert 'w:styleId="Heading6"' in styles_xml
    assert 'w:eastAsia="黑体"' in styles_xml
    for heading_style_id in ["Heading1", "Heading2", "Heading3", "Heading4", "Heading5", "Heading6"]:
        heading_style = extract_style(styles_xml, heading_style_id)
        assert 'w:color w:val="000000"' in heading_style
        assert "w:themeColor" not in heading_style


def test_convert_invalid_formula_raises_conversion_error(tmp_path):
    markdown = (FIXTURES / "sample_invalid_formula.md").read_text(encoding="utf-8")

    with pytest.raises(ConversionError) as exc_info:
        convert_markdown_to_docx(markdown, tmp_path)

    assert exc_info.value.message


def subprocess_completed():
    return type("Completed", (), {"returncode": 0, "stderr": ""})()


def extract_style(styles_xml: str, style_id: str) -> str:
    marker = f'w:styleId="{style_id}"'
    start = styles_xml.index(marker)
    end = styles_xml.index("</w:style>", start)
    return styles_xml[start:end]
