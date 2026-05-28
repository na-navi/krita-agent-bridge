"""Unified snapshot endpoint for krita-agent-bridge.

Issue #20: Provide a single endpoint or adapter method that merges
bridge status, ComfyUI model snapshot, and job state into one response.

Equivalent to the old pi_api GET /api/snapshot.

Provides:
- snapshot(): combines bridge status + ComfyUI queue + active model + recent jobs
- Graceful degradation: partial snapshot when some services are down

Design:
- Aggregation layer: calls existing adapters (ComfyUI, doctor checks)
- Machine-readable JSON output only
- Response time < 2 s target when all services are up
- No new runtime dependencies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client import JsonEndpointClient


class SnapshotError(Enum):
    CONNECTION = "connection"
    PARTIAL = "partial"
    NONE = "none"


@dataclass(frozen=True)
class SnapshotResult:
    """Result from a snapshot operation."""

    ok: bool
    error: SnapshotError = SnapshotError.NONE
    message: str = ""
    data: Any = None


@dataclass
class BridgeStatus:
    """Krita bridge connection status."""

    connected: bool = False
    ai_diffusion_available: bool = False
    version: str = ""
    mode: str = ""


@dataclass
class ComfyUIStatus:
    """ComfyUI server status."""

    connected: bool = False
    node_count: int = 0
    queue_running: int = 0
    queue_pending: int = 0


@dataclass
class ModelInfo:
    """Active model information."""

    name: str = ""
    loaded: bool = False


@dataclass
class JobOverview:
    """Recent job state overview."""

    queued: int = 0
    executing: int = 0
    finished: int = 0
    recent_jobs: tuple[dict[str, Any], ...] = ()


@dataclass
class Snapshot:
    """Unified snapshot combining all service states."""

    bridge: BridgeStatus = field(default_factory=BridgeStatus)
    comfyui: ComfyUIStatus = field(default_factory=ComfyUIStatus)
    model: ModelInfo = field(default_factory=ModelInfo)
    jobs: JobOverview = field(default_factory=JobOverview)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert to a machine-readable dict."""
        return {
            "bridge": {
                "connected": self.bridge.connected,
                "ai_diffusion_available": self.bridge.ai_diffusion_available,
                "version": self.bridge.version,
                "mode": self.bridge.mode,
            },
            "comfyui": {
                "connected": self.comfyui.connected,
                "node_count": self.comfyui.node_count,
                "queue_running": self.comfyui.queue_running,
                "queue_pending": self.comfyui.queue_pending,
            },
            "model": {
                "name": self.model.name,
                "loaded": self.model.loaded,
            },
            "jobs": {
                "queued": self.jobs.queued,
                "executing": self.jobs.executing,
                "finished": self.jobs.finished,
                "recent_jobs": list(self.jobs.recent_jobs),
            },
            "errors": list(self.errors),
        }


class SnapshotAdapter:
    """Unified snapshot: bridge + ComfyUI + model + jobs in one call.

    Aggregation layer that calls multiple endpoints and combines
    results. Graceful degradation: if some services are down,
    returns partial snapshot with error flags.
    """

    def __init__(
        self,
        krita_url: str = "http://127.0.0.1:8900",
        comfyui_url: str = "http://127.0.0.1:8188",
        timeout: float = 5.0,
    ) -> None:
        self.krita_client = JsonEndpointClient(krita_url, timeout=timeout)
        self.comfyui_client = JsonEndpointClient(comfyui_url, timeout=timeout)

    def snapshot(self) -> SnapshotResult:
        """Collect a unified snapshot of all service states.

        Returns Snapshot with bridge status, ComfyUI state, active model,
        and recent jobs. Gracefully degrades when services are unavailable.
        """
        errors: list[str] = []
        bridge = BridgeStatus()
        comfyui = ComfyUIStatus()
        model = ModelInfo()
        jobs = JobOverview()

        # --- Bridge status ---
        bridge_result = self.krita_client.get_json("/api/status")
        if bridge_result.ok and isinstance(bridge_result.data, dict):
            data = bridge_result.data
            bridge.connected = True
            bridge.ai_diffusion_available = bool(data.get("ai_diffusion_available", False))
            bridge.version = str(data.get("ai_diffusion_version", ""))
            bridge.mode = str(data.get("ai_diffusion_mode", ""))
            model.name = str(data.get("active_model", ""))
            model.loaded = bool(model.name)
        else:
            error_msg = bridge_result.error or "unknown error"
            errors.append(f"bridge: {error_msg}")

        # --- ComfyUI status ---
        object_info_result = self.comfyui_client.get_json("/object_info")
        if object_info_result.ok and isinstance(object_info_result.data, dict):
            comfyui.connected = True
            comfyui.node_count = len(object_info_result.data)
        else:
            error_msg = object_info_result.error or "unreachable"
            errors.append(f"comfyui: {error_msg}")

        # --- ComfyUI queue ---
        queue_result = self.comfyui_client.get_json("/queue")
        if queue_result.ok and isinstance(queue_result.data, dict):
            comfyui.queue_running = len(queue_result.data.get("queue_running", []))
            comfyui.queue_pending = len(queue_result.data.get("queue_pending", []))
        # Queue failure is not fatal for snapshot

        # --- Jobs (via bridge) ---
        jobs_result = self.krita_client.get_json("/api/jobs")
        if jobs_result.ok and isinstance(jobs_result.data, dict):
            jobs_data = jobs_result.data
            jobs.queued = int(jobs_data.get("queued", 0))
            jobs.executing = int(jobs_data.get("executing", 0))
            jobs.finished = int(jobs_data.get("finished", 0))
            raw_jobs = jobs_data.get("jobs", [])
            if isinstance(raw_jobs, list):
                jobs.recent_jobs = tuple(
                    {k: v for k, v in j.items() if isinstance(v, (str, int, float, bool))}
                    for j in raw_jobs
                    if isinstance(j, dict)
                )
        # Jobs failure is not fatal for snapshot

        snap = Snapshot(
            bridge=bridge,
            comfyui=comfyui,
            model=model,
            jobs=jobs,
            errors=tuple(errors),
        )

        if errors:
            return SnapshotResult(
                ok=True,
                error=SnapshotError.PARTIAL,
                message=f"Partial snapshot: {len(errors)} service(s) unavailable",
                data=snap,
            )

        return SnapshotResult(
            ok=True,
            message="Full snapshot collected",
            data=snap,
        )
