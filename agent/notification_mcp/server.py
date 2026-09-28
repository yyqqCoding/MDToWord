"""Local MCP server for terminal Agent email notifications."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

TERMINAL = {"completed", "failed", "needs_human", "security_rejected", "budget_exhausted", "cancelled"}


def _config(name: str, *, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise RuntimeError(f"missing configuration: {name}")
    return value


def _db() -> sqlite3.Connection:
    path = Path(os.environ.get("NOTIFICATION_DB_PATH", "/var/lib/mdtoword/notifications.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("create table if not exists sent_notifications (notification_id text primary key, provider_message_id text not null, sent_at text not null)")
    return conn


def _validate(args: dict[str, Any]) -> None:
    notification_id = args.get("notification_id")
    status = args.get("status")
    if not isinstance(notification_id, str) or len(notification_id) > 180 or not notification_id.endswith(":email"):
        raise ValueError("invalid notification_id")
    if status not in TERMINAL:
        raise ValueError("status is not terminal")
    for key in ("to", "from", "cc", "bcc", "attachment", "path", "headers", "html"):
        if key in args:
            raise ValueError(f"unsupported field: {key}")
    for key in ("pr_url", "issue_url", "trace_url"):
        value = args.get(key)
        if value is not None and urlparse(str(value)).scheme not in {"http", "https"}:
            raise ValueError(f"invalid {key}")


def _send_resend(args: dict[str, Any]) -> str:
    api_key = _config("RESEND_API_KEY")
    sender = _config("NOTIFY_EMAIL_FROM")
    recipient = _config("NOTIFY_EMAIL_TO")
    status = args["status"]
    subject = f"MD To Word Agent: {status} ({args['run_ref']})"
    links = "".join(f"<li>{key}: <a href='{args[key]}'>{args[key]}</a></li>" for key in ("pr_url", "issue_url", "trace_url") if args.get(key))
    html = f"<h2>MD To Word Agent</h2><p>状态：{status}</p><p>运行：{args['run_ref']}</p><p>路由：{args.get('route') or 'unknown'}</p><p>类别：{args.get('category') or 'unknown'}</p><p>错误码：{args.get('failure_code') or 'none'}</p><ul>{links}</ul>"
    for attempt in range(3):
        try:
            response = httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Idempotency-Key": args["notification_id"]}, json={"from": sender, "to": [recipient], "subject": subject, "html": html}, timeout=15)
            if response.status_code < 500 and response.status_code != 429:
                response.raise_for_status()
                return str(response.json().get("id", "unknown"))
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise
        if attempt < 2:
            time.sleep(1 if attempt == 0 else 2)
    raise RuntimeError("notification provider unavailable")


mcp = FastMCP("mdtoword-notifications", host="127.0.0.1", port=int(os.environ.get("NOTIFY_MCP_PORT", "8091")))


@mcp.tool()
def send_email(notification_id: str, run_ref: str, status: str, route: str | None = None,
               category: str | None = None, pr_url: str | None = None,
               issue_url: str | None = None, trace_url: str | None = None,
               failure_code: str | None = None) -> dict[str, str]:
    """Send one terminal Agent notification to the configured maintainer email."""
    args = locals()
    _validate(args)
    conn = _db()
    try:
        existing = conn.execute("select provider_message_id from sent_notifications where notification_id=?", (notification_id,)).fetchone()
        if existing:
            return {"status": "already_sent", "notification_id": notification_id, "provider_message_id": existing[0]}
        message_id = _send_resend(args)
        conn.execute("insert into sent_notifications values (?, ?, datetime('now'))", (notification_id, message_id))
        conn.commit()
        return {"status": "sent", "notification_id": notification_id, "provider_message_id": message_id}
    finally:
        conn.close()


@mcp.custom_route("/tools/send_email", methods=["POST"])
async def send_email_http(request: Request) -> JSONResponse:
    """Private local bridge used by the trusted Agent client."""
    if request.headers.get("authorization") != f"Bearer {_config('NOTIFY_MCP_TOKEN')}":
        return JSONResponse({"error_code": "unauthorized"}, status_code=401)
    try:
        payload = await request.json()
        result = send_email(**payload)
    except ValueError as exc:
        return JSONResponse({"error_code": "invalid_notification", "detail": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"error_code": "notification_provider_unavailable"}, status_code=503)
    return JSONResponse(result)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
