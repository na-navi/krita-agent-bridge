"""Fast local bootstrap for real-machine Krita smoke testing."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .client import JsonEndpointClient
from .readiness import ReadinessProbe


@dataclass(frozen=True)
class BootstrapResult:
    ok: bool
    started_krita: bool
    document_created: bool
    ready: bool
    message: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bootstrap_test_mode(
    *,
    krita_exe: str | Path,
    krita_api: str = "http://127.0.0.1:8900",
    comfyui_api: str = "http://127.0.0.1:8188",
    document_name: str = "smoke-bootstrap",
    width: int = 1024,
    height: int = 1024,
    timeout: float = 60.0,
    interval: float = 1.0,
    request_timeout: float = 3.0,
    create_document: bool = True,
) -> BootstrapResult:
    """Start Krita if needed, create a blank document, then wait for generation readiness."""

    exe = Path(krita_exe)
    if not exe.exists():
        return BootstrapResult(
            False,
            False,
            False,
            False,
            f"Krita executable not found: {exe}",
            {},
        )

    started = False
    client = JsonEndpointClient(krita_api, timeout=request_timeout)
    status = client.get_json("/api/status")
    if not status.ok:
        subprocess.Popen([str(exe)], close_fds=True)  # noqa: S603
        started = True
        status = _wait_for_bridge(client, timeout=timeout, interval=interval)

    if not status.ok:
        return BootstrapResult(
            False,
            started,
            False,
            False,
            "Krita bridge did not become reachable",
            {"status_error": status.error},
        )

    created = False
    status_data = status.data if isinstance(status.data, dict) else {}
    if create_document and not status_data.get("document_open"):
        created_result = client.post_json(
            "/api/document/create",
            {"name": document_name, "width": width, "height": height},
        )
        if not created_result.ok:
            return BootstrapResult(
                False,
                started,
                False,
                False,
                "blank document creation failed",
                {"create_error": created_result.error},
            )
        created = True

    readiness = ReadinessProbe(
        krita_api=krita_api,
        comfyui_api=comfyui_api,
        timeout=request_timeout,
    ).wait(timeout=timeout, interval=interval, require_document=create_document)

    return BootstrapResult(
        readiness.ready,
        started,
        created,
        readiness.ready,
        "test mode ready" if readiness.ready else "test mode did not become ready",
        {"readiness": readiness.to_dict()},
    )


def _wait_for_bridge(
    client: JsonEndpointClient,
    *,
    timeout: float,
    interval: float,
) -> Any:
    deadline = time.monotonic() + timeout
    result = client.get_json("/api/status")
    while not result.ok and time.monotonic() < deadline:
        time.sleep(interval)
        result = client.get_json("/api/status")
    return result


def result_json(result: BootstrapResult) -> str:
    return json.dumps({"bootstrap": result.to_dict()}, ensure_ascii=False, indent=2)
