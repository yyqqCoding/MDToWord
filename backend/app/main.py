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
