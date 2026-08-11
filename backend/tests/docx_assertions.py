"""Agent 回归测试可调用的受信 DOCX 结构断言。

模型只传入数据参数；ZIP、XML 和结构检查固定在这个不可由测试补丁修改的模块中。
"""

from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_REQUIRED_PARTS = frozenset({"[Content_Types].xml", "word/document.xml"})


def assert_valid_zip(document: bytes | Path | BinaryIO) -> None:
    with _open_docx(document) as archive:
        assert archive.testzip() is None, "DOCX contains a corrupt ZIP member"


def assert_required_parts_present(document: bytes | Path | BinaryIO) -> None:
    with _open_docx(document) as archive:
        missing = _REQUIRED_PARTS.difference(archive.namelist())
        assert not missing, f"DOCX is missing required parts: {sorted(missing)}"


def assert_xml_parseable(document: bytes | Path | BinaryIO) -> None:
    with _open_docx(document) as archive:
        for name in archive.namelist():
            if name.endswith(".xml"):
                ElementTree.fromstring(archive.read(name))


def assert_minimum_table_count(document: bytes | Path | BinaryIO, minimum: int) -> None:
    root = _document_root(document)
    count = len(root.findall(f".//{{{_WORD_NAMESPACE}}}tbl"))
    assert count >= minimum, f"expected at least {minimum} table(s), got {count}"


def assert_minimum_math_count(document: bytes | Path | BinaryIO, minimum: int) -> None:
    root = _document_root(document)
    count = len(root.findall(f".//{{{_MATH_NAMESPACE}}}oMath"))
    assert count >= minimum, f"expected at least {minimum} math node(s), got {count}"


def assert_minimum_drawing_count(document: bytes | Path | BinaryIO, minimum: int) -> None:
    """同时统计 OOXML DrawingML 与旧版 VML 图片容器。"""

    root = _document_root(document)
    count = len(root.findall(f".//{{{_WORD_NAMESPACE}}}drawing"))
    count += len(root.findall(f".//{{{_WORD_NAMESPACE}}}pict"))
    assert count >= minimum, f"expected at least {minimum} drawing(s), got {count}"


def assert_paragraph_style_present(document: bytes | Path | BinaryIO, style: str) -> None:
    root = _document_root(document)
    styles = {
        item.attrib.get(f"{{{_WORD_NAMESPACE}}}val", "")
        for item in root.findall(f".//{{{_WORD_NAMESPACE}}}pStyle")
    }
    assert style in styles, f"paragraph style is absent: {style}"


def assert_text_absent(document: bytes | Path | BinaryIO, text: str) -> None:
    root = _document_root(document)
    visible = "".join(
        item.text or "" for item in root.findall(f".//{{{_WORD_NAMESPACE}}}t")
    )
    assert text not in visible, f"unexpected visible text remains: {text}"


def assert_three_line_table_structure(document: bytes | Path | BinaryIO) -> None:
    root = _document_root(document)
    tables = root.findall(f".//{{{_WORD_NAMESPACE}}}tbl")
    assert tables, "expected a table"
    expected_table_borders = {
        "top": "single",
        "left": "nil",
        "bottom": "single",
        "right": "nil",
        "insideH": "nil",
        "insideV": "nil",
    }
    value_attribute = f"{{{_WORD_NAMESPACE}}}val"
    for table in tables:
        borders = table.find(
            f"{{{_WORD_NAMESPACE}}}tblPr/{{{_WORD_NAMESPACE}}}tblBorders"
        )
        assert borders is not None, "table is missing three-line borders"
        for name, expected in expected_table_borders.items():
            border = borders.find(f"{{{_WORD_NAMESPACE}}}{name}")
            actual = border.attrib.get(value_attribute) if border is not None else None
            assert actual == expected, f"table border {name} should be {expected}"

        first_row = table.find(f"{{{_WORD_NAMESPACE}}}tr")
        assert first_row is not None, "table is missing a header row"
        cells = first_row.findall(f"{{{_WORD_NAMESPACE}}}tc")
        assert cells, "table header row is missing cells"
        for cell in cells:
            separator = cell.find(
                f"{{{_WORD_NAMESPACE}}}tcPr/"
                f"{{{_WORD_NAMESPACE}}}tcBorders/"
                f"{{{_WORD_NAMESPACE}}}bottom"
            )
            actual = separator.attrib.get(value_attribute) if separator is not None else None
            assert actual == "single", "table header separator should be single"


def _document_root(document: bytes | Path | BinaryIO) -> ElementTree.Element:
    with _open_docx(document) as archive:
        assert_required = _REQUIRED_PARTS.difference(archive.namelist())
        assert not assert_required, f"DOCX is missing required parts: {sorted(assert_required)}"
        return ElementTree.fromstring(archive.read("word/document.xml"))


def _open_docx(document: bytes | Path | BinaryIO) -> ZipFile:
    source: bytes | Path | BinaryIO
    source = BytesIO(document) if isinstance(document, bytes) else document
    try:
        return ZipFile(source)
    except (BadZipFile, OSError) as exc:
        raise AssertionError("output is not a valid DOCX ZIP") from exc
