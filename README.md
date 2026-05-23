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

## Verification

Backend:

```bash
cd backend
.venv/bin/python -m pytest -v
```

Extension build:

```bash
cd extension
npm run build
```

Extension dependency audit:

```bash
cd extension
npm audit --audit-level=moderate
```

Docker image:

```bash
cd backend
docker build -t md-to-word-backend .
```

Manual DOCX acceptance:

- Open the generated `.docx` in Microsoft Word.
- Confirm text is readable.
- Confirm tables are Word-native tables.
- Confirm formulas can be edited as Word equations.

## Load Extension Locally

1. Run `npm run build` in `extension/`.
2. Open `chrome://extensions` or `edge://extensions`.
3. Enable developer mode.
4. Load `extension/dist` as an unpacked extension.
5. Click the extension icon to open the side panel.
6. Set the conversion service URL to `http://127.0.0.1:8000`.
