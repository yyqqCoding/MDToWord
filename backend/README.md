# MD To Word Backend

FastAPI service that converts Markdown containing text, formulas, and tables into Word `.docx`.

## Local Development

```bash
uv venv .venv
uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
```

Pandoc must be installed for `.docx` conversion. Mermaid diagrams additionally require the pinned
Mermaid CLI and Chromium runtime under `backend/mermaid/`. The Docker image installs all three;
local Python development can run non-Mermaid tests without installing the renderer.

Chromium cold-start time varies significantly on Render low-CPU instances and can exceed 75
seconds in the complete conversion path. The local Mermaid renderer therefore uses a fixed
120-second per-diagram limit so cold starts can complete without allowing an unbounded child
process. Ordinary Markdown does not start Chromium.
