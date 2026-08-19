import http.client
import threading
from pathlib import Path

from agent.sandbox.worker import FileJobStore, SandboxWorker
from agent.sandbox.worker_http import WorkerHTTPServer, WorkerRequestHandler


class _RunnerMustNotRun:
    def execute(self, job, artifacts):
        del job, artifacts
        raise AssertionError("unauthorized or invalid requests must not reach the runner")


def _start_server(tmp_path: Path):
    server = WorkerHTTPServer(("127.0.0.1", 0), WorkerRequestHandler)
    server.worker = SandboxWorker(
        credential="worker-secret",
        runner=_RunnerMustNotRun(),
        store=FileJobStore(tmp_path / "jobs"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _post(server: WorkerHTTPServer, body: bytes, authorization: str) -> int:
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(
            "POST",
            "/v1/jobs",
            body=body,
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_worker_http_authenticates_before_parsing_request_body(tmp_path: Path):
    server, thread = _start_server(tmp_path)
    try:
        assert _post(server, b"not-json", "Bearer wrong") == 401
        assert _post(server, b"not-json", "Bearer worker-secret") == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
