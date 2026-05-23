# MD To Word Browser Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser extension and conversion backend that lets users paste Markdown containing text, math formulas, and tables, then export a `.docx` where formulas are editable Word equations.

**Architecture:** The browser extension handles manual input, preview, configuration, and download. A Python FastAPI backend wraps Pandoc for high-quality Markdown-to-DOCX conversion. Docker packages Pandoc with the backend so the same service can run locally or on Render.

**Tech Stack:** Python 3.11, FastAPI, pytest, Pandoc, Docker, TypeScript, Vite, React, Manifest V3, markdown-it, KaTeX.

---

## File Structure

Create the project as two independent applications plus shared fixtures:

```text
backend/
  app/
    __init__.py
    main.py
    models.py
    normalizer.py
    pandoc_runner.py
    settings.py
  tests/
    fixtures/
      sample_basic.md
      sample_invalid_formula.md
    test_convert_api.py
    test_normalizer.py
    test_pandoc_runner.py
  Dockerfile
  pyproject.toml
  README.md

extension/
  public/
    manifest.json
  src/
    App.tsx
    api.ts
    background.ts
    main.tsx
    preview.tsx
    storage.ts
    styles.css
    types.ts
  index.html
  package.json
  tsconfig.json
  vite.config.ts
  README.md

docs/
  samples/
    ai-output-sample.md
  deployment/
    render.md

README.md
```

Responsibilities:

- `backend/app/main.py`: FastAPI app, `/health`, `/convert`.
- `backend/app/models.py`: request and error response models.
- `backend/app/normalizer.py`: formula delimiter normalization.
- `backend/app/pandoc_runner.py`: safe Pandoc subprocess wrapper.
- `backend/app/settings.py`: runtime settings such as timeout and Pandoc path.
- `extension/src/App.tsx`: main extension UI.
- `extension/src/api.ts`: backend health check and conversion calls.
- `extension/src/background.ts`: service worker that opens the side panel when the extension action is clicked.
- `extension/src/preview.tsx`: Markdown, formula, and table preview.
- `extension/src/storage.ts`: local draft and service URL persistence.
- `docs/samples/ai-output-sample.md`: canonical manual test input.
- `docs/deployment/render.md`: Render deployment steps.

## Task 1: Backend Project Skeleton

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/settings.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_convert_api.py`
- Create: `backend/README.md`

- [ ] **Step 1: Write the failing health endpoint test**

Create `backend/tests/test_convert_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "engine": "pandoc",
    }
```

- [ ] **Step 2: Create Python project metadata**

Create `backend/pyproject.toml`:

```toml
[project]
name = "md-to-word-backend"
version = "0.1.0"
description = "Markdown to Word conversion backend for the MD To Word browser extension"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "pydantic>=2.8.0",
  "python-multipart>=0.0.9",
  "uvicorn[standard]>=0.30.0",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.27.0",
  "pytest>=8.3.0",
  "pytest-cov>=5.0.0",
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_convert_api.py::test_health_returns_ok -v
```

Expected: FAIL because `app.main` does not exist yet.

- [ ] **Step 4: Implement the minimal FastAPI app**

Create `backend/app/__init__.py`:

```python
"""MD To Word conversion backend."""
```

Create `backend/app/settings.py`:

```python
from pydantic import BaseModel


class Settings(BaseModel):
    pandoc_binary: str = "pandoc"
    pandoc_timeout_seconds: int = 30


settings = Settings()
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="MD To Word Converter")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine": "pandoc",
    }
```

Create `backend/README.md`:

```md
# MD To Word Backend

FastAPI service that converts Markdown containing text, formulas, and tables into Word `.docx`.

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```
```

- [ ] **Step 5: Run the test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_convert_api.py::test_health_returns_ok -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/app/__init__.py backend/app/settings.py backend/app/main.py backend/tests/test_convert_api.py backend/README.md
git commit -m "feat: add backend skeleton"
```

## Task 2: Markdown Formula Normalizer

**Files:**
- Create: `backend/app/normalizer.py`
- Create: `backend/tests/test_normalizer.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `backend/tests/test_normalizer.py`:

```python
from app.normalizer import normalize_markdown


def test_normalize_inline_parentheses_formula_to_dollars():
    markdown = "质能方程是 \\(E = mc^2\\)。"

    result = normalize_markdown(markdown)

    assert result == "质能方程是 $E = mc^2$。"


def test_normalize_block_bracket_formula_to_double_dollars():
    markdown = "下面是积分：\n\\[\\int_0^1 x^2 dx = \\frac{1}{3}\\]\n结束。"

    result = normalize_markdown(markdown)

    assert result == "下面是积分：\n\n$$\n\\int_0^1 x^2 dx = \\frac{1}{3}\n$$\n\n结束。"


def test_preserve_existing_dollar_formulas_and_table():
    markdown = (
        "行内公式 $a^2 + b^2 = c^2$。\n\n"
        "| 名称 | 公式 |\n"
        "|---|---|\n"
        "| 动能 | $E_k = \\frac{1}{2}mv^2$ |\n"
    )

    result = normalize_markdown(markdown)

    assert result == markdown
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_normalizer.py -v
```

Expected: FAIL because `app.normalizer` does not exist yet.

- [ ] **Step 3: Implement normalization**

Create `backend/app/normalizer.py`:

```python
import re


INLINE_PARENS_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BLOCK_BRACKETS_PATTERN = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def normalize_markdown(markdown: str) -> str:
    """Normalize supported formula delimiters into Pandoc-friendly Markdown."""
    normalized = INLINE_PARENS_PATTERN.sub(lambda match: f"${match.group(1).strip()}$", markdown)
    normalized = BLOCK_BRACKETS_PATTERN.sub(_replace_block_formula, normalized)
    return normalized


def _replace_block_formula(match: re.Match[str]) -> str:
    formula = match.group(1).strip()
    return f"\n\n$$\n{formula}\n$$\n\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cd backend
python -m pytest tests/test_normalizer.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/normalizer.py backend/tests/test_normalizer.py
git commit -m "feat: normalize markdown formulas"
```

## Task 3: Pandoc Runner

**Files:**
- Create: `backend/app/pandoc_runner.py`
- Create: `backend/tests/fixtures/sample_basic.md`
- Create: `backend/tests/fixtures/sample_invalid_formula.md`
- Create: `backend/tests/test_pandoc_runner.py`

- [ ] **Step 1: Write failing Pandoc runner tests**

Create `backend/tests/fixtures/sample_basic.md`:

```md
# 示例文档

这是一段中文文字，包含行内公式 $E = mc^2$。

$$
\int_0^1 x^2 dx = \frac{1}{3}
$$

| 名称 | 公式 | 说明 |
|---|---|---|
| 动能 | $E_k = \frac{1}{2}mv^2$ | 物体运动能量 |
| 质能方程 | $E = mc^2$ | 质量和能量关系 |
```

Create `backend/tests/fixtures/sample_invalid_formula.md`:

```md
这是一个错误公式：

$$
\frac{1}{}
$$
```

Create `backend/tests/test_pandoc_runner.py`:

```python
import zipfile
from pathlib import Path

import pytest

from app.pandoc_runner import ConversionError, convert_markdown_to_docx


FIXTURES = Path(__file__).parent / "fixtures"


def test_convert_markdown_to_docx_creates_word_document(tmp_path):
    markdown = (FIXTURES / "sample_basic.md").read_text(encoding="utf-8")

    docx_bytes = convert_markdown_to_docx(markdown, tmp_path)

    assert docx_bytes.startswith(b"PK")
    docx_path = tmp_path / "result.docx"
    docx_path.write_bytes(docx_bytes)
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "word/document.xml" in names
    assert "示例文档" in document_xml
    assert "<m:oMath" in document_xml or "<m:oMathPara" in document_xml
    assert "<w:tbl>" in document_xml


def test_convert_invalid_formula_raises_conversion_error(tmp_path):
    markdown = (FIXTURES / "sample_invalid_formula.md").read_text(encoding="utf-8")

    with pytest.raises(ConversionError) as exc_info:
        convert_markdown_to_docx(markdown, tmp_path)

    assert exc_info.value.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_pandoc_runner.py -v
```

Expected: FAIL because `app.pandoc_runner` does not exist yet.

- [ ] **Step 3: Implement the Pandoc runner**

Create `backend/app/pandoc_runner.py`:

```python
import subprocess
import tempfile
from pathlib import Path

from app.normalizer import normalize_markdown
from app.settings import settings


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
        settings.pandoc_binary,
        str(input_path),
        "--from",
        "markdown+tex_math_dollars+tex_math_single_backslash+pipe_tables+grid_tables",
        "--to",
        "docx",
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

    if completed.returncode != 0:
        details = [line for line in completed.stderr.splitlines() if line.strip()]
        raise ConversionError("Pandoc failed to convert the Markdown document.", details)

    if not output_path.exists():
        raise ConversionError("Pandoc completed but did not create a DOCX file.")

    return output_path.read_bytes()
```

- [ ] **Step 4: Run tests to verify behavior**

Run:

```bash
cd backend
python -m pytest tests/test_pandoc_runner.py -v
```

Expected:

- PASS when Pandoc is installed.
- If Pandoc is missing, install Pandoc locally or run the test inside the Docker image from Task 7.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pandoc_runner.py backend/tests/fixtures/sample_basic.md backend/tests/fixtures/sample_invalid_formula.md backend/tests/test_pandoc_runner.py
git commit -m "feat: add pandoc docx conversion"
```

## Task 4: Conversion API

**Files:**
- Create: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_convert_api.py`

- [ ] **Step 1: Extend API tests**

Replace `backend/tests/test_convert_api.py` with:

```python
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.pandoc_runner import ConversionError


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "engine": "pandoc",
    }


def test_convert_rejects_empty_markdown():
    response = client.post(
        "/convert",
        json={
            "title": "empty",
            "markdown": "   ",
            "options": {
                "filename": "empty.docx",
            },
        },
    )

    assert response.status_code == 422


def test_convert_returns_docx_file():
    fake_docx = b"PK fake docx bytes"

    with patch("app.main.convert_markdown_to_docx", return_value=fake_docx):
        response = client.post(
            "/convert",
            json={
                "title": "example",
                "markdown": "质能方程是 $E = mc^2$。",
                "options": {
                    "filename": "example.docx",
                },
            },
        )

    assert response.status_code == 200
    assert response.content == fake_docx
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "example.docx" in response.headers["content-disposition"]


def test_convert_returns_structured_error_on_conversion_failure():
    with patch(
        "app.main.convert_markdown_to_docx",
        side_effect=ConversionError("Pandoc failed.", ["line 3: unexpected token"]),
    ):
        response = client.post(
            "/convert",
            json={
                "title": "bad",
                "markdown": "$$\\bad$$",
                "options": {
                    "filename": "bad.docx",
                },
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": "conversion_failed",
        "message": "Pandoc failed.",
        "details": ["line 3: unexpected token"],
    }
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_convert_api.py -v
```

Expected: FAIL because `/convert` and request models are not implemented.

- [ ] **Step 3: Implement request models**

Create `backend/app/models.py`:

```python
from pydantic import BaseModel, Field, field_validator


class ConvertOptions(BaseModel):
    filename: str = "document.docx"

    @field_validator("filename")
    @classmethod
    def ensure_docx_filename(cls, value: str) -> str:
        cleaned = value.strip() or "document.docx"
        if not cleaned.endswith(".docx"):
            cleaned = f"{cleaned}.docx"
        return cleaned


class ConvertRequest(BaseModel):
    title: str = "document"
    markdown: str = Field(min_length=1)
    options: ConvertOptions = Field(default_factory=ConvertOptions)

    @field_validator("markdown")
    @classmethod
    def markdown_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("markdown must not be blank")
        return value


class ConversionErrorResponse(BaseModel):
    error: str
    message: str
    details: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Implement `/convert`**

Replace `backend/app/main.py` with:

```python
import tempfile

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from app.models import ConversionErrorResponse, ConvertRequest
from app.pandoc_runner import ConversionError, convert_markdown_to_docx

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = FastAPI(title="MD To Word Converter")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine": "pandoc",
    }


@app.post("/convert")
def convert(request: ConvertRequest) -> Response:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            docx_bytes = convert_markdown_to_docx(request.markdown, Path(tmp))
    except ConversionError as exc:
        error = ConversionErrorResponse(
            error="conversion_failed",
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=400, content=error.model_dump())

    headers = {
        "Content-Disposition": f'attachment; filename="{request.options.filename}"',
    }
    return Response(content=docx_bytes, media_type=DOCX_MEDIA_TYPE, headers=headers)
```

- [ ] **Step 5: Run API tests to verify they pass**

Run:

```bash
cd backend
python -m pytest tests/test_convert_api.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/tests/test_convert_api.py
git commit -m "feat: expose conversion api"
```

## Task 5: Backend CORS and Full Test Suite

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_convert_api.py`

- [ ] **Step 1: Add failing CORS test**

Append to `backend/tests/test_convert_api.py`:

```python

def test_convert_api_allows_extension_origin_preflight():
    response = client.options(
        "/convert",
        headers={
            "Origin": "chrome-extension://example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_convert_api.py::test_convert_api_allows_extension_origin_preflight -v
```

Expected: FAIL because CORS middleware is not configured.

- [ ] **Step 3: Add CORS settings**

Replace `backend/app/settings.py` with:

```python
from pydantic import BaseModel


class Settings(BaseModel):
    pandoc_binary: str = "pandoc"
    pandoc_timeout_seconds: int = 30
    allowed_origins: list[str] = ["*"]


settings = Settings()
```

Update `backend/app/main.py` to add CORS middleware after app creation:

```python
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.models import ConversionErrorResponse, ConvertRequest
from app.pandoc_runner import ConversionError, convert_markdown_to_docx
from app.settings import settings

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = FastAPI(title="MD To Word Converter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine": "pandoc",
    }


@app.post("/convert")
def convert(request: ConvertRequest) -> Response:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            docx_bytes = convert_markdown_to_docx(request.markdown, Path(tmp))
    except ConversionError as exc:
        error = ConversionErrorResponse(
            error="conversion_failed",
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=400, content=error.model_dump())

    headers = {
        "Content-Disposition": f'attachment; filename="{request.options.filename}"',
    }
    return Response(content=docx_bytes, media_type=DOCX_MEDIA_TYPE, headers=headers)
```

- [ ] **Step 4: Run all backend tests**

Run:

```bash
cd backend
python -m pytest -v
```

Expected: all backend tests PASS when Pandoc is installed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py backend/app/main.py backend/tests/test_convert_api.py
git commit -m "feat: allow extension cors requests"
```

## Task 6: Shared Sample Content and Root README

**Files:**
- Create: `docs/samples/ai-output-sample.md`
- Create: `README.md`

- [ ] **Step 1: Add canonical sample content**

Create `docs/samples/ai-output-sample.md`:

```md
# 物理公式示例

质能方程描述质量和能量之间的关系：$E = mc^2$。

下面是一个定积分：

$$
\int_0^1 x^2 dx = \frac{1}{3}
$$

常见公式如下：

| 名称 | 公式 | 说明 |
|---|---|---|
| 动能 | $E_k = \frac{1}{2}mv^2$ | 物体因为运动而具有的能量 |
| 勾股定理 | $a^2 + b^2 = c^2$ | 直角三角形边长关系 |
| 质能方程 | $E = mc^2$ | 质量和能量之间的关系 |
```

- [ ] **Step 2: Add root README**

Create `README.md`:

```md
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
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Pandoc must be installed locally for `.docx` conversion.

## Extension

```bash
cd extension
npm install
npm run dev
```
```

- [ ] **Step 3: Commit**

```bash
git add docs/samples/ai-output-sample.md README.md
git commit -m "docs: add sample markdown and project overview"
```

## Task 7: Backend Docker Image for Local and Render

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`
- Create: `docs/deployment/render.md`

- [ ] **Step 1: Add Dockerfile**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add Docker ignore**

Create `backend/.dockerignore`:

```text
.venv
__pycache__
.pytest_cache
tests
*.pyc
```

- [ ] **Step 3: Add Render deployment notes**

Create `docs/deployment/render.md`:

```md
# Render Deployment

Deploy the backend as a Docker Web Service.

## Settings

- Root directory: `backend`
- Environment: Docker
- Health check path: `/health`
- Port: `8000`

## Local Docker Check

```bash
cd backend
docker build -t md-to-word-backend .
docker run --rm -p 8000:8000 md-to-word-backend
```

Then check:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok","engine":"pandoc"}
```

## Notes

Render free web services may sleep after being idle. The extension should show a clear service-unavailable or retry message when the backend is waking up.
```

- [ ] **Step 4: Build Docker image**

Run:

```bash
cd backend
docker build -t md-to-word-backend .
```

Expected: image builds successfully and installs Pandoc.

- [ ] **Step 5: Commit**

```bash
git add backend/Dockerfile backend/.dockerignore docs/deployment/render.md
git commit -m "chore: add backend docker deployment"
```

## Task 8: Extension Project Skeleton

**Files:**
- Create: `extension/package.json`
- Create: `extension/tsconfig.json`
- Create: `extension/vite.config.ts`
- Create: `extension/index.html`
- Create: `extension/public/manifest.json`
- Create: `extension/src/types.ts`
- Create: `extension/src/background.ts`
- Create: `extension/src/main.tsx`
- Create: `extension/src/App.tsx`
- Create: `extension/src/styles.css`
- Create: `extension/README.md`

- [ ] **Step 1: Create package metadata**

Create `extension/package.json`:

```json
{
  "name": "md-to-word-extension",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc && vite build",
    "preview": "vite preview --host 127.0.0.1"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^4.3.0",
    "katex": "^0.16.11",
    "markdown-it": "^14.1.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/chrome": "^0.0.270",
    "@types/markdown-it": "^14.1.2",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create TypeScript and Vite config**

Create `extension/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"],
  "references": []
}
```

Create `extension/vite.config.ts`:

```ts
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(__dirname, 'index.html'),
        background: resolve(__dirname, 'src/background.ts'),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === 'background' ? 'background.js' : 'assets/[name]-[hash].js'),
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
});
```

- [ ] **Step 3: Create extension shell files**

Create `extension/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>MD To Word</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `extension/public/manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "MD To Word",
  "version": "0.1.0",
  "description": "Export pasted Markdown with formulas and tables to Word.",
  "action": {
    "default_title": "MD To Word"
  },
  "background": {
    "service_worker": "background.js",
    "type": "module"
  },
  "side_panel": {
    "default_path": "index.html"
  },
  "permissions": ["storage", "downloads", "sidePanel"],
  "host_permissions": ["http://*/*", "https://*/*"],
}
```

Create `extension/src/background.ts`:

```ts
chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true,
  });
});
```

Create `extension/src/types.ts`:

```ts
export type ServiceStatus = 'unknown' | 'available' | 'unavailable';

export interface ConvertOptions {
  filename: string;
}

export interface ConvertRequest {
  title: string;
  markdown: string;
  options: ConvertOptions;
}
```

Create `extension/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';

import { App } from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Create `extension/src/App.tsx`:

```tsx
export function App() {
  return (
    <main className="app">
      <header className="toolbar">
        <div>
          <h1>MD To Word</h1>
          <p>Markdown to editable Word equations</p>
        </div>
        <button type="button">Export DOCX</button>
      </header>
      <section className="workspace">
        <textarea aria-label="Markdown input" placeholder="Paste Markdown here" />
        <article className="preview" aria-label="Preview" />
      </section>
    </main>
  );
}
```

Create `extension/src/styles.css`:

```css
:root {
  color: #1f2933;
  background: #f6f8fb;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

button,
textarea,
input {
  font: inherit;
}

.app {
  min-height: 100vh;
  padding: 20px;
}

.toolbar {
  align-items: center;
  display: flex;
  gap: 16px;
  justify-content: space-between;
  margin: 0 auto 16px;
  max-width: 1180px;
}

.toolbar h1 {
  font-size: 20px;
  line-height: 1.2;
  margin: 0;
}

.toolbar p {
  color: #64748b;
  font-size: 13px;
  margin: 4px 0 0;
}

.toolbar button {
  background: #2563eb;
  border: 0;
  border-radius: 6px;
  color: white;
  cursor: pointer;
  height: 38px;
  padding: 0 14px;
}

.workspace {
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  margin: 0 auto;
  max-width: 1180px;
}

textarea,
.preview {
  background: white;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  min-height: 620px;
  padding: 14px;
}

textarea {
  resize: vertical;
}

@media (max-width: 860px) {
  .workspace {
    grid-template-columns: 1fr;
  }
}
```

Create `extension/README.md`:

```md
# MD To Word Extension

Browser extension UI for pasting Markdown and exporting Word documents through the conversion backend.

## Development

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```
```

- [ ] **Step 4: Install and build**

Run:

```bash
cd extension
npm install
npm run build
```

Expected: TypeScript and Vite build successfully.

- [ ] **Step 5: Commit**

```bash
git add extension/package.json extension/package-lock.json extension/tsconfig.json extension/vite.config.ts extension/index.html extension/public/manifest.json extension/src/types.ts extension/src/background.ts extension/src/main.tsx extension/src/App.tsx extension/src/styles.css extension/README.md
git commit -m "feat: add extension skeleton"
```

## Task 9: Extension Storage and Backend API Client

**Files:**
- Create: `extension/src/storage.ts`
- Create: `extension/src/api.ts`
- Modify: `extension/src/App.tsx`

- [ ] **Step 1: Add storage helper**

Create `extension/src/storage.ts`:

```ts
const SERVICE_URL_KEY = 'mdToWord.serviceUrl';
const DRAFT_KEY = 'mdToWord.draft';

const fallbackStorage = new Map<string, string>();

function hasChromeStorage(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome.storage?.local);
}

export async function loadServiceUrl(): Promise<string> {
  if (!hasChromeStorage()) {
    return fallbackStorage.get(SERVICE_URL_KEY) ?? 'http://127.0.0.1:8000';
  }

  const result = await chrome.storage.local.get(SERVICE_URL_KEY);
  return result[SERVICE_URL_KEY] ?? 'http://127.0.0.1:8000';
}

export async function saveServiceUrl(value: string): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(SERVICE_URL_KEY, value);
    return;
  }

  await chrome.storage.local.set({ [SERVICE_URL_KEY]: value });
}

export async function loadDraft(): Promise<string> {
  if (!hasChromeStorage()) {
    return fallbackStorage.get(DRAFT_KEY) ?? '';
  }

  const result = await chrome.storage.local.get(DRAFT_KEY);
  return result[DRAFT_KEY] ?? '';
}

export async function saveDraft(value: string): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(DRAFT_KEY, value);
    return;
  }

  await chrome.storage.local.set({ [DRAFT_KEY]: value });
}
```

- [ ] **Step 2: Add API client**

Create `extension/src/api.ts`:

```ts
import type { ConvertRequest } from './types';

const DOCX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export interface ConversionApiError {
  error: string;
  message: string;
  details: string[];
}

export async function checkHealth(serviceUrl: string): Promise<boolean> {
  try {
    const response = await fetch(`${trimTrailingSlash(serviceUrl)}/health`);
    if (!response.ok) {
      return false;
    }
    const body = await response.json();
    return body.status === 'ok' && body.engine === 'pandoc';
  } catch {
    return false;
  }
}

export async function convertToDocx(serviceUrl: string, request: ConvertRequest): Promise<Blob> {
  const response = await fetch(`${trimTrailingSlash(serviceUrl)}/convert`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    let apiError: ConversionApiError = {
      error: 'conversion_failed',
      message: `Conversion failed with status ${response.status}.`,
      details: [],
    };

    try {
      apiError = await response.json();
    } catch {
      // Keep fallback error.
    }

    throw new Error([apiError.message, ...apiError.details].filter(Boolean).join('\n'));
  }

  return response.blob();
}

export function downloadDocx(blob: Blob, filename: string): void {
  const docxBlob = blob.type === DOCX_MEDIA_TYPE ? blob : new Blob([blob], { type: DOCX_MEDIA_TYPE });
  const url = URL.createObjectURL(docxBlob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}
```

- [ ] **Step 3: Connect helpers to UI**

Replace `extension/src/App.tsx` with:

```tsx
import { useEffect, useMemo, useState } from 'react';

import { checkHealth, convertToDocx, downloadDocx } from './api';
import { loadDraft, loadServiceUrl, saveDraft, saveServiceUrl } from './storage';
import type { ServiceStatus } from './types';

const DEFAULT_FILENAME = 'md-to-word.docx';

export function App() {
  const [markdown, setMarkdown] = useState('');
  const [serviceUrl, setServiceUrl] = useState('http://127.0.0.1:8000');
  const [status, setStatus] = useState<ServiceStatus>('unknown');
  const [message, setMessage] = useState('');
  const canExport = useMemo(() => markdown.trim().length > 0, [markdown]);

  useEffect(() => {
    void Promise.all([loadDraft(), loadServiceUrl()]).then(([savedDraft, savedServiceUrl]) => {
      setMarkdown(savedDraft);
      setServiceUrl(savedServiceUrl);
    });
  }, []);

  async function handleMarkdownChange(value: string) {
    setMarkdown(value);
    await saveDraft(value);
  }

  async function handleServiceUrlChange(value: string) {
    setServiceUrl(value);
    await saveServiceUrl(value);
  }

  async function handleHealthCheck() {
    setMessage('');
    const available = await checkHealth(serviceUrl);
    setStatus(available ? 'available' : 'unavailable');
    setMessage(available ? 'Conversion service is available.' : 'Conversion service is unavailable.');
  }

  async function handleExport() {
    if (!canExport) {
      setMessage('Paste Markdown before exporting.');
      return;
    }

    setMessage('Exporting DOCX...');
    try {
      const blob = await convertToDocx(serviceUrl, {
        title: 'md-to-word',
        markdown,
        options: {
          filename: DEFAULT_FILENAME,
        },
      });
      downloadDocx(blob, DEFAULT_FILENAME);
      setMessage('DOCX download started.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Export failed.');
    }
  }

  return (
    <main className="app">
      <header className="toolbar">
        <div>
          <h1>MD To Word</h1>
          <p>Service: {status}</p>
        </div>
        <div className="actions">
          <input
            aria-label="Conversion service URL"
            value={serviceUrl}
            onChange={(event) => void handleServiceUrlChange(event.target.value)}
          />
          <button type="button" className="secondary" onClick={() => void handleHealthCheck()}>
            Check
          </button>
          <button type="button" disabled={!canExport} onClick={() => void handleExport()}>
            Export DOCX
          </button>
        </div>
      </header>
      {message ? <p className="message">{message}</p> : null}
      <section className="workspace">
        <textarea
          aria-label="Markdown input"
          placeholder="Paste Markdown here"
          value={markdown}
          onChange={(event) => void handleMarkdownChange(event.target.value)}
        />
        <article className="preview" aria-label="Preview" />
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Build extension**

Run:

```bash
cd extension
npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add extension/src/storage.ts extension/src/api.ts extension/src/App.tsx
git commit -m "feat: connect extension to conversion service"
```

## Task 10: Extension Markdown Preview

**Files:**
- Create: `extension/src/preview.tsx`
- Modify: `extension/src/App.tsx`
- Modify: `extension/src/styles.css`

- [ ] **Step 1: Add preview renderer**

Create `extension/src/preview.tsx`:

```tsx
import MarkdownIt from 'markdown-it';
import katex from 'katex';
import 'katex/dist/katex.min.css';

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

export function MarkdownPreview({ value }: { value: string }) {
  const html = renderMarkdownWithMath(value);
  return <article className="preview" aria-label="Preview" dangerouslySetInnerHTML={{ __html: html }} />;
}

function renderMarkdownWithMath(value: string): string {
  const protectedMath: string[] = [];
  const protectedValue = value
    .replace(/\$\$([\s\S]+?)\$\$/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, true)) - 1;
      return `@@MATH_${index}@@`;
    })
    .replace(/\$([^$\n]+?)\$/g, (_match, formula: string) => {
      const index = protectedMath.push(renderFormula(formula, false)) - 1;
      return `@@MATH_${index}@@`;
    });

  let html = markdown.render(protectedValue);
  protectedMath.forEach((formulaHtml, index) => {
    html = html.replace(`@@MATH_${index}@@`, formulaHtml);
  });
  return html;
}

function renderFormula(formula: string, displayMode: boolean): string {
  try {
    return katex.renderToString(formula.trim(), {
      displayMode,
      throwOnError: false,
    });
  } catch {
    return `<code>${escapeHtml(formula)}</code>`;
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
```

- [ ] **Step 2: Use preview component**

In `extension/src/App.tsx`, import the preview:

```tsx
import { MarkdownPreview } from './preview';
```

Replace the empty preview article:

```tsx
<MarkdownPreview value={markdown} />
```

- [ ] **Step 3: Add preview styling**

Append to `extension/src/styles.css`:

```css
.actions {
  align-items: center;
  display: flex;
  gap: 8px;
}

.actions input {
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  height: 38px;
  min-width: 260px;
  padding: 0 10px;
}

.toolbar button.secondary {
  background: #e2e8f0;
  color: #1f2937;
}

.toolbar button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.message {
  color: #334155;
  font-size: 13px;
  margin: 0 auto 12px;
  max-width: 1180px;
}

.preview {
  overflow: auto;
}

.preview h1,
.preview h2,
.preview h3 {
  line-height: 1.25;
}

.preview table {
  border-collapse: collapse;
  width: 100%;
}

.preview th,
.preview td {
  border: 1px solid #cbd5e1;
  padding: 8px;
  text-align: left;
}

.preview th {
  background: #f1f5f9;
}

.katex-display {
  overflow-x: auto;
  overflow-y: hidden;
}
```

- [ ] **Step 4: Build extension**

Run:

```bash
cd extension
npm run build
```

Expected: build succeeds and preview compiles.

- [ ] **Step 5: Commit**

```bash
git add extension/src/preview.tsx extension/src/App.tsx extension/src/styles.css
git commit -m "feat: preview markdown formulas and tables"
```

## Task 11: End-to-End Local Verification

**Files:**
- Modify: `backend/tests/test_pandoc_runner.py`
- Modify: `extension/README.md`

- [ ] **Step 1: Run backend tests**

Run:

```bash
cd backend
python -m pytest -v
```

Expected: all backend tests PASS.

- [ ] **Step 2: Run extension build**

Run:

```bash
cd extension
npm run build
```

Expected: TypeScript and Vite build PASS.

- [ ] **Step 3: Start backend locally**

Run:

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: server starts and listens on `http://127.0.0.1:8000`.

- [ ] **Step 4: Convert sample with curl**

In a second terminal, create `backend/tests/fixtures/request.json`:

```json
{
  "title": "sample",
  "markdown": "# 示例\n\n质能方程是 $E = mc^2$。\n\n| 名称 | 公式 |\n|---|---|\n| 动能 | $E_k = \\frac{1}{2}mv^2$ |\n",
  "options": {
    "filename": "sample.docx"
  }
}
```

Run:

```bash
curl -X POST http://127.0.0.1:8000/convert \
  -H "Content-Type: application/json" \
  --data-binary @backend/tests/fixtures/request.json \
  --output /tmp/md-to-word-sample.docx
```

Expected: `/tmp/md-to-word-sample.docx` exists and starts with DOCX zip bytes.

- [ ] **Step 5: Inspect DOCX XML for formulas and tables**

Run:

```bash
python - <<'PY'
from pathlib import Path
import zipfile

path = Path("/tmp/md-to-word-sample.docx")
assert path.exists(), "DOCX file was not created"
with zipfile.ZipFile(path) as archive:
    xml = archive.read("word/document.xml").decode("utf-8")
assert "<m:oMath" in xml or "<m:oMathPara" in xml, "No editable Word math found"
assert "<w:tbl>" in xml, "No Word table found"
print("DOCX contains editable math and a native Word table")
PY
```

Expected:

```text
DOCX contains editable math and a native Word table
```

- [ ] **Step 6: Update extension README with local loading steps**

Append to `extension/README.md`:

```md

## Load in Chrome or Edge

1. Run `npm run build`.
2. Open `chrome://extensions` or `edge://extensions`.
3. Enable developer mode.
4. Load the `extension/dist` directory as an unpacked extension.
5. Click the extension icon to open the side panel.
6. Set the service URL to `http://127.0.0.1:8000`.
```

- [ ] **Step 7: Commit**

```bash
git add extension/README.md
git commit -m "docs: add local verification steps"
```

## Task 12: Final Documentation and Release Checklist

**Files:**
- Modify: `README.md`
- Create: `docs/release-checklist.md`

- [ ] **Step 1: Update root README with final workflows**

Add this section to `README.md`:

```md

## Verification

Backend:

```bash
cd backend
python -m pytest -v
```

Extension:

```bash
cd extension
npm run build
```

Docker:

```bash
cd backend
docker build -t md-to-word-backend .
```

Manual DOCX acceptance:

- Open the generated `.docx` in Microsoft Word.
- Confirm text is readable.
- Confirm tables are Word-native tables.
- Confirm formulas can be edited as Word equations.
```

- [ ] **Step 2: Add release checklist**

Create `docs/release-checklist.md`:

```md
# Release Checklist

- [ ] Backend tests pass with Pandoc installed.
- [ ] Extension build passes.
- [ ] Docker image builds.
- [ ] `/health` returns `{"status":"ok","engine":"pandoc"}`.
- [ ] `/convert` returns a valid `.docx`.
- [ ] Sample `.docx` opens in Microsoft Word.
- [ ] Word formulas are editable equations.
- [ ] Markdown tables become Word-native tables.
- [ ] Extension can save service URL.
- [ ] Extension can export using local backend.
- [ ] Render deployment health check passes.
```

- [ ] **Step 3: Run final verification**

Run:

```bash
cd backend
python -m pytest -v
```

Expected: all backend tests PASS.

Run:

```bash
cd extension
npm run build
```

Expected: extension build PASS.

Run:

```bash
cd backend
docker build -t md-to-word-backend .
```

Expected: Docker build PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/release-checklist.md
git commit -m "docs: add release checklist"
```

## Self-Review Checklist

- Spec coverage: This plan covers manual paste input, text, formulas, tables, editable Word formulas, local/cloud backend URL, FastAPI backend, Pandoc conversion, Docker packaging, and Render deployment notes.
- Explicitly out of scope: automatic AI page extraction, images, PDF export, code highlighting, merged cells, nested tables, and batch export.
- Placeholder scan: no task contains placeholder markers or deferred implementation instructions.
- Type consistency: API request uses `title`, `markdown`, and `options.filename` consistently across backend and extension.
- Verification: backend tests, extension build, Docker build, and DOCX XML inspection are included.
