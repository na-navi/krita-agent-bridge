"""Readiness gate for fully automated Krita generation runs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .client import JsonEndpointClient


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: tuple[ReadinessCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checks": [
                {
                    "name": item.name,
                    "ok": item.ok,
                    "detail": item.detail,
                    "data": item.data,
                }
                for item in self.checks
            ],
        }


class ReadinessProbe:
    """Poll public bridge/ComfyUI APIs until generation can be attempted."""

    def __init__(
        self,
        krita_api: str = "http://127.0.0.1:8900",
        comfyui_api: str = "http://127.0.0.1:8188",
        timeout: float = 3.0,
    ) -> None:
        self.krita = JsonEndpointClient(krita_api, timeout=timeout)
        self.comfyui = JsonEndpointClient(comfyui_api, timeout=timeout)

    def check(self, require_document: bool = True) -> ReadinessReport:
        checks = [
            self._krita_bridge(require_document=require_document),
            self._ai_diffusion_styles(),
            self._comfyui_object_info(),
            self._comfyui_queue(),
        ]
        return ReadinessReport(
            ready=all(item.ok for item in checks),
            checks=tuple(checks),
        )

    def wait(
        self,
        timeout: float = 120.0,
        interval: float = 1.0,
        require_document: bool = True,
    ) -> ReadinessReport:
        deadline = time.monotonic() + timeout
        last = self.check(require_document=require_document)
        while not last.ready and time.monotonic() < deadline:
            time.sleep(interval)
            last = self.check(require_document=require_document)
        return last

    def _krita_bridge(self, require_document: bool) -> ReadinessCheck:
        result = self.krita.get_json("/api/status")
        if not result.ok:
            return ReadinessCheck("krita_bridge", False, f"bridge unreachable: {result.error}")
        data = result.data if isinstance(result.data, dict) else {}
        if not data.get("running"):
            return ReadinessCheck("krita_bridge", False, "bridge is not running", data)
        if require_document and not data.get("document_open"):
            return ReadinessCheck("krita_bridge", False, "no active Krita document", data)
        if not data.get("ai_diffusion_available"):
            return ReadinessCheck("krita_bridge", False, "AI Diffusion plugin not detected", data)
        return ReadinessCheck("krita_bridge", True, "Krita bridge is ready", data)

    def _ai_diffusion_styles(self) -> ReadinessCheck:
        result = self.krita.get_json("/api/diffusion/styles")
        if not result.ok:
            return ReadinessCheck("ai_diffusion_styles", False, f"styles unavailable: {result.error}")
        data = result.data if isinstance(result.data, dict) else {}
        styles = data.get("styles", [])
        if not isinstance(styles, list) or not styles:
            return ReadinessCheck("ai_diffusion_styles", False, "no AI Diffusion styles available", data)
        return ReadinessCheck(
            "ai_diffusion_styles",
            True,
            f"{len(styles)} styles available",
            {"count": len(styles)},
        )

    def _comfyui_object_info(self) -> ReadinessCheck:
        result = self.comfyui.get_json("/object_info/CheckpointLoaderSimple")
        if not result.ok:
            return ReadinessCheck("comfyui_object_info", False, f"object_info unavailable: {result.error}")
        data = result.data if isinstance(result.data, dict) else {}
        node = data.get("CheckpointLoaderSimple", {})
        choices = node.get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
        if not choices:
            return ReadinessCheck("comfyui_object_info", False, "no checkpoint choices reported", data)
        return ReadinessCheck(
            "comfyui_object_info",
            True,
            f"{len(choices)} checkpoint choices reported",
            {"checkpoint_count": len(choices)},
        )

    def _comfyui_queue(self) -> ReadinessCheck:
        result = self.comfyui.get_json("/queue")
        if not result.ok:
            return ReadinessCheck("comfyui_queue", False, f"queue unavailable: {result.error}")
        data = result.data if isinstance(result.data, dict) else {}
        running = data.get("queue_running", [])
        pending = data.get("queue_pending", [])
        if not isinstance(running, list) or not isinstance(pending, list):
            return ReadinessCheck("comfyui_queue", False, "unexpected queue response", data)
        return ReadinessCheck(
            "comfyui_queue",
            True,
            "ComfyUI queue is readable",
            {"running": len(running), "pending": len(pending)},
        )
