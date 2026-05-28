"""Safety-bound Krita document operations for the local shim."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safe_files import check_collision, resolve_unique_path, safe_rename, trash_file


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    data: Any = None
    error: str = ""


@dataclass
class OperationLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, action: str, result: OperationResult, path: str = "") -> None:
        self.entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "path": path,
            "ok": result.ok,
            "message": result.message,
            "error": result.error,
        })


class KritaDocumentOps:
    """Document lifecycle operations with collision and dirty-state guards."""

    def __init__(self, app: Any = None) -> None:
        self.app = app if app is not None else self._load_krita_app()
        self.log = OperationLog()
        self._open_paths: dict[str, Any] = {}

    @staticmethod
    def _load_krita_app() -> Any:
        try:
            from krita import Krita  # type: ignore

            return Krita.instance()
        except Exception:
            return None

    def _record(self, action: str, result: OperationResult, path: str = "") -> OperationResult:
        self.log.add(action, result, path=path)
        return result

    def _active_document(self) -> Any:
        if self.app is None:
            return None
        try:
            return self.app.activeDocument()
        except Exception:
            return None

    def _add_view(self, document: Any) -> None:
        try:
            window = self.app.activeWindow()
            if window is not None:
                window.addView(document)
        except Exception:
            return

    def active_document_metadata(self) -> OperationResult:
        doc = self._active_document()
        if doc is None:
            return OperationResult(True, "No active document", data={"name": None})
        return OperationResult(True, "Active document metadata", data=self._metadata(doc))

    def create_document(
        self,
        width: int = 1920,
        height: int = 1080,
        resolution: float = 300.0,
        color_depth: str = "U8",
        name: str = "",
    ) -> OperationResult:
        if self.app is None:
            result = OperationResult(False, "Krita API unavailable", error="krita_unavailable")
            return self._record("create_document", result)
        if not name or not isinstance(name, str):
            result = OperationResult(False, "Document name is required", error="validation")
            return self._record("create_document", result)
        if width <= 0 or height <= 0:
            result = OperationResult(False, "Width and height must be positive", error="validation")
            return self._record("create_document", result)

        try:
            doc = self.app.createDocument(width, height, name, "RGBA", color_depth, "", resolution)
            self._add_view(doc)
        except Exception as exc:
            result = OperationResult(False, f"Create failed: {exc}", error="krita_error")
            return self._record("create_document", result)

        result = OperationResult(True, "Document created", data=self._metadata(doc))
        return self._record("create_document", result)

    def open_document(self, path: str) -> OperationResult:
        target = Path(path)
        if self.app is None:
            result = OperationResult(False, "Krita API unavailable", error="krita_unavailable")
            return self._record("open_document", result, path)
        if not target.exists() or not target.is_file():
            result = OperationResult(False, "File does not exist", error="not_found")
            return self._record("open_document", result, path)
        supported = {
            ".kra", ".krz", ".ora", ".psd", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
        }
        if target.suffix.lower() not in supported:
            result = OperationResult(
                False,
                "Unsupported document format",
                error="unsupported_format",
            )
            return self._record("open_document", result, path)

        key = str(target.resolve())
        if key in self._open_paths:
            result = OperationResult(
                True,
                "Document already open",
                data=self._metadata(self._open_paths[key]),
            )
            return self._record("open_document", result, path)

        try:
            doc = self.app.openDocument(str(target))
            self._add_view(doc)
            self._open_paths[key] = doc
        except Exception as exc:
            result = OperationResult(False, f"Open failed: {exc}", error="krita_error")
            return self._record("open_document", result, path)

        result = OperationResult(True, "Document opened", data=self._metadata(doc))
        return self._record("open_document", result, path)

    def save_document(self, path: str | None = None) -> OperationResult:
        doc = self._active_document()
        if doc is None:
            result = OperationResult(False, "No active document", error="no_document")
            return self._record("save_document", result)

        target = Path(path) if path else Path(str(self._call(doc, "fileName", "")))
        if not str(target):
            result = OperationResult(False, "Document has no filename", error="no_filename")
            return self._record("save_document", result)
        if path and target.exists():
            result = OperationResult(False, "Target already exists", error="collision")
            return self._record("save_document", result, str(target))

        try:
            ok = bool(doc.saveAs(str(target)) if path else doc.save())
        except Exception as exc:
            result = OperationResult(False, f"Save failed: {exc}", error="krita_error")
            return self._record("save_document", result, str(target))
        if not ok:
            result = OperationResult(False, "Krita reported save failure", error="krita_error")
            return self._record("save_document", result, str(target))
        result = OperationResult(True, "Document saved", data=self._metadata(doc))
        return self._record("save_document", result, str(target))

    def close_document(self, save_before: bool = False) -> OperationResult:
        doc = self._active_document()
        if doc is None:
            result = OperationResult(False, "No active document", error="no_document")
            return self._record("close_document", result)
        if self._is_dirty(doc):
            if not save_before:
                result = OperationResult(False, "Document has unsaved changes", error="dirty")
                return self._record("close_document", result)
            save_result = self.save_document()
            if not save_result.ok:
                return self._record("close_document", save_result)
        try:
            ok = bool(doc.close())
        except Exception as exc:
            result = OperationResult(False, f"Close failed: {exc}", error="krita_error")
            return self._record("close_document", result)
        message = "Document closed" if ok else "Krita reported close failure"
        result = OperationResult(ok, message, error="" if ok else "krita_error")
        return self._record("close_document", result)

    def dirty_documents(self) -> OperationResult:
        docs = []
        for doc in self._documents():
            if self._is_dirty(doc):
                docs.append(self._metadata(doc))
        return OperationResult(True, "Dirty documents listed", data={"documents": docs})

    def propose_name(self, base: str, directory: str) -> OperationResult:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = resolve_unique_path(directory, f"{base}_{timestamp}", ".kra")
        return OperationResult(True, "Name proposed", data={"path": str(path)})

    def export_canvas(self, output_path: str) -> OperationResult:
        doc = self._active_document()
        target = Path(output_path)
        if doc is None:
            result = OperationResult(False, "No active document", error="no_document")
            return self._record("export_canvas", result, output_path)
        collision = check_collision(target)
        if not collision.ok:
            result = OperationResult(False, collision.message, error="collision")
            return self._record("export_canvas", result, output_path)
        if not target.parent.exists():
            result = OperationResult(False, "Output directory does not exist", error="not_found")
            return self._record("export_canvas", result, output_path)

        temp_path = resolve_unique_path(target.parent, f"{target.stem}.saving", target.suffix)
        try:
            exported = self._export_document(doc, temp_path)
            if exported is False:
                trash_file(temp_path)
                result = OperationResult(
                    False,
                    "Krita reported export failure",
                    error="krita_error",
                )
                return self._record("export_canvas", result, output_path)
            renamed = safe_rename(temp_path, target)
            if not renamed.ok:
                trash_file(temp_path)
                result = OperationResult(False, renamed.message, error="file_error")
                return self._record("export_canvas", result, output_path)
        except Exception as exc:
            if temp_path.exists():
                trash_file(temp_path)
            result = OperationResult(False, f"Export failed: {exc}", error="krita_error")
            return self._record("export_canvas", result, output_path)
        result = OperationResult(
            True,
            "Canvas exported",
            data={"output_path": str(target), "status": "ok"},
        )
        return self._record("export_canvas", result, output_path)

    def _export_document(self, doc: Any, path: Path) -> bool:
        image_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        if path.suffix.lower() in image_suffixes and hasattr(doc, "projection"):
            image = doc.projection(0, 0, int(doc.width()), int(doc.height()))
            if image is None:
                return False
            if hasattr(image, "isNull") and image.isNull():
                return False
            return bool(image.save(str(path)))

        info = self._info_object()
        return bool(doc.exportImage(str(path), info))

    def import_layer(self, image_path: str, layer_name: str = "AI Generated") -> OperationResult:
        doc = self._active_document()
        source = Path(image_path)
        if doc is None:
            result = OperationResult(False, "No active document", error="no_document")
            return self._record("import_layer", result, image_path)
        if not source.exists() or not source.is_file():
            result = OperationResult(False, "Image file does not exist", error="not_found")
            return self._record("import_layer", result, image_path)
        try:
            layer = self._import_image_into_layer(doc, source, layer_name)
        except Exception as exc:
            result = OperationResult(False, f"Import failed: {exc}", error="krita_error")
            return self._record("import_layer", result, image_path)
        result = OperationResult(
            True,
            "Layer imported",
            data={"layer_name": self._call(layer, "name", layer_name), "status": "ok"},
        )
        return self._record("import_layer", result, image_path)

    def _info_object(self) -> Any:
        try:
            from krita import InfoObject  # type: ignore

            return InfoObject()
        except Exception:
            return None

    def _import_image_into_layer(self, doc: Any, source: Path, layer_name: str) -> Any:
        try:
            from PyQt5.QtGui import QImage  # type: ignore
        except Exception:
            from PyQt6.QtGui import QImage  # type: ignore

        image = QImage(str(source))
        if image.isNull():
            raise ValueError("Image could not be loaded")
        if hasattr(QImage, "Format_RGBA8888"):
            image = image.convertToFormat(QImage.Format_RGBA8888)
        layer = doc.createNode(layer_name, "paintLayer")
        bits = image.bits()
        if hasattr(bits, "setsize"):
            bits.setsize(image.sizeInBytes())
        raw = bytes(bits)
        layer.setPixelData(raw, 0, 0, image.width(), image.height())
        doc.rootNode().addChildNode(layer, None)
        self._call(doc, "refreshProjection")
        return layer

    def _documents(self) -> list[Any]:
        if self.app is None:
            return []
        try:
            return list(self.app.documents())
        except Exception:
            doc = self._active_document()
            return [doc] if doc is not None else []

    def _metadata(self, doc: Any) -> dict[str, Any]:
        return {
            "name": self._call(doc, "name", None) or self._call(doc, "fileName", None),
            "width": int(self._call(doc, "width", 0) or 0),
            "height": int(self._call(doc, "height", 0) or 0),
            "layers": self._layer_count(doc),
            "color_depth": str(self._call(doc, "colorDepth", "") or ""),
            "resolution": float(self._call(doc, "resolution", 0.0) or 0.0),
            "dirty": self._is_dirty(doc),
            "file_name": str(self._call(doc, "fileName", "") or ""),
        }

    def _layer_count(self, doc: Any) -> int:
        try:
            root = doc.rootNode()
            return self._count_children(root)
        except Exception:
            return 0

    def _count_children(self, node: Any) -> int:
        children = list(node.childNodes())
        return len(children) + sum(self._count_children(child) for child in children)

    def _is_dirty(self, doc: Any) -> bool:
        for name in ("modified", "isModified"):
            try:
                attr = getattr(doc, name)
                return bool(attr() if callable(attr) else attr)
            except Exception:
                continue
        return False

    @staticmethod
    def _call(obj: Any, method_name: str, default: Any = None) -> Any:
        try:
            attr = getattr(obj, method_name)
            return attr() if callable(attr) else attr
        except Exception:
            return default
