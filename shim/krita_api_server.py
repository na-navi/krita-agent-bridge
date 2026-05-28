"""Localhost Krita API shim.

Run inside Krita's Python environment from Tools > Scripts or Scripter.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

try:
    from .ai_diffusion_endpoints import AIDiffusionBridge, DiffusionResult
    from .document_ops import KritaDocumentOps, OperationResult
    from .job_queue_endpoints import JobQueueBridge
except ImportError:  # pragma: no cover - allows running this file directly in Krita
    from ai_diffusion_endpoints import AIDiffusionBridge, DiffusionResult  # type: ignore
    from document_ops import KritaDocumentOps, OperationResult  # type: ignore
    from job_queue_endpoints import JobQueueBridge  # type: ignore


HOST = "127.0.0.1"
PORT = 8900
DEFAULT_REQUEST_TIMEOUT = 20.0


@dataclass(frozen=True)
class ShimSettings:
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    log_path: str = ""
    comfyui_api: str = ""
    comfyui_confirm: bool = False
    comfyui_confirmation_delay: float = 1.0


@dataclass
class ShimContext:
    documents: KritaDocumentOps
    diffusion: AIDiffusionBridge
    jobs: JobQueueBridge
    executor: Any
    settings: ShimSettings


class DirectExecutor:
    """Fallback executor used outside Krita/PyQt tests."""

    def call(self, func: Callable[[], Any], timeout: float = 10.0) -> Any:  # noqa: ARG002
        return func()


class QtMainThreadExecutor:
    """Run callables on the Qt main thread from HTTP worker threads."""

    def __init__(self) -> None:
        try:
            from PyQt5.QtCore import QObject, QCoreApplication, Qt, pyqtSignal  # type: ignore
        except Exception:
            self._bridge = None
            return
        if QCoreApplication.instance() is None:
            self._bridge = None
            return

        class Bridge(QObject):
            requested = pyqtSignal(object)

            def __init__(self) -> None:
                super().__init__()
                self.requested.connect(self._run, type=Qt.QueuedConnection)

            def _run(
                self,
                payload: tuple[threading.Event, dict[str, Any], Callable[[], Any]],
            ) -> None:
                event, container, func = payload
                try:
                    container["value"] = func()
                except Exception as exc:  # pragma: no cover - defensive Krita boundary
                    container["error"] = exc
                finally:
                    event.set()

        self._bridge = Bridge()

    def call(self, func: Callable[[], Any], timeout: float = 10.0) -> Any:
        if self._bridge is None:
            return func()

        event = threading.Event()
        container: dict[str, Any] = {}
        self._bridge.requested.emit((event, container, func))
        if not event.wait(timeout):
            raise TimeoutError("Krita main-thread operation timed out")
        if "error" in container:
            raise container["error"]
        return container.get("value")


def make_context(
    app: Any = None,
    diffusion_provider: Any = None,
    jobs_provider: Any = None,
    settings: ShimSettings | None = None,
) -> ShimContext:
    executor = QtMainThreadExecutor()
    active_settings = settings or ShimSettings()
    return ShimContext(
        documents=KritaDocumentOps(app=app),
        diffusion=AIDiffusionBridge(
            provider=diffusion_provider,
            executor=executor,
            request_timeout=active_settings.request_timeout,
        ),
        jobs=JobQueueBridge(
            provider=jobs_provider,
            executor=executor,
            request_timeout=active_settings.request_timeout,
            comfyui_api_url=active_settings.comfyui_api if active_settings.comfyui_confirm else "",
            comfyui_confirmation_delay=active_settings.comfyui_confirmation_delay,
        ),
        executor=executor,
        settings=active_settings,
    )


def _json_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON request body must be an object")
    return data


def _krita_version(app: Any) -> str:
    if app is None:
        return ""
    for method_name in ("version", "applicationVersion"):
        try:
            attr = getattr(app, method_name)
            return str(attr() if callable(attr) else attr)
        except Exception:
            continue
    return ""


def _write_log(context: ShimContext, entry: dict[str, Any]) -> None:
    if not context.settings.log_path:
        return
    try:
        path = Path(context.settings.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return


def _settings_from_env(
    request_timeout: float | None = None,
    log_path: str | None = None,
) -> ShimSettings:
    timeout_value = request_timeout
    if timeout_value is None:
        raw_timeout = os.environ.get("KRITA_AGENT_SHIM_TIMEOUT", "")
        try:
            timeout_value = float(raw_timeout) if raw_timeout else DEFAULT_REQUEST_TIMEOUT
        except ValueError:
            timeout_value = DEFAULT_REQUEST_TIMEOUT

    raw_delay = os.environ.get("KRITA_AGENT_COMFYUI_CONFIRMATION_DELAY", "")
    try:
        confirmation_delay = float(raw_delay) if raw_delay else 1.0
    except ValueError:
        confirmation_delay = 1.0
    confirm_raw = os.environ.get("KRITA_AGENT_COMFYUI_CONFIRM", "").strip().lower()

    return ShimSettings(
        request_timeout=timeout_value,
        log_path=log_path if log_path is not None else os.environ.get("KRITA_AGENT_SHIM_LOG", ""),
        comfyui_api=os.environ.get("KRITA_AGENT_COMFYUI_API", "http://127.0.0.1:8188"),
        comfyui_confirm=confirm_raw in {"1", "true", "yes", "on"},
        comfyui_confirmation_delay=confirmation_delay,
    )


def make_handler(context: ShimContext) -> type[BaseHTTPRequestHandler]:
    class KritaApiHandler(BaseHTTPRequestHandler):
        server_version = "KritaAgentShim/0.1"

        def do_GET(self) -> None:  # noqa: N802
            started = time.monotonic()
            path = urlparse(self.path).path.rstrip("/") or "/"
            status = 500
            try:
                if path == "/api/status":
                    status = self._send_json(self._status())
                elif path == "/api/document":
                    result = self._document_call(context.documents.active_document_metadata)
                    status = self._send_operation(result)
                elif path == "/api/document/dirty":
                    result = self._document_call(context.documents.dirty_documents)
                    status = self._send_operation(result)
                elif path == "/api/diffusion/styles":
                    status = self._send_diffusion(context.diffusion.styles())
                elif path == "/api/diffusion/model":
                    status = self._send_diffusion(context.diffusion.model())
                elif path == "/api/diffusion/mode":
                    status = self._send_diffusion(context.diffusion.get_mode())
                elif path == "/api/diffusion/params":
                    status = self._send_diffusion(context.diffusion.params())
                elif path == "/api/jobs":
                    status = self._send_json(context.jobs.jobs())
                elif path == "/api/jobs/history":
                    status = self._send_json(context.jobs.history())
                else:
                    status = self._send_json(
                        {"error": "not_found", "message": "Endpoint not found"},
                        status=404,
                    )
            except Exception as exc:
                status = self._send_json({"error": "server_error", "message": str(exc)}, status=500)
            finally:
                self._log_request("GET", path, status, started)

        def do_POST(self) -> None:  # noqa: N802
            started = time.monotonic()
            path = urlparse(self.path).path.rstrip("/") or "/"
            status = 500
            try:
                body = _json_payload(self)
                if path == "/api/document/export":
                    output_path = str(body.get("output_path", ""))
                    status = self._send_operation(
                        self._document_call(lambda: context.documents.export_canvas(output_path))
                    )
                elif path == "/api/document/import-layer":
                    status = self._send_operation(
                        self._document_call(
                            lambda: context.documents.import_layer(
                                str(body.get("image_path", "")),
                                str(body.get("layer_name", "AI Generated")),
                            )
                        )
                    )
                elif path == "/api/document/create":
                    status = self._send_operation(
                        self._document_call(lambda: context.documents.create_document(**body))
                    )
                elif path == "/api/document/open":
                    status = self._send_operation(
                        self._document_call(
                            lambda: context.documents.open_document(str(body.get("path", "")))
                        )
                    )
                elif path == "/api/document/save":
                    raw_path = body.get("path")
                    path_value = str(raw_path) if raw_path else None
                    status = self._send_operation(
                        self._document_call(lambda: context.documents.save_document(path_value))
                    )
                elif path == "/api/document/close":
                    save_before = bool(body.get("save_before", False))
                    status = self._send_operation(
                        self._document_call(lambda: context.documents.close_document(save_before))
                    )
                elif path == "/api/diffusion/mode":
                    mode = str(body.get("mode", ""))
                    status = self._send_diffusion(context.diffusion.set_mode(mode))
                elif path == "/api/diffusion/generate":
                    result = context.diffusion.generate(body)
                    if result.ok:
                        context.jobs.register_mapping(
                            str(result.data.get("prompt_id", "")),
                            str(result.data.get("job_id", "")),
                        )
                    status = self._send_diffusion(result)
                else:
                    status = self._send_json(
                        {"error": "not_found", "message": "Endpoint not found"},
                        status=404,
                    )
            except json.JSONDecodeError as exc:
                status = self._send_json({"error": "invalid_json", "message": str(exc)}, status=400)
            except ValueError as exc:
                status = self._send_json({"error": "validation", "message": str(exc)}, status=400)
            except Exception as exc:
                status = self._send_json({"error": "server_error", "message": str(exc)}, status=500)
            finally:
                self._log_request("POST", path, status, started)

        def _status(self) -> dict[str, Any]:
            metadata = self._document_call(context.documents.active_document_metadata).data or {}
            app = context.documents.app
            return {
                "running": True,
                "krita_version": _krita_version(app),
                "document_open": metadata.get("name") is not None,
                "log_path": context.settings.log_path,
                "request_timeout": context.settings.request_timeout,
                "comfyui_confirm": context.settings.comfyui_confirm,
                "comfyui_confirmation_delay": context.settings.comfyui_confirmation_delay,
                **context.diffusion.status_fields(),
            }

        def _document_call(self, func: Callable[[], OperationResult]) -> OperationResult:
            try:
                return context.executor.call(func, timeout=context.settings.request_timeout)
            except Exception as exc:
                return OperationResult(
                    ok=False,
                    message=str(exc),
                    error="krita_thread_error",
                )

        def _send_operation(self, result: OperationResult) -> int:
            status = 200 if result.ok else 400
            payload = result.data if isinstance(result.data, dict) else {}
            if result.ok:
                ok_payload = payload or {"status": "ok", "message": result.message}
                return self._send_json(ok_payload, status=status)
            else:
                error_payload = {
                    "error": result.error or "operation_failed",
                    "message": result.message,
                }
                return self._send_json(error_payload, status=status)

        def _send_diffusion(self, result: DiffusionResult) -> int:
            return self._send_json(result.data, status=result.status)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> int:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return status

        def _log_request(self, method: str, path: str, status: int, started: float) -> None:
            _write_log(
                context,
                {
                    "ts": time.time(),
                    "method": method,
                    "path": path,
                    "status": status,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                    "client": self.client_address[0] if self.client_address else "",
                },
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return KritaApiHandler


def create_server(
    host: str = HOST,
    port: int = PORT,
    context: ShimContext | None = None,
    log_path: str | None = None,
    request_timeout: float | None = None,
) -> ThreadingHTTPServer:
    if host != HOST:
        raise ValueError("Krita shim must bind to 127.0.0.1 only")
    settings = _settings_from_env(request_timeout=request_timeout, log_path=log_path)
    return ThreadingHTTPServer(
        (host, port),
        make_handler(context or make_context(settings=settings)),
    )


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the Krita Agent API shim")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--request-timeout", type=float, default=None)
    args = parser.parse_args(argv)

    server = create_server(
        port=args.port,
        log_path=args.log_file,
        request_timeout=args.request_timeout,
    )
    print(f"Krita Agent API shim listening on http://{HOST}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
