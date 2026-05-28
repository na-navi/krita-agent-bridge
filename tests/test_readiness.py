"""Tests for generation readiness gating."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.readiness import ReadinessProbe


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}

    def do_GET(self) -> None:  # noqa: N802
        key = self.path.split("?")[0].rstrip("/")
        status, body = self.routes.get(key, (404, '{"error":"not found"}'))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        return


@pytest.fixture()
def server() -> str:
    _Handler.routes.clear()
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_readiness_requires_all_generation_dependencies(server: str) -> None:
    _Handler.routes["/api/status"] = (
        200,
        json.dumps({
            "running": True,
            "document_open": True,
            "ai_diffusion_available": True,
        }),
    )
    _Handler.routes["/api/diffusion/styles"] = (200, json.dumps({"styles": ["built-in/anima.json"]}))
    _Handler.routes["/object_info/CheckpointLoaderSimple"] = (
        200,
        json.dumps({
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["model.safetensors"]]}}
            }
        }),
    )
    _Handler.routes["/queue"] = (200, json.dumps({"queue_running": [], "queue_pending": []}))

    report = ReadinessProbe(krita_api=server, comfyui_api=server).check()

    assert report.ready
    assert {item.name for item in report.checks} == {
        "krita_bridge",
        "ai_diffusion_styles",
        "comfyui_object_info",
        "comfyui_queue",
    }


def test_readiness_reports_document_gap(server: str) -> None:
    _Handler.routes["/api/status"] = (
        200,
        json.dumps({
            "running": True,
            "document_open": False,
            "ai_diffusion_available": True,
        }),
    )
    _Handler.routes["/api/diffusion/styles"] = (200, json.dumps({"styles": ["built-in/anima.json"]}))
    _Handler.routes["/object_info/CheckpointLoaderSimple"] = (
        200,
        json.dumps({
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["model.safetensors"]]}}
            }
        }),
    )
    _Handler.routes["/queue"] = (200, json.dumps({"queue_running": [], "queue_pending": []}))

    report = ReadinessProbe(krita_api=server, comfyui_api=server).check()

    assert not report.ready
    assert report.checks[0].name == "krita_bridge"
    assert "document" in report.checks[0].detail
