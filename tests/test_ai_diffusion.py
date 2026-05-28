"""Tests for the AI Diffusion capability adapter (Issue #5).

Covers:
- detect(): plugin present, absent, bridge unreachable
- active_model(): model loaded, no model, plugin absent
- version(): version available, version unknown, plugin absent
- styles(): success, empty, invalid response, plugin absent
- Graceful degradation: no exception on absent plugin
- CapabilityResult / DiffusionInfo dataclass checks
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.ai_diffusion import (
    AIDiffusionAdapter,
    CapabilityError,
    CapabilityResult,
    DiffusionInfo,
)


# ---------------------------------------------------------------------------
# Stub HTTP server
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}

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

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    _StubHandler.routes.clear()


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def adapter(mock_server: str) -> AIDiffusionAdapter:
    return AIDiffusionAdapter(base_url=mock_server, timeout=2.0)


# Status with AI Diffusion active
STATUS_ACTIVE = json.dumps({
    "running": True,
    "ai_diffusion_available": True,
    "ai_diffusion_version": "1.5.0",
    "active_model": "sd_xl_base_1.0",
    "ai_diffusion_mode": "manual",
})

# Status without AI Diffusion
STATUS_NO_PLUGIN = json.dumps({
    "running": True,
})


# ---------------------------------------------------------------------------
# detect() tests
# ---------------------------------------------------------------------------


class TestDetect:
    def test_plugin_present(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        result = adapter.detect()
        assert result.ok
        assert result.error == CapabilityError.NONE
        assert isinstance(result.data, DiffusionInfo)
        assert result.data.available is True
        assert result.data.version == "1.5.0"
        assert result.data.active_model == "sd_xl_base_1.0"
        assert result.data.mode == "manual"

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.detect()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE
        assert isinstance(result.data, DiffusionInfo)
        assert result.data.available is False

    def test_connection_failure(self) -> None:
        adapter = AIDiffusionAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.detect()
        assert not result.ok
        assert result.error == CapabilityError.CONNECTION
        assert "unreachable" in result.message.lower()

    def test_invalid_response(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, '"not a dict"')
        result = adapter.detect()
        assert not result.ok
        assert result.error == CapabilityError.VALIDATION

    def test_no_exception_on_absence(self, adapter: AIDiffusionAdapter) -> None:
        """Graceful degradation: must never raise for absent plugin."""
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.detect()
        assert result is not None
        assert isinstance(result, CapabilityResult)


# ---------------------------------------------------------------------------
# active_model() tests
# ---------------------------------------------------------------------------


class TestActiveModel:
    def test_model_loaded(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        result = adapter.active_model()
        assert result.ok
        assert result.data["model"] == "sd_xl_base_1.0"

    def test_no_model_loaded(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
            "active_model": "",
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.active_model()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE
        assert "No active model" in result.message

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.active_model()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# version() tests
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_available(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        result = adapter.version()
        assert result.ok
        assert result.data["version"] == "1.5.0"

    def test_version_unknown(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.version()
        assert result.ok  # still ok, version is just empty
        assert result.data["version"] == ""

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.version()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# styles() tests
# ---------------------------------------------------------------------------


class TestStyles:
    def test_styles_available(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.routes["/api/diffusion/styles"] = (
            200, '{"styles": ["anime", "photorealistic", "painterly"]}'
        )
        result = adapter.styles()
        assert result.ok
        assert isinstance(result.data, tuple)
        assert "anime" in result.data
        assert len(result.data) == 3

    def test_empty_styles(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.routes["/api/diffusion/styles"] = (
            200, '{"styles": []}'
        )
        result = adapter.styles()
        assert result.ok
        assert result.data == ()

    def test_styles_endpoint_unreachable(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        # /api/diffusion/styles not in routes → 404
        result = adapter.styles()
        assert not result.ok
        assert result.error == CapabilityError.CONNECTION

    def test_invalid_styles_response(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.routes["/api/diffusion/styles"] = (
            200, '"not a dict"'
        )
        result = adapter.styles()
        assert not result.ok
        assert result.error == CapabilityError.VALIDATION

    def test_styles_not_a_list(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.routes["/api/diffusion/styles"] = (
            200, '{"styles": "oops"}'
        )
        result = adapter.styles()
        assert not result.ok
        assert result.error == CapabilityError.VALIDATION

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.styles()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Dataclass checks
# ---------------------------------------------------------------------------


class TestCapabilityResult:
    def test_frozen(self) -> None:
        result = CapabilityResult(ok=True)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = CapabilityResult(ok=True)
        assert result.error == CapabilityError.NONE
        assert result.message == ""
        assert result.data is None


class TestDiffusionInfo:
    def test_frozen(self) -> None:
        info = DiffusionInfo(available=True)
        with pytest.raises(AttributeError):
            info.available = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        info = DiffusionInfo(available=True)
        assert info.version == ""
        assert info.active_model == ""
        assert info.mode == ""
        assert info.styles == ()
