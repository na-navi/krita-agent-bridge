"""Tests for the Krita document adapter MVP (Issue #4).

Covers:
- active_document() success, no-document, invalid response, connection failure
- export_canvas() sends expected JSON body, empty path validation
- import_image_as_layer() sends expected JSON body, with/without layer_name
- Connection failure classification
- No destructive public method exists on the adapter
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.krita_document import (
    DocumentError,
    DocumentInfo,
    DocumentResult,
    KritaDocumentAdapter,
)


# ---------------------------------------------------------------------------
# Stub HTTP server
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


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    """Clear stub routes and post bodies before each test."""
    _StubHandler.routes.clear()
    _StubHandler.post_routes.clear()
    _StubHandler.last_post_body.clear()


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def adapter(mock_server: str) -> KritaDocumentAdapter:
    return KritaDocumentAdapter(base_url=mock_server, timeout=2.0)


# Sample document response
SAMPLE_DOCUMENT = json.dumps({
    "name": "test_art.kra",
    "width": 1920,
    "height": 1080,
    "layers": 5,
    "color_depth": "8-bit integer",
    "resolution": 300.0,
})


# ---------------------------------------------------------------------------
# active_document tests
# ---------------------------------------------------------------------------


class TestActiveDocument:
    def test_success(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.routes["/api/document"] = (200, SAMPLE_DOCUMENT)
        result = adapter.active_document()
        assert result.ok
        assert result.error == DocumentError.NONE
        assert isinstance(result.data, DocumentInfo)
        info = result.data
        assert info.name == "test_art.kra"
        assert info.width == 1920
        assert info.height == 1080
        assert info.layers == 5
        assert info.color_depth == "8-bit integer"
        assert info.resolution == 300.0

    def test_no_active_document(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.routes["/api/document"] = (200, '{"name": null}')
        result = adapter.active_document()
        assert not result.ok
        assert result.error == DocumentError.NO_DOCUMENT
        assert "No active document" in result.message

    def test_empty_response_body(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.routes["/api/document"] = (200, '{}')
        result = adapter.active_document()
        assert not result.ok
        assert result.error == DocumentError.NO_DOCUMENT

    def test_invalid_response_type(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.routes["/api/document"] = (200, '"not a dict"')
        result = adapter.active_document()
        assert not result.ok
        assert result.error == DocumentError.VALIDATION
        assert "Unexpected response format" in result.message

    def test_missing_required_field(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.routes["/api/document"] = (200, '{"name": "test.kra"}')
        result = adapter.active_document()
        assert not result.ok
        assert result.error == DocumentError.VALIDATION
        assert "Invalid document metadata" in result.message

    def test_connection_failure(self) -> None:
        adapter = KritaDocumentAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.active_document()
        assert not result.ok
        assert result.error == DocumentError.CONNECTION
        assert "unreachable" in result.message.lower()


# ---------------------------------------------------------------------------
# export_canvas tests
# ---------------------------------------------------------------------------


class TestExportCanvas:
    def test_success(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/export"] = (
            200, '{"output_path": "/tmp/export.png", "status": "ok"}'
        )
        result = adapter.export_canvas("/tmp/export.png")
        assert result.ok
        assert "exported" in result.message.lower()

    def test_sends_expected_json_body(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/export"] = (
            200, '{"status": "ok"}'
        )
        adapter.export_canvas("/tmp/art.png")
        posted = _StubHandler.last_post_body.get("/api/document/export", {})
        assert posted["output_path"] == "/tmp/art.png"

    def test_empty_output_path_rejected(self, adapter: KritaDocumentAdapter) -> None:
        result = adapter.export_canvas("")
        assert not result.ok
        assert result.error == DocumentError.VALIDATION
        assert "output_path" in result.message

    def test_connection_failure(self) -> None:
        adapter = KritaDocumentAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.export_canvas("/tmp/out.png")
        assert not result.ok
        assert result.error == DocumentError.CONNECTION

    def test_server_404(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/export"] = (
            404, '{"error":"not found"}'
        )
        result = adapter.export_canvas("/tmp/out.png")
        assert not result.ok
        assert "not found" in result.message.lower()


# ---------------------------------------------------------------------------
# import_image_as_layer tests
# ---------------------------------------------------------------------------


class TestImportImageAsLayer:
    def test_success(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/import-layer"] = (
            200, '{"layer_name": "imported_1", "status": "ok"}'
        )
        result = adapter.import_image_as_layer("/tmp/img.png")
        assert result.ok
        assert "imported" in result.message.lower()

    def test_sends_expected_json_body(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/import-layer"] = (
            200, '{"status": "ok"}'
        )
        adapter.import_image_as_layer("/tmp/img.png", layer_name="My Layer")
        posted = _StubHandler.last_post_body.get("/api/document/import-layer", {})
        assert posted["image_path"] == "/tmp/img.png"
        assert posted["layer_name"] == "My Layer"

    def test_without_layer_name(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/import-layer"] = (
            200, '{"status": "ok"}'
        )
        adapter.import_image_as_layer("/tmp/img.png")
        posted = _StubHandler.last_post_body.get("/api/document/import-layer", {})
        assert "image_path" in posted
        assert "layer_name" not in posted

    def test_empty_image_path_rejected(self, adapter: KritaDocumentAdapter) -> None:
        result = adapter.import_image_as_layer("")
        assert not result.ok
        assert result.error == DocumentError.VALIDATION
        assert "image_path" in result.message

    def test_connection_failure(self) -> None:
        adapter = KritaDocumentAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.import_image_as_layer("/tmp/img.png")
        assert not result.ok
        assert result.error == DocumentError.CONNECTION

    def test_server_404(self, adapter: KritaDocumentAdapter) -> None:
        _StubHandler.post_routes["/api/document/import-layer"] = (
            404, '{"error":"not found"}'
        )
        result = adapter.import_image_as_layer("/tmp/img.png")
        assert not result.ok
        assert "not found" in result.message.lower()


# ---------------------------------------------------------------------------
# Safety: no destructive public methods
# ---------------------------------------------------------------------------


class TestSafetyGuarantees:
    """Issue #4 acceptance: adapter avoids destructive writes by default."""

    DESTRUCTIVE_KEYWORDS = (
        "save",
        "overwrite",
        "merge",
        "delete",
        "remove",
        "flatten",
        "close",
        "quit",
    )

    def test_no_destructive_public_methods(self) -> None:
        """The adapter must not expose any destructive public methods."""
        public_methods = [
            name
            for name in dir(KritaDocumentAdapter)
            if not name.startswith("_") and callable(getattr(KritaDocumentAdapter, name, None))
        ]
        for method_name in public_methods:
            for keyword in self.DESTRUCTIVE_KEYWORDS:
                assert keyword not in method_name.lower(), (
                    f"Destructive method detected: {method_name} (contains '{keyword}')"
                )

    def test_export_is_read_only(self) -> None:
        """export_canvas docstring must mention read-only behavior."""
        doc = KritaDocumentAdapter.export_canvas.__doc__ or ""
        assert "read-only" in doc.lower()

    def test_import_creates_new_layer(self) -> None:
        """import_image_as_layer docstring must mention creating a new layer."""
        doc = KritaDocumentAdapter.import_image_as_layer.__doc__ or ""
        assert "new layer" in doc.lower()


# ---------------------------------------------------------------------------
# Result dataclass checks
# ---------------------------------------------------------------------------


class TestDocumentResult:
    def test_frozen(self) -> None:
        result = DocumentResult(ok=True)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = DocumentResult(ok=True)
        assert result.error == DocumentError.NONE
        assert result.message == ""
        assert result.data is None
