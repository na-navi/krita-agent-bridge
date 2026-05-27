"""Tests for the ComfyUI adapter (Issue #6).

Covers:
- /object_info inspection
- /queue status retrieval
- /history retrieval
- Prompt validation against node schemas
- Output file resolution
- Error classification: connection / validation / execution
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.comfyui import (
    AdapterError,
    ComfyUIAdapter,
    NodeSchema,
)


# ---------------------------------------------------------------------------
# Stub HTTP server (shared with test_cli but standalone here)
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}
    post_routes: dict[str, tuple[int, str]] = {}
    last_post_body: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802
        key = self.path.split("?")[0].rstrip("/")
        if key in self.routes:
            status, body = self.routes[key]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        _StubHandler.last_post_body[self.path] = json.loads(raw) if raw else {}
        key = self.path.rstrip("/")
        routes = self.post_routes
        if key in routes:
            status, body = routes[key]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def adapter(mock_server: str) -> ComfyUIAdapter:
    return ComfyUIAdapter(base_url=mock_server, timeout=2.0)


# Sample object_info data
SAMPLE_OBJECT_INFO = json.dumps({
    "KSampler": {
        "input": {"required": {"model": ["MODEL", {}], "steps": ["INT", {"default": 20}]}},
        "output": [{"type": "LATENT", "name": "LATENT"}],
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"default": ""}]}},
        "output": [{"type": "CONDITIONING", "name": "CONDITIONING"}],
    },
})

_OBJ = json.loads(SAMPLE_OBJECT_INFO)  # dict version for building responses


# ---------------------------------------------------------------------------
# /object_info tests
# ---------------------------------------------------------------------------


class TestObjectInfo:
    def test_all_nodes(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info"] = (200, SAMPLE_OBJECT_INFO)
        result = adapter.object_info()
        assert result.ok
        assert result.error == AdapterError.NONE
        assert "KSampler" in result.data
        assert "CLIPTextEncode" in result.data

    def test_filtered_node(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/KSampler"] = (
            200, json.dumps({"KSampler": json.loads(SAMPLE_OBJECT_INFO)["KSampler"]})
        )
        result = adapter.object_info(node_filter="KSampler")
        assert result.ok
        assert "KSampler" in result.data

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.object_info()
        assert not result.ok
        assert result.error == AdapterError.CONNECTION

    def test_invalid_response(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info"] = (200, '"not a dict"')
        result = adapter.object_info()
        assert not result.ok
        assert result.error == AdapterError.VALIDATION


# ---------------------------------------------------------------------------
# get_node_schema tests
# ---------------------------------------------------------------------------


class TestGetNodeSchema:
    def test_existing_node(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/KSampler"] = (
            200, json.dumps({"KSampler": json.loads(SAMPLE_OBJECT_INFO)["KSampler"]})
        )
        result = adapter.get_node_schema("KSampler")
        assert result.ok
        schema = result.data
        assert isinstance(schema, NodeSchema)
        assert schema.name == "KSampler"
        assert "model" in schema.inputs
        assert len(schema.outputs) > 0

    def test_missing_node(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/NonExistentNode"] = (200, '{}')
        result = adapter.get_node_schema("NonExistentNode")
        assert not result.ok
        assert result.error == AdapterError.VALIDATION
        assert "NonExistentNode" in result.message

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.get_node_schema("KSampler")
        assert not result.ok
        assert result.error == AdapterError.CONNECTION


# ---------------------------------------------------------------------------
# /queue tests
# ---------------------------------------------------------------------------


class TestQueueStatus:
    def test_queue_ok(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/queue"] = (200, '{"queue_running":[],"queue_pending":[]}')
        result = adapter.queue_status()
        assert result.ok
        assert result.data["queue_running"] == []
        assert result.data["queue_pending"] == []

    def test_queue_with_items(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/queue"] = (
            200, '{"queue_running":[{"prompt_id":"abc"}],"queue_pending":[]}'
        )
        result = adapter.queue_status()
        assert result.ok
        assert len(result.data["queue_running"]) == 1

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.queue_status()
        assert not result.ok
        assert result.error == AdapterError.CONNECTION


# ---------------------------------------------------------------------------
# /history tests
# ---------------------------------------------------------------------------


class TestHistory:
    def test_all_history(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/history"] = (200, '{"prompt1":{"status":{"status_str":"success"}}}')
        result = adapter.history()
        assert result.ok
        assert "prompt1" in result.data

    def test_specific_history(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/history/prompt1"] = (
            200, '{"prompt1":{"status":{"status_str":"success"}}}'
        )
        result = adapter.history(prompt_id="prompt1")
        assert result.ok

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.history()
        assert not result.ok
        assert result.error == AdapterError.CONNECTION


# ---------------------------------------------------------------------------
# Prompt validation tests
# ---------------------------------------------------------------------------


class TestValidatePrompt:
    def test_valid_workflow(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info"] = (200, SAMPLE_OBJECT_INFO)
        workflow = {
            "nodes": [
                {"type": "KSampler", "id": 1},
                {"type": "CLIPTextEncode", "id": 2},
            ]
        }
        result = adapter.validate_prompt(workflow)
        assert result.ok

    def test_unknown_node_type(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info"] = (200, SAMPLE_OBJECT_INFO)
        workflow = {
            "nodes": [
                {"type": "KSampler", "id": 1},
                {"type": "FakeNode", "id": 2},
            ]
        }
        result = adapter.validate_prompt(workflow)
        assert not result.ok
        assert result.error == AdapterError.VALIDATION
        assert "FakeNode" in result.message

    def test_empty_workflow(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info"] = (200, SAMPLE_OBJECT_INFO)
        result = adapter.validate_prompt({"nodes": []})
        assert result.ok

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.validate_prompt({"nodes": [{"type": "KSampler"}]})
        assert not result.ok
        assert result.error == AdapterError.CONNECTION


# ---------------------------------------------------------------------------
# Output resolution tests
# ---------------------------------------------------------------------------


class TestResolveOutputs:
    def test_with_images(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/history/prompt1"] = (200, json.dumps({
            "prompt1": {
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "ComfyUI_00001.png", "subfolder": "", "type": "output"},
                            {"filename": "ComfyUI_00002.png", "subfolder": "batch", "type": "output"},
                        ]
                    }
                }
            }
        }))
        result = adapter.resolve_outputs("prompt1")
        assert result.ok
        assert len(result.data) == 2
        # All paths must be absolute
        for path in result.data:
            assert os.path.isabs(path), f"Path not absolute: {path}"

    def test_with_subfolder(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/history/prompt1"] = (200, json.dumps({
            "prompt1": {
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "test.png", "subfolder": "sub/dir", "type": "output"},
                        ]
                    }
                }
            }
        }))
        result = adapter.resolve_outputs("prompt1")
        assert result.ok
        assert len(result.data) == 1
        assert "sub" in result.data[0]

    def test_empty_outputs(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/history/prompt1"] = (200, json.dumps({
            "prompt1": {"outputs": {}}
        }))
        result = adapter.resolve_outputs("prompt1")
        assert result.ok
        assert result.data == []

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.resolve_outputs("prompt1")
        assert not result.ok
        assert result.error == AdapterError.CONNECTION


# ---------------------------------------------------------------------------
# POST /prompt — submit_prompt tests
# ---------------------------------------------------------------------------


SAMPLE_WORKFLOW = {
    "prompt": {
        "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
    },
    "client_id": "test-client",
}


class TestSubmitPrompt:
    def test_successful_submission(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/KSampler"] = (
            200, json.dumps({"KSampler": _OBJ["KSampler"]})
        )
        _StubHandler.routes["/object_info/CLIPTextEncode"] = (
            200, json.dumps({"CLIPTextEncode": _OBJ["CLIPTextEncode"]})
        )
        _StubHandler.post_routes["/prompt"] = (
            200, json.dumps({"prompt_id": "abc-123", "number": 1, "node_errors": {}})
        )
        result = adapter.submit_prompt(SAMPLE_WORKFLOW)
        assert result.ok
        assert result.data["prompt_id"] == "abc-123"
        assert "abc-123" in result.message

    def test_post_body_sent_correctly(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/KSampler"] = (
            200, json.dumps({"KSampler": _OBJ["KSampler"]})
        )
        _StubHandler.routes["/object_info/CLIPTextEncode"] = (
            200, json.dumps({"CLIPTextEncode": _OBJ["CLIPTextEncode"]})
        )
        _StubHandler.post_routes["/prompt"] = (
            200, json.dumps({"prompt_id": "xyz", "number": 1, "node_errors": {}})
        )
        result = adapter.submit_prompt(SAMPLE_WORKFLOW)
        assert result.ok
        posted = _StubHandler.last_post_body.get("/prompt", {})
        assert posted["client_id"] == "test-client"
        assert "1" in posted["prompt"]

    def test_unknown_node_type_rejected(self, adapter: ComfyUIAdapter) -> None:
        bad_workflow = {
            "prompt": {
                "1": {"class_type": "FakeNode", "inputs": {}},
            },
            "client_id": "test",
        }
        _StubHandler.routes["/object_info/FakeNode"] = (200, '{}')
        result = adapter.submit_prompt(bad_workflow)
        assert not result.ok
        assert result.error == AdapterError.VALIDATION
        assert "FakeNode" in result.message

    def test_connection_failure(self) -> None:
        adapter = ComfyUIAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.submit_prompt(SAMPLE_WORKFLOW)
        assert not result.ok
        assert result.error == AdapterError.CONNECTION

    def test_server_4xx_returns_validation_error(self, adapter: ComfyUIAdapter) -> None:
        _StubHandler.routes["/object_info/KSampler"] = (
            200, json.dumps({"KSampler": _OBJ["KSampler"]})
        )
        _StubHandler.routes["/object_info/CLIPTextEncode"] = (
            200, json.dumps({"CLIPTextEncode": _OBJ["CLIPTextEncode"]})
        )
        _StubHandler.post_routes["/prompt"] = (
            400, json.dumps({"error": "invalid prompt format"})
        )
        result = adapter.submit_prompt(SAMPLE_WORKFLOW)
        assert not result.ok
        assert result.error == AdapterError.VALIDATION


# ---------------------------------------------------------------------------
# Client POST unit tests
# ---------------------------------------------------------------------------


class TestClientPostJson:
    def test_post_success(self, mock_server: str) -> None:
        from krita_agent_bridge.client import JsonEndpointClient
        _StubHandler.post_routes["/test"] = (200, '{"received":true}')
        client = JsonEndpointClient(mock_server, timeout=2)
        result = client.post_json("/test", {"key": "value"})
        assert result.ok
        assert result.status == 200
        assert result.data == {"received": True}

    def test_post_http_error(self, mock_server: str) -> None:
        from krita_agent_bridge.client import JsonEndpointClient
        _StubHandler.post_routes["/test"] = (422, '{"error":"bad"}')
        client = JsonEndpointClient(mock_server, timeout=2)
        result = client.post_json("/test", {"key": "value"})
        assert not result.ok
        assert result.status == 422

    def test_post_connection_refused(self) -> None:
        from krita_agent_bridge.client import JsonEndpointClient
        client = JsonEndpointClient("http://127.0.0.1:1", timeout=0.5)
        result = client.post_json("/test", {"key": "value"})
        assert not result.ok
        assert result.status is None
        assert result.error is not None


