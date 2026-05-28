"""Tests for Krita shim endpoints (Issues #29, #31, #32, #33)."""

from __future__ import annotations

import sys
from http.server import HTTPServer
from pathlib import Path
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from krita_agent_bridge.client import JsonEndpointClient
from shim.job_queue_endpoints import JobQueueBridge
from shim.krita_api_server import ShimSettings, create_server, make_context


class FakeNode:
    def __init__(self) -> None:
        self.children: list[object] = []

    def childNodes(self) -> list[object]:  # noqa: N802
        return self.children

    def addChildNode(self, layer: object, _above: object = None) -> None:  # noqa: N802
        self.children.append(layer)


class FakeLayer:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class FakeDocument:
    def __init__(self, name: str = "art.kra", dirty: bool = False) -> None:
        self._name = name
        self._file_name = ""
        self._dirty = dirty
        self.root = FakeNode()
        self.closed = False

    def name(self) -> str:
        return self._name

    def width(self) -> int:
        return 1920

    def height(self) -> int:
        return 1080

    def colorDepth(self) -> str:  # noqa: N802
        return "8-bit integer"

    def resolution(self) -> float:
        return 300.0

    def fileName(self) -> str:  # noqa: N802
        return self._file_name

    def rootNode(self) -> FakeNode:  # noqa: N802
        return self.root

    def modified(self) -> bool:
        return self._dirty

    def exportImage(self, filename: str, _info: object = None) -> bool:  # noqa: N802
        Path(filename).write_bytes(b"png bytes")
        return True

    def createNode(self, name: str, _kind: str) -> FakeLayer:  # noqa: N802
        return FakeLayer(name)

    def save(self) -> bool:
        self._dirty = False
        return True

    def saveAs(self, filename: str) -> bool:  # noqa: N802
        self._file_name = filename
        self._dirty = False
        Path(filename).write_text("kra", encoding="utf-8")
        return True

    def close(self) -> bool:
        self.closed = True
        return True


class FakeImage:
    def isNull(self) -> bool:  # noqa: N802
        return False

    def save(self, filename: str) -> bool:
        Path(filename).write_bytes(b"projection png bytes")
        return True


class ProjectionDocument(FakeDocument):
    def projection(self, x: int, y: int, width: int, height: int) -> FakeImage:  # noqa: ARG002
        return FakeImage()


class FakeWindow:
    def addView(self, _doc: FakeDocument) -> None:  # noqa: N802
        return


class FakeAction:
    def __init__(self) -> None:
        self.triggered = False

    def trigger(self) -> None:
        self.triggered = True


class FakeApp:
    def __init__(self) -> None:
        self.doc = FakeDocument()
        self.quit_action = FakeAction()

    def activeDocument(self) -> FakeDocument:  # noqa: N802
        return self.doc

    def createDocument(  # noqa: N802, ARG002
        self,
        width: int,
        height: int,
        name: str,
        *args: object,
    ) -> FakeDocument:
        self.doc = FakeDocument(name)
        return self.doc

    def openDocument(self, path: str) -> FakeDocument:  # noqa: N802
        self.doc = FakeDocument(Path(path).name)
        self.doc._file_name = path
        return self.doc

    def activeWindow(self) -> FakeWindow:  # noqa: N802
        return FakeWindow()

    def documents(self) -> list[FakeDocument]:
        return [self.doc]

    def version(self) -> str:
        return "5.2.6"

    def action(self, name: str) -> FakeAction | None:
        return self.quit_action if name == "file_quit" else None


class FakeDiffusion:
    version = "1.5.0"
    mode = "manual"
    active_model = "sd_xl_base_1.0"
    styles = ["anime", "photorealistic"]

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def params(self) -> dict[str, object]:
        return {"positive": "test", "seed": 42}

    def generate(self, **_request: object) -> dict[str, str]:
        return {"job_id": "j1", "prompt_id": "p1", "status": "queued"}


class FakeJobs:
    def jobs(self) -> list[dict[str, object]]:
        return [
            {"job_id": "j1", "prompt_id": "p1", "state": "queued", "progress": 0.0},
            {"job_id": "j2", "prompt_id": "p2", "state": "running", "progress": 0.5},
        ]


@pytest.fixture()
def shim_server() -> tuple[str, HTTPServer, FakeApp]:
    app = FakeApp()
    context = make_context(app=app, diffusion_provider=FakeDiffusion(), jobs_provider=FakeJobs())
    context.documents._import_image_into_layer = (  # type: ignore[method-assign]
        lambda doc, source, name: doc.createNode(name, "paintLayer")
    )
    server = create_server(port=0, context=context)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}", server, app
    server.shutdown()


def test_status_and_document_endpoints(shim_server: tuple[str, HTTPServer, FakeApp]) -> None:
    base_url, _server, _app = shim_server
    client = JsonEndpointClient(base_url, timeout=2)
    status = client.get_json("/api/status")
    assert status.ok
    assert status.data["running"] is True
    assert status.data["krita_version"] == "5.2.6"
    assert status.data["document_open"] is True
    assert status.data["ai_diffusion_available"] is True

    document = client.get_json("/api/document")
    assert document.ok
    assert document.data["name"] == "art.kra"
    assert document.data["width"] == 1920


def test_export_and_import_layer_endpoints(
    shim_server: tuple[str, HTTPServer, FakeApp], tmp_path: Path
) -> None:
    base_url, _server, _app = shim_server
    client = JsonEndpointClient(base_url, timeout=2)
    output_path = tmp_path / "export.png"
    export = client.post_json("/api/document/export", {"output_path": str(output_path)})
    assert export.ok
    assert output_path.read_bytes() == b"png bytes"

    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"image")
    imported = client.post_json(
        "/api/document/import-layer",
        {"image_path": str(image_path), "layer_name": "Generated"},
    )
    assert imported.ok
    assert imported.data["layer_name"] == "Generated"


def test_export_prefers_projection_for_image_files(tmp_path: Path) -> None:
    app = FakeApp()
    app.doc = ProjectionDocument()
    context = make_context(app=app, diffusion_provider=FakeDiffusion(), jobs_provider=FakeJobs())
    output_path = tmp_path / "projection.png"

    result = context.documents.export_canvas(str(output_path))

    assert result.ok
    assert output_path.read_bytes() == b"projection png bytes"


def test_document_lifecycle_safety(
    shim_server: tuple[str, HTTPServer, FakeApp],
    tmp_path: Path,
) -> None:
    base_url, _server, app = shim_server
    client = JsonEndpointClient(base_url, timeout=2)
    created = client.post_json(
        "/api/document/create",
        {"name": "new art", "width": 640, "height": 480},
    )
    assert created.ok
    assert created.data["name"] == "new art"

    save_path = tmp_path / "new.kra"
    saved = client.post_json("/api/document/save", {"path": str(save_path)})
    assert saved.ok
    assert save_path.exists()

    app.doc._dirty = True
    denied = client.post_json("/api/document/close", {"save_before": False})
    assert not denied.ok
    assert denied.status == 400
    assert "dirty" in (denied.error or "")

    closed = client.post_json("/api/document/close", {"save_before": True})
    assert closed.ok
    assert app.doc.closed is True


def test_app_quit_endpoint_requires_explicit_policy(
    shim_server: tuple[str, HTTPServer, FakeApp],
) -> None:
    base_url, _server, app = shim_server
    client = JsonEndpointClient(base_url, timeout=2)

    invalid = client.post_json("/api/app/quit", {"policy": "bad"})
    assert not invalid.ok
    assert invalid.status == 400

    scheduled = client.post_json("/api/app/quit", {"policy": "cancel", "delay_ms": 0})
    assert scheduled.ok
    assert scheduled.data["policy"] == "cancel"
    assert scheduled.data["status"] == "scheduled"
    assert app.quit_action.triggered is True


def test_diffusion_and_jobs_endpoints(shim_server: tuple[str, HTTPServer, FakeApp]) -> None:
    base_url, _server, _app = shim_server
    client = JsonEndpointClient(base_url, timeout=2)
    styles = client.get_json("/api/diffusion/styles")
    assert styles.ok
    assert styles.data["styles"] == ["anime", "photorealistic"]

    invalid_mode = client.post_json("/api/diffusion/mode", {"mode": "bad"})
    assert not invalid_mode.ok
    assert invalid_mode.status == 400

    mode = client.post_json("/api/diffusion/mode", {"mode": "auto"})
    assert mode.ok
    assert mode.data["mode"] == "auto"

    generated = client.post_json("/api/diffusion/generate", {"positive": "test"})
    assert generated.ok
    assert generated.data["job_id"] == "j1"

    jobs = client.get_json("/api/jobs")
    assert jobs.ok
    assert jobs.data["queued"] == 1
    assert jobs.data["executing"] == 1


def test_job_queue_can_confirm_finished_job_from_comfyui(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"p1":{"outputs":{"9":{"images":[{"filename":"out.png"}]}}}}'

    def fake_urlopen(url: str, timeout: float = 0.0) -> FakeResponse:  # noqa: ARG001
        assert url in {
            "http://127.0.0.1:8188/history/p1",
            "http://127.0.0.1:8188/history/p2",
        }
        return FakeResponse()

    monkeypatch.setattr("shim.job_queue_endpoints.urlopen", fake_urlopen)
    bridge = JobQueueBridge(
        provider=FakeJobs(),
        comfyui_api_url="http://127.0.0.1:8188",
        comfyui_confirmation_delay=1.0,
    )

    jobs = bridge.jobs()

    confirmed = next(job for job in jobs["jobs"] if job["prompt_id"] == "p1")
    assert confirmed["state"] == "finished"
    assert confirmed["progress"] == 1.0
    assert confirmed["comfyui"]["confirmed"] is True


def test_request_log_file_written(tmp_path: Path) -> None:
    app = FakeApp()
    log_path = tmp_path / "shim.jsonl"
    context = make_context(
        app=app,
        diffusion_provider=FakeDiffusion(),
        jobs_provider=FakeJobs(),
        settings=ShimSettings(log_path=str(log_path), request_timeout=2.0),
    )
    server = create_server(port=0, context=context)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = JsonEndpointClient(f"http://{host}:{port}", timeout=2)
        status = client.get_json("/api/status")
        assert status.ok
    finally:
        server.shutdown()

    contents = log_path.read_text(encoding="utf-8")
    assert '"/api/status"' in contents
    assert '"status": 200' in contents
