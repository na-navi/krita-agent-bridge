"""Tests for fast Krita test-mode bootstrap."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from krita_agent_bridge.bootstrap import bootstrap_test_mode


class _BootstrapHandler(BaseHTTPRequestHandler):
    routes: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    posts: list[str] = []
    document_open = False

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.posts.append(self.path.rstrip("/"))
        if self.path.rstrip("/") == "/api/document/create":
            type(self).document_open = True
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        self._handle("POST")

    def _handle(self, method: str) -> None:
        if method == "GET" and self.path.rstrip("/") == "/api/status":
            body = {
                "running": True,
                "document_open": type(self).document_open,
                "ai_diffusion_available": True,
            }
            status = 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return
        status, body = self.routes.get((method, self.path.rstrip("/")), (404, {"error": "not found"}))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        return


def test_bootstrap_creates_document_and_waits_for_readiness(tmp_path: Path) -> None:
    _BootstrapHandler.posts = []
    _BootstrapHandler.document_open = False
    _BootstrapHandler.routes = {
        ("POST", "/api/document/create"): (200, {"status": "ok"}),
        ("GET", "/api/diffusion/styles"): (200, {"styles": ["default"]}),
        ("GET", "/object_info/CheckpointLoaderSimple"): (
            200,
            {
                "CheckpointLoaderSimple": {
                    "input": {"required": {"ckpt_name": [["model.safetensors"]]}}
                }
            },
        ),
        ("GET", "/queue"): (200, {"queue_running": [], "queue_pending": []}),
    }
    server = HTTPServer(("127.0.0.1", 0), _BootstrapHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exe = tmp_path / "krita.exe"
    exe.write_text("", encoding="utf-8")
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        result = bootstrap_test_mode(
            krita_exe=exe,
            krita_api=base_url,
            comfyui_api=base_url,
            timeout=1,
            interval=0.01,
        )
    finally:
        server.shutdown()

    assert result.ok
    assert result.started_krita is False
    assert result.document_created is True
    assert "/api/document/create" in _BootstrapHandler.posts
