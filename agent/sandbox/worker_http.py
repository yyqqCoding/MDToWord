"""以最小 HTTP 接口暴露 Sandbox Worker，不复用业务 API 进程。"""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from agent.domain.errors import (
    AgentError,
    SandboxAuthenticationError,
    SandboxJobConflictError,
)
from agent.sandbox.docker_runner import DockerRunner
from agent.sandbox.worker import FileJobStore, SandboxWorker


_MAX_REQUEST_BYTES = 71_000_000


class WorkerHTTPServer(ThreadingHTTPServer):
    worker: SandboxWorker


class WorkerRequestHandler(BaseHTTPRequestHandler):
    server: WorkerHTTPServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/jobs":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        if length < 1 or length > _MAX_REQUEST_BYTES:
            # 在读取 body 前拒绝超限请求，避免无界内存占用。
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            result = self.server.worker.execute(
                self.headers.get("Authorization"),
                payload,
                idempotency_key=self.headers.get("Idempotency-Key"),
            )
        except SandboxAuthenticationError as exc:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": exc.error_code})
            return
        except SandboxJobConflictError as exc:
            self._send(HTTPStatus.CONFLICT, {"error": exc.error_code})
            return
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        except AgentError as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": exc.error_code})
            return
        self._send(HTTPStatus.OK, result.model_dump(mode="json"))

    def log_message(self, format: str, *args: object) -> None:
        # 默认 HTTP 日志可能包含路径或可控文本；外层服务另记结构化摘要。
        del format, args

    def _send(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def build_server(environ: dict[str, str] | None = None) -> WorkerHTTPServer:
    """只读取 SANDBOX_* 配置组装 Worker，避免业务 Secret 进入执行边界。"""

    values = environ if environ is not None else cast(dict[str, str], os.environ)
    credential = values.get("SANDBOX_WORKER_CREDENTIAL", "")
    image_digest = values.get("SANDBOX_IMAGE_DIGEST", "")
    if not credential:
        raise RuntimeError("missing required configuration: SANDBOX_WORKER_CREDENTIAL")
    if not image_digest:
        raise RuntimeError("missing required configuration: SANDBOX_IMAGE_DIGEST")
    host = values.get("SANDBOX_BIND_HOST", "127.0.0.1")
    try:
        port = int(values.get("SANDBOX_BIND_PORT", "8090"))
    except ValueError as exc:
        raise RuntimeError("SANDBOX_BIND_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise RuntimeError("SANDBOX_BIND_PORT is out of range")
    root = Path(values.get("SANDBOX_JOB_ROOT", "var/sandbox-jobs")).resolve()
    worker = SandboxWorker(
        credential=credential,
        runner=DockerRunner(
            image_digest=image_digest,
            work_root=root / "work",
        ),
        store=FileJobStore(root / "results"),
    )
    server = WorkerHTTPServer((host, port), WorkerRequestHandler)
    server.worker = worker
    return server


def main() -> None:
    server = build_server()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
