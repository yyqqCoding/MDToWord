import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.models import ConversionErrorResponse, ConvertRequest, FeedbackRequest, FeedbackResponse
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


@app.post("/feedback")
async def feedback(request: FeedbackRequest) -> FeedbackResponse:
    feedback_id = str(uuid.uuid4())
    payload = {
        "id": feedback_id,
        "feedback_type": request.feedback_type,
        "markdown_content": request.markdown_content,
        "description": request.description,
        "contact": request.contact,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.supabase_url}/rest/v1/feedback",
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=payload,
            )
            if resp.status_code >= 400:
                detail = resp.text[:200] if resp.text else "no response body"
                return JSONResponse(
                    status_code=502,
                    content={
                        "success": False,
                        "id": None,
                        "message": f"supabase {resp.status_code}: {detail}",
                    },
                )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "id": None,
                "message": f"request error: {type(exc).__name__}: {exc}",
            },
        )

    return FeedbackResponse(success=True, id=feedback_id)
