# MD To Word

Browser extension and conversion backend for exporting AI-generated Markdown to Word.

Supported content:

- Text
- Mathematical formulas
- Markdown pipe tables

The generated Word document should contain editable Word equations, not formula screenshots.

## Project Layout

```text
backend/   FastAPI + Pandoc conversion service
extension/ Browser extension built with TypeScript and Manifest V3
docs/      Design docs, plans, deployment notes, and sample content
```

## Backend

```bash
cd backend
uv venv .venv
uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
```

Pandoc must be installed locally for `.docx` conversion. The development extra includes `pypandoc_binary` so local tests can run without a system Pandoc install.

## Extension

```bash
cd extension
npm install
npm run dev
```
