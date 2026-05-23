# MD To Word Backend

FastAPI service that converts Markdown containing text, formulas, and tables into Word `.docx`.

## Local Development

```bash
uv venv .venv
uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
```

Pandoc must be installed for `.docx` conversion. The Docker image installs Pandoc automatically.
