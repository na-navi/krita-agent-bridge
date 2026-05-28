"""Krita document adapter MVP for krita-agent-bridge.

Issue #4: Narrow adapter for Krita document operations needed by
agent workflows.

Provides:
- active_document(): fetch active document info via GET /api/document
- export_canvas(): export current canvas to a file via POST /api/document/export
- import_image_as_layer(): import an image as a new layer via POST /api/document/import-layer

Safety guarantees:
- Export is read-only (saves to a specified output path)
- Import always creates a new layer (never overwrites)
- No save, overwrite, merge, delete, or destructive operation is exposed
- All results are machine-readable
- All failures include actionable diagnostics
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .client import JsonEndpointClient


class DocumentError(Enum):
    CONNECTION = "connection"
    NO_DOCUMENT = "no_document"
    VALIDATION = "validation"
    NONE = "none"


@dataclass(frozen=True)
class DocumentResult:
    """Result from a Krita document adapter operation."""

    ok: bool
    error: DocumentError = DocumentError.NONE
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class DocumentInfo:
    """Metadata about the active Krita document."""

    name: str
    width: int
    height: int
    layers: int
    color_depth: str = ""
    resolution: float = 0.0


class KritaDocumentAdapter:
    """Adapter for Krita document operations.

    All operations are non-destructive:
    - Reading document metadata (GET)
    - Exporting canvas to file (POST, creates new file)
    - Importing image as new layer (POST, always adds, never overwrites)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8900",
        timeout: float = 10.0,
    ) -> None:
        self.client = JsonEndpointClient(base_url, timeout=timeout)

    # -----------------------------------------------------------------------
    # GET /api/document — active document info
    # -----------------------------------------------------------------------

    def active_document(self) -> DocumentResult:
        """Fetch metadata about the currently active Krita document.

        Returns DocumentInfo with name, dimensions, layer count, etc.
        """
        result = self.client.get_json("/api/document")

        if not result.ok:
            return DocumentResult(
                ok=False,
                error=DocumentError.CONNECTION,
                message=self._connection_message(result.error),
            )

        data = result.data
        if not isinstance(data, dict):
            return DocumentResult(
                ok=False,
                error=DocumentError.VALIDATION,
                message="Unexpected response format from /api/document: expected JSON object",
            )

        # Empty or null document means no document is open
        if not data or data.get("name") is None:
            return DocumentResult(
                ok=False,
                error=DocumentError.NO_DOCUMENT,
                message="No active document open in Krita",
            )

        try:
            info = DocumentInfo(
                name=str(data["name"]),
                width=int(data["width"]),
                height=int(data["height"]),
                layers=int(data.get("layers", 0)),
                color_depth=str(data.get("color_depth", "")),
                resolution=float(data.get("resolution", 0.0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return DocumentResult(
                ok=False,
                error=DocumentError.VALIDATION,
                message=f"Invalid document metadata from /api/document: {exc}",
            )

        return DocumentResult(ok=True, data=info)

    # -----------------------------------------------------------------------
    # POST /api/document/export — canvas export
    # -----------------------------------------------------------------------

    def export_canvas(self, output_path: str) -> DocumentResult:
        """Export the current canvas to a file.

        Args:
            output_path: File path for the exported image. The server decides
                         the format based on extension.

        The export is read-only — it writes to the specified path without
        modifying the Krita document.
        """
        if not output_path:
            return DocumentResult(
                ok=False,
                error=DocumentError.VALIDATION,
                message="output_path must not be empty",
            )

        result = self.client.post_json(
            "/api/document/export",
            {"output_path": output_path},
        )

        if not result.ok:
            return DocumentResult(
                ok=False,
                error=DocumentError.CONNECTION,
                message=self._export_error_message(result),
            )

        data = result.data if isinstance(result.data, dict) else {}
        return DocumentResult(
            ok=True,
            message=f"Canvas exported to {output_path}",
            data=data,
        )

    # -----------------------------------------------------------------------
    # POST /api/document/import-layer — image import
    # -----------------------------------------------------------------------

    def import_image_as_layer(
        self,
        image_path: str,
        layer_name: str | None = None,
    ) -> DocumentResult:
        """Import an image file as a new layer in the active document.

        Args:
            image_path: Path to the image file to import.
            layer_name: Optional name for the new layer. If omitted, the
                        server assigns a default name.

        Always creates a new layer — never overwrites or merges.
        """
        if not image_path:
            return DocumentResult(
                ok=False,
                error=DocumentError.VALIDATION,
                message="image_path must not be empty",
            )

        body: dict[str, Any] = {"image_path": image_path}
        if layer_name is not None:
            body["layer_name"] = layer_name

        result = self.client.post_json("/api/document/import-layer", body)

        if not result.ok:
            return DocumentResult(
                ok=False,
                error=DocumentError.CONNECTION,
                message=self._import_error_message(result),
            )

        data = result.data if isinstance(result.data, dict) else {}
        return DocumentResult(
            ok=True,
            message=f"Image imported as new layer from {image_path}",
            data=data,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _connection_message(raw_error: str | None) -> str:
        if raw_error:
            return f"Krita bridge unreachable: {raw_error}"
        return "Krita bridge unreachable: unknown error"

    @staticmethod
    def _export_error_message(result: Any) -> str:
        status = getattr(result, "status", None)
        error = getattr(result, "error", None)
        if status == 404:
            return "Export endpoint not found. Ensure the Krita Agent API plugin is running."
        if error:
            return f"Canvas export failed: {error}"
        return "Canvas export failed: unknown error"

    @staticmethod
    def _import_error_message(result: Any) -> str:
        status = getattr(result, "status", None)
        error = getattr(result, "error", None)
        if status == 404:
            return "Import endpoint not found. Ensure the Krita Agent API plugin is running."
        if error:
            return f"Image import failed: {error}"
        return "Image import failed: unknown error"
