import io
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pandoc_runner import ConversionError, convert_markdown_to_docx


FIXTURES = Path(__file__).parent / "fixtures"


def test_chemical_formulas_export_as_editable_equations(tmp_path):
    markdown = (
        r"$\ce{Ca(OH)2}$"
        "\n\n"
        r"\(\ce{SO4^{2-}}\)"
        "\n\n"
        r"\[\ce{NH4+ + OH- <=> NH3 + H2O}\]"
        "\n\n"
        r"$\ce{2H2 + O2 -> 2H2O}$"
    )
    with zipfile.ZipFile(io.BytesIO(convert_markdown_to_docx(markdown, tmp_path))) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
    equations = root.findall(".//m:oMath", ns)
    assert len(equations) == 4
    assert equations[0].find(".//m:sSub/m:sub", ns) is not None
    charge = equations[1].findall(".//m:sSup/m:sup//m:t", ns)
    assert "".join(node.text or "" for node in charge).replace("−", "-") == "2-"
    assert "⇌" in "".join(equations[2].itertext())
    assert "→" in "".join(equations[3].itertext())


def test_unsupported_chemical_formulas_are_preserved_as_text(tmp_path):
    nested_inline = r"\(\ce{C=C\(\boldsymbol{\ce{BBr3、HI}}\)C#C}\)"
    nested_dollar = r"$\ce{OH^-\(\ce{C=C}\)CN^-}$"
    table_formula = r"$\ce{A|B}$"
    markdown = (
        f"前文{nested_inline}后文 {nested_dollar} $\\ce{{H2O}}$\n\n"
        f"| Compound |\n| --- |\n| {table_formula} |\n"
    )
    with zipfile.ZipFile(io.BytesIO(convert_markdown_to_docx(markdown, tmp_path))) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    ns = {
        "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    text = "".join(node.text or "" for node in root.findall(".//w:t", ns))
    assert f"前文{nested_inline}后文 {nested_dollar}" in text
    assert len(root.findall(".//m:oMath", ns)) == 1
    cell_text = root.findall(".//w:tbl//w:t", ns)
    assert table_formula in [node.text for node in cell_text]


def test_formula_warnings_are_deduplicated_and_bounded(tmp_path):
    formulas = ["x" * 200, "H₂O", "CO₂", "CH₄", "NaCl", "NH₃"]
    warnings = [
        f"[WARNING] Could not convert TeX math {formula}, rendering as TeX: detail"
        for formula in [formulas[0], *formulas]
    ]
    completed = subprocess.CompletedProcess([], 0, stderr="\n".join(warnings))

    with patch("app.pandoc_runner.subprocess.run", return_value=completed) as run:
        with pytest.raises(ConversionError) as error:
            convert_markdown_to_docx("text", tmp_path)

    assert run.call_args.kwargs["encoding"] == "utf-8"
    assert error.value.details == [
        "Formula conversion warnings: 7. Distinct formula snippets: 6.",
        "x" * 159 + "…",
        *formulas[1:5],
    ]


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


def test_convert_markdown_table_uses_three_line_table_borders(tmp_path):
    markdown = (FIXTURES / "table_three_line.md").read_text(encoding="utf-8")

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    docx_path = tmp_path / "three_line_table.docx"
    docx_path.write_bytes(docx_bytes)

    with zipfile.ZipFile(docx_path) as archive:
        document_xml = archive.read("word/document.xml")

    table = ET.fromstring(document_xml).find(".//w:tbl", WORD_XML_NAMESPACES)
    assert table is not None

    table_borders = table.find("./w:tblPr/w:tblBorders", WORD_XML_NAMESPACES)
    assert table_borders is not None
    assert border_attributes(table_borders, "top") == {"val": "single", "sz": "12"}
    assert border_attributes(table_borders, "bottom") == {"val": "single", "sz": "12"}
    assert border_attributes(table_borders, "left") == {"val": "nil"}
    assert border_attributes(table_borders, "right") == {"val": "nil"}
    assert border_attributes(table_borders, "insideH") == {"val": "nil"}
    assert border_attributes(table_borders, "insideV") == {"val": "nil"}

    first_row_bottom_borders = [
        border_attributes(cell_borders, "bottom")
        for cell_borders in table.findall("./w:tr[1]/w:tc/w:tcPr/w:tcBorders", WORD_XML_NAMESPACES)
    ]
    assert first_row_bottom_borders
    assert all(attributes == {"val": "single", "sz": "6"} for attributes in first_row_bottom_borders)


def test_convert_table_glued_to_title_still_produces_word_table(tmp_path):
    markdown = (
        "**表 5-2 美的集团第二类代理成本相关指标变动（2012—2024 年）**\n"
        "| 年份 | 关联交易占营收比例 | 现金分红比例 | 股权激励支付率 |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| 2012 | 1.12% | 31.25% | 31.25% |\n"
        "| 2024 | 0.91% | 58.35% | 58.35% |\n"
        "\n"
        "数据来源：美的集团历年年报"
    )

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    docx_path = tmp_path / "glued_table.docx"
    docx_path.write_bytes(docx_bytes)

    with zipfile.ZipFile(docx_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "<w:tbl>" in document_xml
    assert "| 年份 |" not in document_xml


def test_convert_list_without_blank_line_renders_as_list(tmp_path):
    markdown = "说明：\n- 第一项\n- 第二项\n"

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    docx_path = tmp_path / "list.docx"
    docx_path.write_bytes(docx_bytes)

    with zipfile.ZipFile(docx_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "第一项" in document_xml
    assert "第二项" in document_xml
    assert "- 第一项" not in document_xml


def test_warn_on_unparsed_tables_logs_warning(tmp_path, caplog):
    from app.pandoc_runner import _warn_on_unparsed_tables

    docx_path = tmp_path / "leaked_table.docx"
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{WORD_XML_NAMESPACES["w"]}"><w:body>'
            "<w:p><w:r><w:t>标题 | 年份 | 占比 | --- | --- | --- | 2012 | 1% | 2%</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )

    with caplog.at_level("WARNING"):
        _warn_on_unparsed_tables(docx_path)

    assert "unparsed Markdown table" in caplog.text


def test_no_warning_for_properly_parsed_table(tmp_path, caplog):
    from app.pandoc_runner import _warn_on_unparsed_tables

    markdown = (FIXTURES / "table_three_line.md").read_text(encoding="utf-8")
    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)
    docx_path = tmp_path / "ok_table.docx"
    docx_path.write_bytes(docx_bytes)

    with caplog.at_level("WARNING"):
        _warn_on_unparsed_tables(docx_path)

    assert "unparsed Markdown table" not in caplog.text


def test_convert_uses_reference_docx_for_word_styles(tmp_path):
    markdown = "# 标题\n\n正文"
    fake_docx = minimal_docx_bytes()

    def fake_run(command, **kwargs):
        (tmp_path / "result.docx").write_bytes(fake_docx)
        assert any(arg.startswith("--reference-doc=") for arg in command)
        return subprocess_completed()

    with patch("app.pandoc_runner.subprocess.run", side_effect=fake_run):
        docx_bytes = convert_markdown_to_docx(markdown, tmp_path)

    assert docx_bytes == fake_docx


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


def minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{WORD_XML_NAMESPACES["w"]}">'
                "<w:body><w:p /></w:body>"
                "</w:document>"
            ),
        )
    return buffer.getvalue()


def extract_style(styles_xml: str, style_id: str) -> str:
    marker = f'w:styleId="{style_id}"'
    start = styles_xml.index(marker)
    end = styles_xml.index("</w:style>", start)
    return styles_xml[start:end]


WORD_XML_NAMESPACES = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def border_attributes(parent: ET.Element, border_name: str) -> dict[str, str]:
    namespace = WORD_XML_NAMESPACES["w"]
    border = parent.find(f"./w:{border_name}", WORD_XML_NAMESPACES)
    assert border is not None
    attributes = {"val": border.attrib[f"{{{namespace}}}val"]}
    if f"{{{namespace}}}sz" in border.attrib:
        attributes["sz"] = border.attrib[f"{{{namespace}}}sz"]
    return attributes
