"""Tests for the unified snapshot endpoint (Issue #20).

Covers:
- snapshot(): full success, partial degradation, all down
- Bridge status fields: connected, ai_diffusion_available, version, mode
- ComfyUI status: connected, node_count, queue counts
- Model info: name, loaded
- Job overview: counts, recent_jobs
- to_dict(): machine-readable output
- Graceful degradation: partial snapshot when ComfyUI down
- SnapshotResult / Snapshot dataclass checks
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.snapshot import (
    BridgeStatus,
    ComfyUIStatus,
    JobOverview,
    ModelInfo,
    Snapshot,
    SnapshotAdapter,
    SnapshotError,
    SnapshotResult,
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
def dual_adapter(mock_server: str) -> SnapshotAdapter:
    """Adapter with both Krita and ComfyUI pointing to same mock server."""
    return SnapshotAdapter(
        krita_url=mock_server,
        comfyui_url=mock_server,
        timeout=2.0,
    )


# Sample responses
BRIDGE_STATUS = json.dumps({
    "running": True,
    "ai_diffusion_available": True,
    "ai_diffusion_version": "1.5.0",
    "ai_diffusion_mode": "auto",
    "active_model": "sd_xl_base_1.0",
})

COMFYUI_OBJECT_INFO = json.dumps({
    "KSampler": {},
    "CLIPTextEncode": {},
    "VAEDecode": {},
})

COMFYUI_QUEUE = json.dumps({
    "queue_running": [{"prompt_id": "abc"}],
    "queue_pending": [],
})

JOBS_RESPONSE = json.dumps({
    "queued": 1,
    "executing": 1,
    "finished": 3,
    "jobs": [
        {"job_id": "j1", "state": "queued", "progress": 0.0},
        {"job_id": "j2", "state": "executing", "progress": 0.5},
    ],
})


def _setup_full_routes() -> None:
    _StubHandler.routes["/api/status"] = (200, BRIDGE_STATUS)
    _StubHandler.routes["/object_info"] = (200, COMFYUI_OBJECT_INFO)
    _StubHandler.routes["/queue"] = (200, COMFYUI_QUEUE)
    _StubHandler.routes["/api/jobs"] = (200, JOBS_RESPONSE)


# ---------------------------------------------------------------------------
# Full snapshot tests
# ---------------------------------------------------------------------------


class TestFullSnapshot:
    def test_all_services_up(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.NONE
        assert isinstance(result.data, Snapshot)

    def test_bridge_fields(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        snap = result.data
        assert snap.bridge.connected is True
        assert snap.bridge.ai_diffusion_available is True
        assert snap.bridge.version == "1.5.0"
        assert snap.bridge.mode == "auto"

    def test_comfyui_fields(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        snap = result.data
        assert snap.comfyui.connected is True
        assert snap.comfyui.node_count == 3
        assert snap.comfyui.queue_running == 1
        assert snap.comfyui.queue_pending == 0

    def test_model_info(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        snap = result.data
        assert snap.model.name == "sd_xl_base_1.0"
        assert snap.model.loaded is True

    def test_job_overview(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        snap = result.data
        assert snap.jobs.queued == 1
        assert snap.jobs.executing == 1
        assert snap.jobs.finished == 3
        assert len(snap.jobs.recent_jobs) == 2

    def test_no_errors(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        snap = result.data
        assert snap.errors == ()


# ---------------------------------------------------------------------------
# Partial degradation tests
# ---------------------------------------------------------------------------


class TestPartialDegradation:
    def test_comfyui_down(self, dual_adapter: SnapshotAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, BRIDGE_STATUS)
        _StubHandler.routes["/api/jobs"] = (200, JOBS_RESPONSE)
        # ComfyUI endpoints not set → 404
        result = dual_adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.PARTIAL
        snap = result.data
        assert snap.bridge.connected is True
        assert snap.comfyui.connected is False
        assert len(snap.errors) > 0
        assert any("comfyui" in e for e in snap.errors)

    def test_bridge_down(self, mock_server: str) -> None:
        adapter = SnapshotAdapter(
            krita_url="http://127.0.0.1:1",
            comfyui_url=mock_server,
            timeout=0.5,
        )
        _StubHandler.routes["/object_info"] = (200, COMFYUI_OBJECT_INFO)
        _StubHandler.routes["/queue"] = (200, COMFYUI_QUEUE)
        result = adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.PARTIAL
        snap = result.data
        assert snap.bridge.connected is False
        assert snap.comfyui.connected is True
        assert any("bridge" in e for e in snap.errors)

    def test_all_down(self) -> None:
        adapter = SnapshotAdapter(
            krita_url="http://127.0.0.1:1",
            comfyui_url="http://127.0.0.1:2",
            timeout=0.5,
        )
        result = adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.PARTIAL
        snap = result.data
        assert snap.bridge.connected is False
        assert snap.comfyui.connected is False
        assert len(snap.errors) >= 2

    def test_queue_failure_not_fatal(self, dual_adapter: SnapshotAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, BRIDGE_STATUS)
        _StubHandler.routes["/object_info"] = (200, COMFYUI_OBJECT_INFO)
        # /queue not set → 404
        result = dual_adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.NONE  # queue failure is non-fatal
        snap = result.data
        assert snap.comfyui.connected is True
        assert snap.comfyui.queue_running == 0

    def test_jobs_failure_not_fatal(self, dual_adapter: SnapshotAdapter) -> None:
        _StubHandler.routes["/api/status"] = (200, BRIDGE_STATUS)
        _StubHandler.routes["/object_info"] = (200, COMFYUI_OBJECT_INFO)
        _StubHandler.routes["/queue"] = (200, COMFYUI_QUEUE)
        # /api/jobs not set → 404
        result = dual_adapter.snapshot()
        assert result.ok
        assert result.error == SnapshotError.NONE  # jobs failure is non-fatal
        snap = result.data
        assert snap.jobs.queued == 0


# ---------------------------------------------------------------------------
# to_dict tests
# ---------------------------------------------------------------------------


class TestToDict:
    def test_structure(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert "bridge" in d
        assert "comfyui" in d
        assert "model" in d
        assert "jobs" in d
        assert "errors" in d

    def test_bridge_dict(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert d["bridge"]["connected"] is True
        assert d["bridge"]["ai_diffusion_available"] is True
        assert d["bridge"]["version"] == "1.5.0"

    def test_comfyui_dict(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert d["comfyui"]["node_count"] == 3
        assert d["comfyui"]["queue_running"] == 1

    def test_model_dict(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert d["model"]["name"] == "sd_xl_base_1.0"
        assert d["model"]["loaded"] is True

    def test_jobs_dict(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert d["jobs"]["queued"] == 1
        assert d["jobs"]["finished"] == 3
        assert len(d["jobs"]["recent_jobs"]) == 2

    def test_errors_list(self, dual_adapter: SnapshotAdapter) -> None:
        # Partial snapshot
        _StubHandler.routes["/api/status"] = (200, BRIDGE_STATUS)
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        assert isinstance(d["errors"], list)

    def test_json_serializable(self, dual_adapter: SnapshotAdapter) -> None:
        _setup_full_routes()
        result = dual_adapter.snapshot()
        d = result.data.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert len(serialized) > 0


# ---------------------------------------------------------------------------
# Dataclass checks
# ---------------------------------------------------------------------------


class TestSnapshotResult:
    def test_frozen(self) -> None:
        result = SnapshotResult(ok=True)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = SnapshotResult(ok=True)
        assert result.error == SnapshotError.NONE
        assert result.message == ""
        assert result.data is None


class TestBridgeStatus:
    def test_defaults(self) -> None:
        status = BridgeStatus()
        assert status.connected is False
        assert status.ai_diffusion_available is False


class TestComfyUIStatus:
    def test_defaults(self) -> None:
        status = ComfyUIStatus()
        assert status.connected is False
        assert status.node_count == 0


class TestModelInfo:
    def test_defaults(self) -> None:
        info = ModelInfo()
        assert info.name == ""
        assert info.loaded is False


class TestJobOverview:
    def test_defaults(self) -> None:
        overview = JobOverview()
        assert overview.queued == 0
        assert overview.recent_jobs == ()
