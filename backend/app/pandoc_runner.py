import shutil
import subprocess
import tempfile
from pathlib import Path

from app.normalizer import normalize_markdown
from app.settings import settings

REFERENCE_DOC_PATH = Path(__file__).with_name("reference.docx")


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
