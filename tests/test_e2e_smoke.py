"""Integration smoke workflow for bridge + Krita shim (Issue #35).

This test requires a running Krita process with the shim loaded, AI Diffusion,
and ComfyUI. It is skipped by default and only runs when
KRITA_AGENT_RUN_E2E=1 is set.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from krita_agent_bridge.client import JsonEndpointClient
from krita_agent_bridge.comfyui import ComfyUIAdapter
from krita_agent_bridge.doctor import run_doctor
from krita_agent_bridge.job_monitor import JobMonitor
from krita_agent_bridge.prepare import PrepareInput, build_workflow
from krita_agent_bridge.readiness import ReadinessProbe
from krita_agent_bridge.snapshot import Snapshot, SnapshotAdapter


pytestmark = pytest.mark.integration


def _record(report: list[dict[str, Any]], step: str, ok: bool, data: Any = None) -> None:
    report.append({"timestamp": time.time(), "step": step, "ok": ok, "data": data})


def _checkpoint_from_comfy(comfy: ComfyUIAdapter) -> str:
    result = comfy.object_info("CheckpointLoaderSimple")
    if not result.ok:
        raise AssertionError(result.message)
    node = result.data.get("CheckpointLoaderSimple", {})
    choices = node.get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
    if not choices:
        raise AssertionError("No ComfyUI checkpoints available")
    return str(choices[0])


def test_e2e_smoke_workflow(tmp_path: Path) -> None:
    if os.environ.get("KRITA_AGENT_RUN_E2E") != "1":
        pytest.skip("Set KRITA_AGENT_RUN_E2E=1 to run Krita/ComfyUI integration smoke")

    krita_api = os.environ.get("KRITA_AGENT_KRITA_API", "http://127.0.0.1:8900")
    comfyui_api = os.environ.get("KRITA_AGENT_COMFYUI_API", "http://127.0.0.1:8188")
    timeout = float(os.environ.get("KRITA_AGENT_SMOKE_TIMEOUT", "300"))
    report: list[dict[str, Any]] = []
    report_path = Path("smoke_report.json")

    client = JsonEndpointClient(krita_api, timeout=10)
    comfy = ComfyUIAdapter(comfyui_api, timeout=10)
    jobs = JobMonitor(krita_api, timeout=10)

    try:
        doctor = run_doctor(krita_api=krita_api, comfyui_api=comfyui_api, check_ports=False)
        _record(report, "doctor", doctor.exit_code == 0, doctor.to_dict())
        assert doctor.exit_code == 0

        snapshot_result = SnapshotAdapter(krita_url=krita_api, comfyui_url=comfyui_api).snapshot()
        snapshot = snapshot_result.data
        assert isinstance(snapshot, Snapshot)
        _record(report, "snapshot", snapshot.bridge.connected, snapshot.to_dict())
        assert snapshot.bridge.connected

        created = client.post_json(
            "/api/document/create",
            {"name": "smoke", "width": 1024, "height": 1024},
        )
        _record(report, "create_document", created.ok, created.data or created.error)
        assert created.ok

        readiness = ReadinessProbe(
            krita_api=krita_api,
            comfyui_api=comfyui_api,
            timeout=10,
        ).wait(timeout=timeout, interval=1.0)
        _record(report, "readiness", readiness.ready, readiness.to_dict())
        assert readiness.ready

        styles = client.get_json("/api/diffusion/styles")
        _record(report, "diffusion_styles", styles.ok, styles.data or styles.error)
        assert styles.ok
        assert styles.data.get("styles")

        mode = client.post_json("/api/diffusion/mode", {"mode": "auto"})
        _record(report, "set_mode", mode.ok, mode.data or mode.error)
        assert mode.ok

        checkpoint = os.environ.get("KRITA_AGENT_CHECKPOINT") or _checkpoint_from_comfy(comfy)
        workflow = build_workflow(
            PrepareInput(positive="1girl, test", seed=42, checkpoint=checkpoint),
            comfyui_adapter=comfy,
        )
        _record(report, "build_workflow", workflow.ok, workflow.message)
        assert workflow.ok

        generated = client.post_json(
            "/api/diffusion/generate",
            {
                "positive": "1girl, test",
                "seed": 42,
            },
        )
        _record(report, "generate", generated.ok, generated.data or generated.error)
        assert generated.ok
        job_id = str(generated.data.get("job_id", ""))
        assert job_id

        waited = jobs.wait_for_job(job_id, timeout=timeout)
        _record(report, "wait_for_job", waited.ok, waited.data)
        assert waited.ok

        history = client.get_json("/api/jobs/history")
        _record(report, "job_history", history.ok, history.data or history.error)
        assert history.ok
        output_path = ""
        for item in history.data.get("history", []):
            if item.get("job_id") == job_id:
                output_path = str(item.get("output_path", ""))
                break
        assert output_path

        imported = client.post_json(
            "/api/document/import-layer",
            {"image_path": output_path, "layer_name": "Smoke"},
        )
        _record(report, "import_layer", imported.ok, imported.data or imported.error)
        assert imported.ok

        export_path = tmp_path / "smoke_output.png"
        exported = client.post_json("/api/document/export", {"output_path": str(export_path)})
        _record(report, "export_canvas", exported.ok, exported.data or exported.error)
        assert exported.ok
        assert export_path.exists()
        assert export_path.stat().st_size > 1024
    finally:
        report_path.write_text(
            json.dumps({"steps": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
