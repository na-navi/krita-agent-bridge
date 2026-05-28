"""Build the Krita plugin ZIP for the Agent Bridge shim."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "krita_agent_bridge_shim.zip"
PLUGIN_ID = "krita_agent_bridge_shim"
SHIM_FILES = (
    "__init__.py",
    "ai_diffusion_endpoints.py",
    "document_ops.py",
    "job_queue_endpoints.py",
    "krita_api_server.py",
    "safe_files.py",
)

DESKTOP_ENTRY = """[Desktop Entry]
Type=Service
ServiceTypes=Krita/PythonPlugin
X-KDE-Library=krita_agent_bridge_shim
X-Python-2-Compatible=false
Name=Krita Agent Bridge Shim
Comment=Localhost API bridge for agent-driven Krita workflows.
"""

PLUGIN_ENTRYPOINT = '''"""Krita Python plugin entrypoint for krita-agent-bridge shim."""

from __future__ import annotations

import threading
from typing import Any

from krita import Extension, Krita  # type: ignore

from .krita_api_server import HOST, PORT, create_server

_server: Any | None = None
_thread: threading.Thread | None = None


def start_server() -> None:
    """Start the localhost bridge once per Krita process."""
    global _server, _thread
    if _server is not None:
        print(f"Krita Agent API shim already running on http://{HOST}:{PORT}")
        return
    _server = create_server()
    _thread = threading.Thread(
        target=_server.serve_forever,
        name="KritaAgentBridgeShim",
        daemon=True,
    )
    _thread.start()
    print(f"Krita Agent API shim listening on http://{HOST}:{PORT}")


class KritaAgentBridgeShim(Extension):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)

    def setup(self) -> None:
        try:
            start_server()
        except Exception as exc:
            print(f"Krita Agent API shim failed to start: {exc}")

    def createActions(self, window: Any) -> None:  # noqa: N802
        action = window.createAction(
            "krita_agent_bridge_start",
            "Start Krita Agent API Shim",
            "tools/scripts",
        )
        action.triggered.connect(start_server)


Krita.instance().addExtension(KritaAgentBridgeShim(Krita.instance()))
'''


def build_zip(output: Path, write_checksum: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{PLUGIN_ID}.desktop", DESKTOP_ENTRY)
        archive.writestr(f"{PLUGIN_ID}/__init__.py", PLUGIN_ENTRYPOINT)
        for name in SHIM_FILES:
            if name == "__init__.py":
                continue
            source = REPO_ROOT / "shim" / name
            archive.write(source, f"{PLUGIN_ID}/{name}")

    if write_checksum:
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{checksum}  {output.name}\n",
            encoding="utf-8",
        )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sha256", action="store_true")
    args = parser.parse_args(argv)
    path = build_zip(args.output, write_checksum=args.sha256)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
