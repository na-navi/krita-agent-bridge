"""Tests for reproducible Krita shim ZIP packaging."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_krita_shim_zip import PLUGIN_ID, SHIM_FILES, build_zip


def test_build_krita_shim_zip_layout(tmp_path: Path) -> None:
    output = tmp_path / "krita_agent_bridge_shim.zip"
    build_zip(output, write_checksum=True)

    assert output.exists()
    assert output.with_suffix(".zip.sha256").exists()

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert f"{PLUGIN_ID}.desktop" in names
        assert f"{PLUGIN_ID}/__init__.py" in names
        for filename in SHIM_FILES:
            assert f"{PLUGIN_ID}/{filename}" in names

        desktop = archive.read(f"{PLUGIN_ID}.desktop").decode("utf-8")
        assert "ServiceTypes=Krita/PythonPlugin" in desktop
        assert f"X-KDE-Library={PLUGIN_ID}" in desktop

        entrypoint = archive.read(f"{PLUGIN_ID}/__init__.py").decode("utf-8")
        assert "Start Krita Agent API Shim" in entrypoint
        assert "create_server()" in entrypoint
        assert "def setup" in entrypoint
        assert "start_server()" in entrypoint
