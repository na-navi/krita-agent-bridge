from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.cli import main
from krita_agent_bridge.e2e_smoke import run_smoke_workflow


class _SmokeHandler(BaseHTTPRequestHandler):
    routes: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body) if body else {}
        if self.path.rstrip("/") == "/api/document/export":
            output_path = Path(str(payload["output_path"]))
            output_path.write_bytes(b"x" * 2048)
        self._handle("POST")

    def _handle(self, method: str) -> None:
        key = (method, self.path.rstrip("/"))
        if key in self.routes:
            status, body = self.routes[key]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not found"}')

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture()
def smoke_server():
    _SmokeHandler.routes = {
        ("GET", "/api/status"): (
            200,
            {
                "running": True,
                "document_open": True,
                "ai_diffusion_available": True,
                "ai_diffusion_version": "test",
                "ai_diffusion_mode": "manual",
                "active_model": "model.safetensors",
            },
        ),
        ("GET", "/api/jobs"): (
            200,
            {
                "queued": 0,
                "executing": 0,
                "finished": 1,
                "jobs": [{"job_id": "job-1", "state": "finished", "progress": 1.0}],
            },
        ),
        ("GET", "/api/jobs/history"): (
            200,
            {"history": [{"job_id": "job-1", "output_path": "C:/tmp/generated.png"}]},
        ),
        ("GET", "/api/diffusion/styles"): (200, {"styles": ["default"]}),
        ("GET", "/object_info"): (200, {"KSampler": {}, "CheckpointLoaderSimple": {}}),
        ("GET", "/object_info/CheckpointLoaderSimple"): (
            200,
            {
                "CheckpointLoaderSimple": {
                    "input": {"required": {"ckpt_name": [["model.safetensors"]]}}
                }
            },
        ),
        ("GET", "/queue"): (200, {"queue_running": [], "queue_pending": []}),
        ("POST", "/api/document/create"): (200, {"status": "ok"}),
        ("POST", "/api/diffusion/mode"): (200, {"mode": "auto"}),
        ("POST", "/api/diffusion/generate"): (200, {"job_id": "job-1", "status": "queued"}),
        ("POST", "/api/document/import-layer"): (200, {"status": "ok"}),
        ("POST", "/api/document/export"): (200, {"status": "ok"}),
    }
    server = HTTPServer(("127.0.0.1", 0), _SmokeHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_smoke_workflow_writes_report(smoke_server: str, tmp_path: Path) -> None:
    report_path = tmp_path / "smoke_report.json"
    output_path = tmp_path / "smoke_output.png"

    result = run_smoke_workflow(
        krita_api=smoke_server,
        comfyui_api=smoke_server,
        report_path=report_path,
        output_path=output_path,
        interval=0.01,
        timeout=1.0,
    )

    assert result.ok
    assert report_path.exists()
    assert output_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert [step["step"] for step in data["steps"]][-1] == "export_canvas"


def test_smoke_cli_returns_nonzero_and_report_on_failure(tmp_path: Path) -> None:
    report_path = tmp_path / "smoke_report.json"
    output_path = tmp_path / "smoke_output.png"

    exit_code = main([
        "--krita-api",
        "http://127.0.0.1:1",
        "--comfyui-api",
        "http://127.0.0.1:1",
        "smoke",
        "--json",
        "--report",
        str(report_path),
        "--output",
        str(output_path),
        "--timeout",
        "0.01",
    ])

    assert exit_code == 1
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert data["steps"][0]["step"] == "doctor"
