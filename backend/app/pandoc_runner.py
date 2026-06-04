import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.normalizer import normalize_markdown
from app.settings import settings

REFERENCE_DOC_PATH = Path(__file__).with_name("reference.docx")
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NAMESPACE}}}"
DOCX_XML_NAMESPACES = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": WORD_NAMESPACE,
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
TABLE_BORDER_SPECS = {
    "top": {"val": "single", "sz": "12"},
    "left": {"val": "nil"},
    "bottom": {"val": "single", "sz": "12"},
    "right": {"val": "nil"},
    "insideH": {"val": "nil"},
    "insideV": {"val": "nil"},
}
HEADER_SEPARATOR_BORDER_SPECS = {
    "bottom": {"val": "single", "sz": "6"},
}

for prefix, namespace in DOCX_XML_NAMESPACES.items():
    ET.register_namespace(prefix, namespace)


class ConversionError(Exception):
    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


def convert_markdown_to_docx(markdown: str, work_dir: Path | None = None) -> bytes:
    if work_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _convert(markdown, Path(tmp))

    work_dir.mkdir(parents=True, exist_ok=True)
    return _convert(markdown, work_dir)


def _convert(markdown: str, work_dir: Path) -> bytes:
    input_path = work_dir / "input.md"
    output_path = work_dir / "result.docx"
    input_path.write_text(normalize_markdown(markdown), encoding="utf-8")

    command = [
        _resolve_pandoc_binary(),
        str(input_path),
        "--from",
        "markdown+tex_math_dollars+tex_math_single_backslash+pipe_tables+grid_tables",
        "--to",
        "docx",
        f"--reference-doc={REFERENCE_DOC_PATH}",
        "--output",
        str(output_path),
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.pandoc_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ConversionError("Pandoc is not installed or is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("Pandoc conversion timed out.") from exc

    details = [line for line in completed.stderr.splitlines() if line.strip()]
    if completed.returncode != 0:
        raise ConversionError("Pandoc failed to convert the Markdown document.", details)

    if _has_math_conversion_warning(details):
        details = [line for line in completed.stderr.splitlines() if line.strip()]
        raise ConversionError("Pandoc could not convert one or more formulas to editable Word equations.", details)

    if not output_path.exists():
        raise ConversionError("Pandoc completed but did not create a DOCX file.")

    _apply_three_line_table_borders(output_path)

    return output_path.read_bytes()


def _resolve_pandoc_binary() -> str:
    configured = settings.pandoc_binary
    if configured != "pandoc" or shutil.which(configured):
        return configured

    try:
        import pypandoc
    except ImportError:
        return configured

    return pypandoc.get_pandoc_path()


def _has_math_conversion_warning(details: list[str]) -> bool:
    return any("Could not convert TeX math" in line for line in details)


def _apply_three_line_table_borders(docx_path: Path) -> None:
    with zipfile.ZipFile(docx_path) as archive:
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}

    document_entry = entries.get("word/document.xml")
    if document_entry is None:
        return

    document_xml = document_entry[1]
    root = ET.fromstring(document_xml)
    tables = root.findall(f".//{WORD}tbl")
    if not tables:
        return

    for table in tables:
        _format_three_line_table(table)

    updated_document_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(docx_path, "w") as archive:
        for filename, (info, content) in entries.items():
            archive.writestr(info, updated_document_xml if filename == "word/document.xml" else content)


def _format_three_line_table(table: ET.Element) -> None:
    table_properties = _get_or_create_first_child(table, f"{WORD}tblPr")
    table_borders = _replace_border_container(table_properties, f"{WORD}tblBorders")
    _set_borders(table_borders, TABLE_BORDER_SPECS)

    first_row = table.find(f"./{WORD}tr")
    if first_row is None:
        return

    for cell in first_row.findall(f"./{WORD}tc"):
        cell_properties = _get_or_create_first_child(cell, f"{WORD}tcPr")
        cell_borders = _replace_border_container(cell_properties, f"{WORD}tcBorders")
        _set_borders(cell_borders, HEADER_SEPARATOR_BORDER_SPECS)


def _get_or_create_first_child(parent: ET.Element, tag: str) -> ET.Element:
    child = parent.find(f"./{tag}")
    if child is not None:
        return child

    child = ET.Element(tag)
    parent.insert(0, child)
    return child


def _replace_border_container(parent: ET.Element, tag: str) -> ET.Element:
    existing = parent.find(f"./{tag}")
    if existing is not None:
        parent.remove(existing)

    container = ET.Element(tag)
    parent.append(container)
    return container


def _set_borders(parent: ET.Element, border_specs: dict[str, dict[str, str]]) -> None:
    for border_name, attributes in border_specs.items():
        border = ET.SubElement(parent, f"{WORD}{border_name}")
        for name, value in attributes.items():
            border.set(f"{WORD}{name}", value)
