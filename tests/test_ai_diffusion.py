"""Tests for the AI Diffusion capability adapter (Issues #5 + #18).

Covers:
- detect(): plugin present, absent, bridge unreachable
- active_model(): model loaded, no model, plugin absent
- version(): version available, version unknown, plugin absent
- styles(): success, empty, invalid response, plugin absent
- get_mode(): mode returned, mode not reported, plugin absent
- set_mode(): valid modes, invalid mode, plugin absent, connection failure
- assert_auto_mode(): auto ok, manual blocked, watch blocked, plugin absent
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
    VALID_MODES,
)


# ---------------------------------------------------------------------------
# Stub HTTP server (supports GET and POST)
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
# get_mode() tests (Issue #18)
# ---------------------------------------------------------------------------


class TestGetMode:
    def test_mode_returned(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        result = adapter.get_mode()
        assert result.ok
        assert result.data["mode"] == "manual"

    def test_mode_auto(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
            "ai_diffusion_mode": "auto",
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.get_mode()
        assert result.ok
        assert result.data["mode"] == "auto"

    def test_mode_not_reported(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.get_mode()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE
        assert "mode not reported" in result.message.lower()

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.get_mode()
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# set_mode() tests (Issue #18)
# ---------------------------------------------------------------------------


class TestSetMode:
    def test_set_auto(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            200, '{"mode": "auto"}'
        )
        result = adapter.set_mode("auto")
        assert result.ok
        assert result.data["mode"] == "auto"

    def test_set_manual(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            200, '{"mode": "manual"}'
        )
        result = adapter.set_mode("manual")
        assert result.ok
        assert result.data["mode"] == "manual"

    def test_set_watch(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            200, '{"mode": "watch"}'
        )
        result = adapter.set_mode("watch")
        assert result.ok

    def test_set_mode_case_insensitive(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            200, '{"mode": "auto"}'
        )
        result = adapter.set_mode("Auto")
        assert result.ok

    def test_sends_expected_body(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            200, '{"mode": "auto"}'
        )
        adapter.set_mode("auto")
        posted = _StubHandler.last_post_body.get("/api/diffusion/mode", {})
        assert posted["mode"] == "auto"

    def test_invalid_mode_rejected(self, adapter: AIDiffusionAdapter) -> None:
        result = adapter.set_mode("turbo")
        assert not result.ok
        assert result.error == CapabilityError.VALIDATION
        assert "turbo" in result.message
        # Should list valid modes
        for mode in VALID_MODES:
            assert mode in result.message

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.set_mode("auto")
        assert not result.ok
        assert result.error == CapabilityError.NOT_AVAILABLE

    def test_connection_failure(self) -> None:
        adapter = AIDiffusionAdapter("http://127.0.0.1:1", timeout=0.5)
        result = adapter.set_mode("auto")
        assert not result.ok
        assert result.error == CapabilityError.CONNECTION

    def test_server_rejects_mode(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)
        _StubHandler.post_routes["/api/diffusion/mode"] = (
            400, '{"error": "mode not supported"}'
        )
        result = adapter.set_mode("auto")
        assert not result.ok
        assert result.error == CapabilityError.CONNECTION


# ---------------------------------------------------------------------------
# assert_auto_mode() tests (Issue #18)
# ---------------------------------------------------------------------------


class TestAssertAutoMode:
    def test_auto_mode_ok(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
            "ai_diffusion_mode": "auto",
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.assert_auto_mode()
        assert result.ok
        assert "auto" in result.message

    def test_manual_mode_blocked(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_ACTIVE)  # mode=manual
        result = adapter.assert_auto_mode()
        assert not result.ok
        assert result.error == CapabilityError.VALIDATION
        assert "manual" in result.message
        assert "auto" in result.message
        assert "set_mode" in result.message

    def test_watch_mode_blocked(self, adapter: AIDiffusionAdapter) -> None:
        status = json.dumps({
            "running": True,
            "ai_diffusion_available": True,
            "ai_diffusion_mode": "watch",
        })
        _StubHandler.routes["/api/status"] = (200, status)
        result = adapter.assert_auto_mode()
        assert not result.ok
        assert "watch" in result.message

    def test_plugin_absent(self, adapter: AIDiffusionAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, STATUS_NO_PLUGIN)
        result = adapter.assert_auto_mode()
        assert not result.ok


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
