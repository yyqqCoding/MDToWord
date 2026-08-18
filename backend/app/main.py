import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.feedback_rate_limit import (
    ClientIpUnavailableError,
    FeedbackRateLimiter,
    FeedbackRateLimitPolicy,
    resolve_cloudflare_client_ip,
)
from app.models import (
    ConversionErrorResponse,
    ConvertRequest,
    FeedbackRequest,
    FeedbackResponse,
)
from app.pandoc_runner import ConversionError, convert_markdown_to_docx
from app.settings import settings

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


class FeedbackStorageError(RuntimeError):
    """反馈存储失败；异常内容不得直接回显给公网调用方。"""


def _new_feedback_rate_limiter() -> FeedbackRateLimiter:
    return FeedbackRateLimiter(
        FeedbackRateLimitPolicy(
            per_minute=settings.feedback_rate_per_minute,
            per_hour=settings.feedback_rate_per_hour,
            per_day=settings.feedback_rate_per_day,
            global_per_hour=settings.feedback_global_rate_per_hour,
        )
    )


app = FastAPI(title="MD To Word Converter")
# 当前 Render 仅运行一个 Uvicorn worker；应用内所有反馈请求共享同一个限流器。
app.state.feedback_rate_limiter = _new_feedback_rate_limiter()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine": "pandoc",
    }


@app.head("/health", include_in_schema=False)
def health_head() -> Response:
    """兼容 UptimeRobot 默认发送的 HEAD 健康检查。"""
    return Response(status_code=200)


@app.post("/convert")
def convert(request: ConvertRequest) -> Response:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            docx_bytes = convert_markdown_to_docx(
                request.markdown,
                Path(tmp),
            )
    except ConversionError as exc:
        error = ConversionErrorResponse(
            error="conversion_failed",
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(
            status_code=400,
            content=error.model_dump(),
        )

    headers = {
        "Content-Disposition": (
            f'attachment; filename="{request.options.filename}"'
        ),
    }

    return Response(
        content=docx_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers=headers,
    )


async def _insert_feedback(payload: dict[str, str]) -> None:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/feedback",
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise FeedbackStorageError("feedback storage request failed") from exc

    if response.status_code >= 400:
        raise FeedbackStorageError("feedback storage rejected the request")


@app.post("/feedback")
async def feedback(
    feedback_request: FeedbackRequest,
    request: Request,
) -> FeedbackResponse:
    try:
        ip_key = resolve_cloudflare_client_ip(
            request.headers.get("CF-Connecting-IP")
        )
    except ClientIpUnavailableError:
        return JSONResponse(
            status_code=503,
            headers={"Cache-Control": "no-store"},
            content={
                "success": False,
                "id": None,
                "error": "client_ip_unavailable",
                "message": "暂时无法验证请求来源，请稍后重试",
            },
        )

    limiter: FeedbackRateLimiter = request.app.state.feedback_rate_limiter
    decision = await limiter.consume(ip_key)
    if not decision.allowed:
        retry_after = decision.retry_after_seconds or 1
        return JSONResponse(
            status_code=429,
            headers={
                "Retry-After": str(retry_after),
                "Cache-Control": "no-store",
            },
            content={
                "success": False,
                "id": None,
                "error": "rate_limited",
                "message": "提交过于频繁，请稍后再试",
            },
        )

    feedback_id = str(uuid.uuid4())

    payload = {
        "id": feedback_id,
        "feedback_type": feedback_request.feedback_type,
        "markdown_content": feedback_request.markdown_content,
        "description": feedback_request.description,
        "contact": feedback_request.contact,
    }

    try:
        # 限流额度已在进程锁内消费；数据库调用必须位于锁外，失败时也不返还额度。
        await _insert_feedback(payload)
    except FeedbackStorageError:
        return JSONResponse(
            status_code=502,
            headers={"Cache-Control": "no-store"},
            content={
                "success": False,
                "id": None,
                "error": "feedback_storage_unavailable",
                "message": "反馈服务暂时不可用，请稍后重试",
            },
        )

    return FeedbackResponse(
        success=True,
        id=feedback_id,
    )
