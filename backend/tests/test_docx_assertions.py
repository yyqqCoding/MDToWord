from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from docx_assertions import (
    assert_minimum_drawing_count,
    assert_minimum_math_count,
    assert_minimum_table_count,
    assert_paragraph_style_present,
    assert_required_parts_present,
    assert_text_absent,
    assert_three_line_table_structure,
    assert_valid_zip,
    assert_xml_parseable,
)


def _docx() -> bytes:
    content = BytesIO()
    with ZipFile(content, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "word/document.xml",
            """<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1" /></w:pPr><w:r><w:t>Visible</w:t></w:r></w:p>
  <m:oMath />
  <w:p><w:r><w:drawing /></w:r></w:p>
  <w:p><w:r><w:pict /></w:r></w:p>
  <w:tbl>
   <w:tblPr><w:tblBorders>
    <w:top w:val="single"/><w:left w:val="nil"/><w:bottom w:val="single"/>
    <w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/>
   </w:tblBorders></w:tblPr>
   <w:tr><w:tc><w:tcPr><w:tcBorders><w:bottom w:val="single"/></w:tcBorders></w:tcPr></w:tc></w:tr>
  </w:tbl>
 </w:body>
</w:document>""",
        )
    return content.getvalue()


def test_trusted_docx_assertions_accept_expected_structure() -> None:
    document = _docx()
    assert_valid_zip(document)
    assert_required_parts_present(document)
    assert_xml_parseable(document)
    assert_minimum_table_count(document, 1)
    assert_minimum_math_count(document, 1)
    assert_minimum_drawing_count(document, 2)
    assert_paragraph_style_present(document, "Heading1")
    assert_text_absent(document, "raw delimiter")
    assert_three_line_table_structure(document)


def test_trusted_docx_assertions_report_missing_structure() -> None:
    with pytest.raises(AssertionError, match="at least 2 table"):
        assert_minimum_table_count(_docx(), 2)
    with pytest.raises(AssertionError, match="at least 3 drawing"):
        assert_minimum_drawing_count(_docx(), 3)
