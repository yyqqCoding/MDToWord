from pathlib import Path
from unittest.mock import Mock

import pytest

from app import mermaid_renderer
from app.mermaid_renderer import MermaidRenderError, render_mermaid_blocks


def _fake_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Mock:
    puppeteer_config = tmp_path / "puppeteer.json"
    mermaid_config = tmp_path / "mermaid.json"
    puppeteer_config.write_text("{}", encoding="utf-8")
    mermaid_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mermaid_renderer, "PUPPETEER_CONFIG_PATH", puppeteer_config)
    monkeypatch.setattr(mermaid_renderer, "MERMAID_CONFIG_PATH", mermaid_config)
    monkeypatch.setattr(mermaid_renderer.shutil, "which", lambda _: "/usr/local/bin/mmdc")

    completed = Mock(returncode=0, stderr="")

    def run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"png")
        completed(command, **kwargs)
        return Mock(returncode=0, stderr="")

    monkeypatch.setattr(mermaid_renderer.subprocess, "run", run)
    return completed


def test_fenced_mermaid_blocks_are_replaced_with_local_png_references(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = _fake_runtime(monkeypatch, tmp_path)
    markdown = "Before\n\n```mermaid\ngraph TD\nA --> B\n```\n\nAfter"

    result = render_mermaid_blocks(markdown, tmp_path)

    assert result == "Before\n\n![Mermaid diagram](mermaid-1.png)\n\nAfter"
    command = run.call_args.args[0]
    assert command[0] == "/usr/local/bin/mmdc"
    assert "--puppeteerConfigFile" in command
    assert "--configFile" in command
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["timeout"] == 20
    assert "MODEL_API_KEY" not in run.call_args.kwargs["env"]


def test_bare_flowchart_document_is_rendered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fake_runtime(monkeypatch, tmp_path)

    result = render_mermaid_blocks(
        "graph TD\n    A([起床]) --> B{看窗外天气}\n    B -- 下雨 --> C[带伞]",
        tmp_path,
    )

    assert result == "![Mermaid diagram](mermaid-1.png)\n"


def test_non_mermaid_markdown_is_unchanged(tmp_path: Path) -> None:
    markdown = "# Graph TD\n\nThis is normal prose."

    assert render_mermaid_blocks(markdown, tmp_path) == markdown


@pytest.mark.parametrize(
    "source",
    (
        "graph TD\nA[<b>HTML</b>] --> B",
        "graph TD\nclick A https://example.com",
        "%%{init: {'securityLevel': 'loose'}}%%\ngraph TD\nA --> B",
    ),
)
def test_unsafe_mermaid_content_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
) -> None:
    _fake_runtime(monkeypatch, tmp_path)

    with pytest.raises(MermaidRenderError, match="unsupported"):
        render_mermaid_blocks(source, tmp_path)


def test_diagram_size_and_count_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fake_runtime(monkeypatch, tmp_path)
    too_many = "\n".join(
        f"```mermaid\ngraph TD\nA{index} --> B{index}\n```" for index in range(6)
    )

    with pytest.raises(MermaidRenderError, match="at most 5"):
        render_mermaid_blocks(too_many, tmp_path)
    with pytest.raises(MermaidRenderError, match="cannot exceed"):
        render_mermaid_blocks("graph TD\n" + "A" * 20_001, tmp_path)


def test_renderer_failure_exposes_only_bounded_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fake_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mermaid_renderer.subprocess,
        "run",
        lambda *args, **kwargs: Mock(
            returncode=1,
            stderr="\n".join(f"line {index}" for index in range(20)),
        ),
    )

    with pytest.raises(MermaidRenderError) as exc_info:
        render_mermaid_blocks("graph TD\nA --> B", tmp_path)

    assert exc_info.value.details == [f"line {index}" for index in range(10, 20)]
