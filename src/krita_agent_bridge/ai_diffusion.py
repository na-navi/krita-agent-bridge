"""Krita AI Diffusion capability adapter for krita-agent-bridge.

Issue #5: Detect and optionally use Krita AI Diffusion without
making it a hard dependency.

Provides:
- plugin presence detection
- version / capability reporting
- active model availability check
- style list query

Design:
- All methods return CapabilityResult with graceful degradation
- No exception is raised for absent plugin — ok=False + clear message
- Follows ComfyUIAdapter / KritaDocumentAdapter conventions
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .client import JsonEndpointClient


class CapabilityError(Enum):
    CONNECTION = "connection"
    NOT_AVAILABLE = "not_available"
    VALIDATION = "validation"
    NONE = "none"


@dataclass(frozen=True)
class CapabilityResult:
    """Result from an AI Diffusion capability query."""

    ok: bool
    error: CapabilityError = CapabilityError.NONE
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class DiffusionInfo:
    """Metadata about the AI Diffusion plugin state."""

    available: bool
    version: str = ""
    active_model: str = ""
    mode: str = ""
    styles: tuple[str, ...] = ()


class AIDiffusionAdapter:
    """Adapter for Krita AI Diffusion plugin capabilities.

    Graceful degradation: every method returns CapabilityResult.
    When AI Diffusion is not installed or not responding, ok=False
    with an actionable message — never raises.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8900",
        timeout: float = 10.0,
    ) -> None:
        self.client = JsonEndpointClient(base_url, timeout=timeout)

    # -----------------------------------------------------------------------
    # Plugin presence & info
    # -----------------------------------------------------------------------

    def detect(self) -> CapabilityResult:
        """Detect whether the AI Diffusion plugin is present and active.

        Queries /api/status and checks the ai_diffusion_available flag.
        Returns DiffusionInfo with availability, version, and mode.
        """
        result = self.client.get_json("/api/status")

        if not result.ok:
            return CapabilityResult(
                ok=False,
                error=CapabilityError.CONNECTION,
                message=self._connection_message(result.error),
            )

        data = result.data
        if not isinstance(data, dict):
            return CapabilityResult(
                ok=False,
                error=CapabilityError.VALIDATION,
                message="Unexpected response format from /api/status",
            )

        available = bool(data.get("ai_diffusion_available", False))
        if not available:
            return CapabilityResult(
                ok=False,
                error=CapabilityError.NOT_AVAILABLE,
                message="AI Diffusion plugin is not active",
                data=DiffusionInfo(available=False),
            )

        info = DiffusionInfo(
            available=True,
            version=str(data.get("ai_diffusion_version", "")),
            active_model=str(data.get("active_model", "")),
            mode=str(data.get("ai_diffusion_mode", "")),
        )
        return CapabilityResult(
            ok=True,
            message="AI Diffusion plugin is active",
            data=info,
        )

    # -----------------------------------------------------------------------
    # Active model
    # -----------------------------------------------------------------------

    def active_model(self) -> CapabilityResult:
        """Check which model is currently active in AI Diffusion.

        Returns the model name on success.
        """
        detect_result = self.detect()
        if not detect_result.ok:
            return detect_result

        info = detect_result.data
        if not isinstance(info, DiffusionInfo):
            return CapabilityResult(
                ok=False,
                error=CapabilityError.VALIDATION,
                message="Unexpected data type from detect()",
            )

        model_name = info.active_model
        if not model_name:
            return CapabilityResult(
                ok=False,
                error=CapabilityError.NOT_AVAILABLE,
                message="No active model loaded in AI Diffusion",
            )

        return CapabilityResult(
            ok=True,
            message=f"Active model: {model_name}",
            data={"model": model_name},
        )

    # -----------------------------------------------------------------------
    # Version
    # -----------------------------------------------------------------------

    def version(self) -> CapabilityResult:
        """Report the AI Diffusion plugin version.

        Returns version string on success.
        """
        detect_result = self.detect()
        if not detect_result.ok:
            return detect_result

        info = detect_result.data
        if not isinstance(info, DiffusionInfo):
            return CapabilityResult(
                ok=False,
                error=CapabilityError.VALIDATION,
                message="Unexpected data type from detect()",
            )

        ver = info.version
        if not ver:
            return CapabilityResult(
                ok=True,
                message="AI Diffusion is active but version is unknown",
                data={"version": ""},
            )

        return CapabilityResult(
            ok=True,
            message=f"AI Diffusion version: {ver}",
            data={"version": ver},
        )

    # -----------------------------------------------------------------------
    # Style list
    # -----------------------------------------------------------------------

    def styles(self) -> CapabilityResult:
        """Query available styles from the AI Diffusion plugin.

        Returns a tuple of style name strings.
        """
        detect_result = self.detect()
        if not detect_result.ok:
            return detect_result

        result = self.client.get_json("/api/diffusion/styles")

        if not result.ok:
            return CapabilityResult(
                ok=False,
                error=CapabilityError.CONNECTION,
                message=f"Failed to fetch styles: {result.error}",
            )

        data = result.data
        if not isinstance(data, dict):
            return CapabilityResult(
                ok=False,
                error=CapabilityError.VALIDATION,
                message="Unexpected response format from /api/diffusion/styles",
            )

        style_list = data.get("styles", [])
        if not isinstance(style_list, list):
            return CapabilityResult(
                ok=False,
                error=CapabilityError.VALIDATION,
                message="Styles field is not a list",
            )

        style_names = tuple(str(s) for s in style_list)
        return CapabilityResult(
            ok=True,
            message=f"{len(style_names)} styles available",
            data=style_names,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _connection_message(raw_error: str | None) -> str:
        if raw_error:
            return f"Krita bridge unreachable: {raw_error}"
        return "Krita bridge unreachable: unknown error"
