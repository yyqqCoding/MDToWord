"""把受支持的 Mermaid 源码安全地渲染为供 Pandoc 嵌入的本地 PNG。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


MAX_DIAGRAMS = 5
MAX_SOURCE_BYTES = 20_000
# Render 的低 CPU 实例首次启动 Chromium 波动较大；保留明确上限并为完整转换链路留出余量。
RENDER_TIMEOUT_SECONDS = 120
PUPPETEER_CONFIG_PATH = Path("/opt/mdtoword/puppeteer-config.json")
MERMAID_CONFIG_PATH = Path("/opt/mdtoword/mermaid-config.json")

_FENCED_MERMAID_PATTERN = re.compile(
    r"(?P<indent>^[ \t]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*mermaid[ \t]*\r?\n"
    r"(?P<source>.*?)\r?\n(?P=indent)(?P=fence)[ \t]*(?=\r?\n|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_BARE_MERMAID_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:graph|flowchart)\s+(?:tb|td|bt|rl|lr)\b"
    r"|sequenceDiagram\b"
    r"|classDiagram\b"
    r"|stateDiagram(?:-v2)?\b"
    r"|erDiagram\b"
    r"|journey\b"
    r"|gantt\b"
    r"|pie\b"
    r"|mindmap\b"
    r"|timeline\b"
    r"|gitGraph\b"
    r")",
    re.IGNORECASE,
)
_MERMAID_DECLARATION_PATTERN = re.compile(
    _BARE_MERMAID_PATTERN.pattern,
    re.IGNORECASE | re.MULTILINE,
)
_UNSAFE_SOURCE_PATTERNS = (
    re.compile(r"%%\s*\{", re.IGNORECASE),
    re.compile(r"\b(?:https?|file|data|javascript):", re.IGNORECASE),
    re.compile(r"(?m)^\s*click\b", re.IGNORECASE),
    re.compile(r"<[^>]+>"),
)


class MermaidRenderError(Exception):
    """可安全返回给转换层的 Mermaid 渲染错误。"""

    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


def render_mermaid_blocks(markdown: str, work_dir: Path) -> str:
    """将 Mermaid 块替换成本地 PNG 引用；没有图时原样返回 Markdown。

    只允许固定的本地 ``mmdc``、固定配置和固定参数。调用方不能传命令、配置路径或
    环境变量，避免把模型生成的源码提升为任意浏览器/进程执行能力。
    """

    matches = list(_FENCED_MERMAID_PATTERN.finditer(markdown))
    if (
        not matches
        and _MERMAID_DECLARATION_PATTERN.search(markdown)
        and any(pattern.search(markdown) for pattern in _UNSAFE_SOURCE_PATTERNS)
    ):
        raise MermaidRenderError(
            "Mermaid diagram contains unsupported external or HTML content."
        )
    if matches:
        if len(matches) > MAX_DIAGRAMS:
            raise MermaidRenderError(
                f"A document can contain at most {MAX_DIAGRAMS} Mermaid diagrams."
            )
        replacements: list[tuple[int, int, str]] = []
        for index, match in enumerate(matches, start=1):
            image_name = _render_diagram(match.group("source"), work_dir, index)
            replacements.append(
                (match.start(), match.end(), f"![Mermaid diagram]({image_name})")
            )
        return _replace_ranges(markdown, replacements)

    if _BARE_MERMAID_PATTERN.match(markdown):
        image_name = _render_diagram(markdown.strip(), work_dir, 1)
        return f"![Mermaid diagram]({image_name})\n"

    return markdown


def _render_diagram(source: str, work_dir: Path, index: int) -> str:
    encoded = source.encode("utf-8")
    if not encoded or not source.strip():
        raise MermaidRenderError("Mermaid diagram source is empty.")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise MermaidRenderError(
            f"A Mermaid diagram cannot exceed {MAX_SOURCE_BYTES} UTF-8 bytes."
        )
    if any(pattern.search(source) for pattern in _UNSAFE_SOURCE_PATTERNS):
        raise MermaidRenderError("Mermaid diagram contains unsupported external or HTML content.")

    executable = shutil.which("mmdc")
    if executable is None:
        raise MermaidRenderError("The local Mermaid renderer is not installed.")
    if not PUPPETEER_CONFIG_PATH.is_file() or not MERMAID_CONFIG_PATH.is_file():
        raise MermaidRenderError("The local Mermaid renderer configuration is missing.")

    source_path = work_dir / f"mermaid-{index}.mmd"
    output_path = work_dir / f"mermaid-{index}.png"
    source_path.write_bytes(encoded)
    command = [
        executable,
        "--input",
        str(source_path),
        "--output",
        str(output_path),
        "--puppeteerConfigFile",
        str(PUPPETEER_CONFIG_PATH),
        "--configFile",
        str(MERMAID_CONFIG_PATH),
        "--backgroundColor",
        "white",
        "--scale",
        "2",
    ]
    environment = {
        "HOME": str(work_dir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PUPPETEER_SKIP_DOWNLOAD": "true",
        "XDG_CACHE_HOME": str(work_dir / ".cache"),
        "XDG_CONFIG_HOME": str(work_dir / ".config"),
    }
    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise MermaidRenderError("The local Mermaid renderer is not available.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MermaidRenderError("Mermaid rendering timed out.") from exc

    details = [line for line in completed.stderr.splitlines() if line.strip()]
    if completed.returncode != 0:
        raise MermaidRenderError("Mermaid failed to render the diagram.", details[-10:])
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise MermaidRenderError("Mermaid completed but did not create a PNG image.")
    return output_path.name


def _replace_ranges(
    value: str,
    replacements: list[tuple[int, int, str]],
) -> str:
    chunks: list[str] = []
    cursor = 0
    for start, end, replacement in replacements:
        chunks.extend((value[cursor:start], replacement))
        cursor = end
    chunks.append(value[cursor:])
    return "".join(chunks)
