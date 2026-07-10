# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project overview

MD To Word converts AI-generated Markdown into editable Word `.docx` files, preserving tables (as three-line tables), formulas (as editable OMML), and headings.

- `backend/` — FastAPI + Pandoc conversion service (Python, deployed to Render)
- `extension/` — Chrome/Edge Manifest V3 side-panel extension (React + TypeScript + Vite)

## Commands

### Backend

```bash
cd backend
uv venv .venv && uv pip install -e ".[dev]"       # setup
.venv/bin/uvicorn app.main:app --reload            # run locally
.venv/bin/python -m pytest -v                      # all tests
.venv/bin/python -m pytest tests/test_normalizer.py -v  # normalization tests only
docker build -t md-to-word-backend .               # Docker build
```

### Extension

```bash
cd extension
npm install              # install deps
npm run dev              # Vite dev server
npm run build            # production build (tsc + vite → dist/)
```

No dedicated test/lint script; `npm run build` runs `tsc` for type checking.

## Local extension workflow

1. `npm run build` in `extension/`
2. Open `edge://extensions`, enable developer mode
3. Load `extension/dist` as unpacked extension
4. Click extension icon to open side panel

Production service URL: `https://mdtoword.onrender.com`

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/convert` | Markdown → DOCX conversion |
| POST | `/feedback` | User feedback (stored in Supabase) |

The feedback endpoint requires `SUPABASE_URL` and `SUPABASE_KEY` environment variables on Render.

## Key rules

### Math normalization

Preview and export must normalize math consistently:

- Frontend: `extension/src/normalizer.ts`
- Backend: `backend/app/normalizer.py`
- When changing normalization behavior, update both unless intentionally one-sided.

### Extension dependencies

- `canvas-confetti` — celebration animation on feedback submit
- `markdown-it` + `katex` — Markdown preview with math rendering
- `lucide-react` — icons

### Deployment

- Backend deploys to Render from `backend/`; health path `/health`
- Extension: build `extension/dist` and reload in browser (or publish to Edge Add-ons)
