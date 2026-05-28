"""End-to-end smoke workflow runner for Krita + shim + ComfyUI."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .client import EndpointResult, JsonEndpointClient
from .comfyui import ComfyUIAdapter
from .doctor import run_doctor
from .job_monitor import JobMonitor
from .prepare import PrepareInput, build_workflow
from .readiness import ReadinessProbe
from .snapshot import Snapshot, SnapshotAdapter


@dataclass(frozen=True)
class SmokeStep:
    timestamp: float
    step: str
    ok: bool
    data: Any = None


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    report_path: str
    output_path: str
    steps: tuple[SmokeStep, ...]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "report_path": self.report_path,
            "output_path": self.output_path,
            "steps": [_jsonable(step) for step in self.steps],
        }


def run_smoke_workflow(
    *,
    krita_api: str = "http://127.0.0.1:8900",
    comfyui_api: str = "http://127.0.0.1:8188",
    report_path: str | Path = "smoke_report.json",
    output_path: str | Path = "smoke_output.png",
    document_name: str = "smoke",
    positive: str = "1girl, test",
    seed: int = 42,
    checkpoint: str | None = None,
    width: int = 1024,
    height: int = 1024,
    timeout: float = 120.0,
    request_timeout: float = 10.0,
    interval: float = 1.0,
) -> SmokeResult:
    """Run the issue #35 smoke workflow and always write a JSON report."""

    report_file = Path(report_path)
    output_file = Path(output_path)
    steps: list[SmokeStep] = []
    client = JsonEndpointClient(krita_api, timeout=request_timeout)
    comfy = ComfyUIAdapter(comfyui_api, timeout=request_timeout)
    jobs = JobMonitor(krita_api, timeout=request_timeout)

    def record(step: str, ok: bool, data: Any = None) -> None:
        steps.append(SmokeStep(time.time(), step, ok, _jsonable(data)))

    def finish(ok: bool, message: str) -> SmokeResult:
        result = SmokeResult(
            ok=ok,
            report_path=str(report_file),
            output_path=str(output_file),
            steps=tuple(steps),
            message=message,
        )
        report_file.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    try:
        doctor = run_doctor(krita_api=krita_api, comfyui_api=comfyui_api, check_ports=False)
        record("doctor", doctor.exit_code == 0, doctor.to_dict())
        if doctor.exit_code != 0:
            return finish(False, "doctor checks did not pass")

        snapshot_result = SnapshotAdapter(
            krita_url=krita_api,
            comfyui_url=comfyui_api,
            timeout=request_timeout,
        ).snapshot()
        snapshot = snapshot_result.data
        snapshot_ok = isinstance(snapshot, Snapshot) and snapshot.bridge.connected
        snapshot_data = snapshot.to_dict() if isinstance(snapshot, Snapshot) else snapshot_result
        record("snapshot", snapshot_ok, snapshot_data)
        if not snapshot_ok:
            return finish(False, "snapshot did not report a connected bridge")

        created = client.post_json(
            "/api/document/create",
            {"name": document_name, "width": width, "height": height},
        )
        record("create_document", created.ok, created)
        if not created.ok:
            return finish(False, "document creation failed")

        readiness = ReadinessProbe(
            krita_api=krita_api,
            comfyui_api=comfyui_api,
            timeout=request_timeout,
        ).wait(timeout=timeout, interval=interval)
        record("readiness", readiness.ready, readiness.to_dict())
        if not readiness.ready:
            return finish(False, "readiness checks did not pass")

        styles = client.get_json("/api/diffusion/styles")
        styles_data = styles.data if isinstance(styles.data, dict) else {}
        styles_ok = styles.ok and bool(styles_data.get("styles"))
        record("diffusion_styles", styles_ok, styles)
        if not styles_ok:
            return finish(False, "diffusion styles are unavailable")

        mode = client.post_json("/api/diffusion/mode", {"mode": "auto"})
        record("set_mode", mode.ok, mode)
        if not mode.ok:
            return finish(False, "failed to set diffusion mode")

        resolved_checkpoint = checkpoint or _checkpoint_from_comfy(comfy)
        workflow = build_workflow(
            PrepareInput(
                positive=positive,
                seed=seed,
                checkpoint=resolved_checkpoint,
                width=width,
                height=height,
            ),
            comfyui_adapter=comfy,
        )
        record("build_workflow", workflow.ok, workflow)
        if not workflow.ok:
            return finish(False, "workflow construction failed")

        generated = client.post_json(
            "/api/diffusion/generate",
            {"positive": positive, "seed": seed},
        )
        generated_data = generated.data if isinstance(generated.data, dict) else {}
        job_id = str(generated_data.get("job_id", ""))
        record("generate", generated.ok and bool(job_id), generated)
        if not generated.ok or not job_id:
            return finish(False, "generation did not return a job_id")

        waited = jobs.wait_for_job(job_id, timeout=timeout, interval=interval)
        record("wait_for_job", waited.ok, waited)
        if not waited.ok:
            return finish(False, "job did not finish successfully")

        history = client.get_json("/api/jobs/history")
        output_from_history = _output_path_for_job(history, job_id)
        record("job_history", history.ok and bool(output_from_history), history)
        if not history.ok or not output_from_history:
            return finish(False, "job history did not include an output path")

        imported = client.post_json(
            "/api/document/import-layer",
            {"image_path": output_from_history, "layer_name": "Smoke"},
        )
        record("import_layer", imported.ok, imported)
        if not imported.ok:
            return finish(False, "generated output import failed")

        exported = client.post_json("/api/document/export", {"output_path": str(output_file)})
        export_ok = exported.ok and output_file.exists() and output_file.stat().st_size > 1024
        record("export_canvas", export_ok, exported)
        if not export_ok:
            return finish(False, "canvas export did not produce a non-trivial file")

        return finish(True, "smoke workflow completed")
    except Exception as exc:  # noqa: BLE001 - smoke reports should capture failures.
        record("exception", False, {"type": type(exc).__name__, "message": str(exc)})
        return finish(False, f"smoke workflow failed: {exc}")


def _checkpoint_from_comfy(comfy: ComfyUIAdapter) -> str:
    result = comfy.object_info("CheckpointLoaderSimple")
    if not result.ok:
        raise RuntimeError(result.message)
    data = result.data if isinstance(result.data, dict) else {}
    node = data.get("CheckpointLoaderSimple", {})
    choices = node.get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
    if not choices:
        raise RuntimeError("No ComfyUI checkpoints available")
    return str(choices[0])


def _output_path_for_job(history: EndpointResult, job_id: str) -> str:
    if not history.ok or not isinstance(history.data, dict):
        return ""
    for item in history.data.get("history", []):
        if isinstance(item, dict) and str(item.get("job_id", "")) == job_id:
            return str(item.get("output_path", ""))
    return ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
