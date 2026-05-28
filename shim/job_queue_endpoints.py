"""Job queue endpoint helpers for the Krita shim."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import urlopen


@dataclass
class JobQueueBridge:
    """Normalize optional AI Diffusion job state into bridge shape."""

    provider: Any = None
    executor: Any = None
    request_timeout: float = 10.0
    comfyui_api_url: str = ""
    comfyui_confirmation_delay: float = 1.0
    prompt_to_job: dict[str, str] = field(default_factory=dict)
    history_items: list[dict[str, Any]] = field(default_factory=list)

    def jobs(self) -> dict[str, Any]:
        raw_jobs = self._raw_jobs()
        job_metadata = self._ai_diffusion_job_metadata()
        normalized = [
            self._with_comfyui_confirmation(
                self._merge_job_metadata(self._normalize_job(item), job_metadata)
            )
            for item in raw_jobs
        ]
        counts = {
            "queued": sum(1 for item in normalized if item["state"] == "queued"),
            "executing": sum(1 for item in normalized if item["state"] == "executing"),
            "finished": sum(1 for item in normalized if item["state"] == "finished"),
        }
        return {**counts, "jobs": normalized}

    def history(self, limit: int = 20) -> dict[str, Any]:
        if self.provider is not None:
            try:
                raw = self._call_or_attr(self.provider, ("history", "job_history"), None)
                if raw is not None:
                    items = raw() if callable(raw) else raw
                    history = [
                        self._with_comfyui_confirmation(
                            self._merge_job_metadata(self._normalize_history(item), self._ai_diffusion_job_metadata())
                        )
                        for item in list(items)[-limit:]
                    ]
                    return {"history": history}
            except Exception as exc:
                return {
                    "history": [],
                    "ok": False,
                    "error": "job_history_unavailable",
                    "message": str(exc),
                }
        ai_history = self._ai_diffusion_history(limit)
        if ai_history is not None:
            return {"history": ai_history}
        return {"history": self.history_items[-limit:]}

    def register_mapping(self, prompt_id: str, job_id: str) -> None:
        if prompt_id and job_id:
            self.prompt_to_job[prompt_id] = job_id

    def record_finished(self, job: dict[str, Any]) -> None:
        item = dict(job)
        item.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
        self.history_items.append(self._normalize_history(item))

    def _raw_jobs(self) -> list[Any]:
        if self.provider is None:
            return self._ai_diffusion_jobs()
        try:
            raw = self._call_or_attr(self.provider, ("jobs", "job_queue", "queue"), [])
            if callable(raw):
                raw = raw()
            if isinstance(raw, dict):
                if "jobs" in raw and isinstance(raw["jobs"], list):
                    return raw["jobs"]
                return list(raw.values())
            return list(raw or [])
        except Exception as exc:
            return [{"job_id": "", "state": "error", "progress": 0.0, "error": str(exc)}]

    def _call_on_executor(self, func: Callable[[], Any]) -> Any:
        if self.executor is None or not hasattr(self.executor, "call"):
            return func()
        return self.executor.call(func, timeout=self.request_timeout)

    def _active_ai_model(self) -> Any | None:
        try:
            root_module = importlib.import_module("ai_diffusion.root")
            root = getattr(root_module, "root", None)
            if root is None or not hasattr(root, "model_for_active_document"):
                return None
            return self._call_on_executor(root.model_for_active_document)
        except Exception:
            return None

    def _ai_diffusion_jobs(self) -> list[Any]:
        model = self._active_ai_model()
        jobs = getattr(model, "jobs", None)
        if jobs is None:
            return []
        try:
            return list(jobs)
        except Exception as exc:
            return [{"job_id": "", "state": "error", "progress": 0.0, "error": str(exc)}]

    def _ai_diffusion_history(self, limit: int) -> list[dict[str, Any]] | None:
        model = self._active_ai_model()
        if model is None:
            return None
        try:
            raw = model.history() if hasattr(model, "history") else []
            return [
                self._with_comfyui_confirmation(
                    self._merge_job_metadata(self._normalize_history(item), self._ai_diffusion_job_metadata())
                )
                for item in list(raw)[-limit:]
            ]
        except Exception:
            return None

    def _ai_diffusion_job_metadata(self) -> dict[str, dict[str, str]]:
        try:
            module = importlib.import_module("ai_diffusion.pi_external")
            store = getattr(module, "store", None)
            snapshot = store.snapshot() if store is not None and hasattr(store, "snapshot") else {}
        except Exception:
            return {}
        if not isinstance(snapshot, dict):
            return {}

        metadata: dict[str, dict[str, str]] = {}
        committed = snapshot.get("last_committed")
        if isinstance(committed, dict):
            request_id = str(committed.get("request_id", ""))
            run_id = str(committed.get("run_id", ""))
            job_ids = committed.get("job_ids", [])
            if isinstance(job_ids, list):
                for job_id in job_ids:
                    if job_id:
                        metadata[str(job_id)] = {"request_id": request_id, "run_id": run_id}

        run_to_jobs = snapshot.get("run_to_jobs")
        if isinstance(run_to_jobs, dict):
            for run_id, job_ids in run_to_jobs.items():
                if not isinstance(job_ids, list):
                    continue
                for job_id in job_ids:
                    if job_id:
                        metadata.setdefault(str(job_id), {})["run_id"] = str(run_id)
        return metadata

    @staticmethod
    def _merge_job_metadata(
        job: dict[str, Any],
        metadata: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        extra = metadata.get(str(job.get("job_id", "")))
        if not extra:
            return job
        merged = dict(job)
        merged.update({key: value for key, value in extra.items() if value})
        return merged

    def _with_comfyui_confirmation(self, job: dict[str, Any]) -> dict[str, Any]:
        if not self.comfyui_api_url:
            return job
        lookup_id = str(job.get("prompt_id") or job.get("job_id") or "")
        if not lookup_id:
            return job

        checked_at = time.time()
        history = self._comfyui_history(lookup_id)
        confirmed = isinstance(history, dict) and bool(history)
        status = "finished" if self._history_has_outputs(history, lookup_id) else ""
        enriched = dict(job)
        enriched["comfyui"] = {
            "checked": True,
            "confirmed": confirmed,
            "state": status or ("seen" if confirmed else "unknown"),
            "confirmation_delay": self.comfyui_confirmation_delay,
            "checked_at": checked_at,
        }
        if status == "finished":
            enriched["state"] = "finished"
            enriched["progress"] = 1.0
        return enriched

    def _comfyui_history(self, prompt_id: str) -> dict[str, Any] | None:
        url = f"{self.comfyui_api_url.rstrip('/')}/history/{prompt_id}"
        try:
            with urlopen(url, timeout=min(self.request_timeout, 3.0)) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def _history_has_outputs(history: dict[str, Any] | None, prompt_id: str) -> bool:
        if not isinstance(history, dict):
            return False
        entry = history.get(prompt_id, history)
        return isinstance(entry, dict) and bool(entry.get("outputs"))

    @staticmethod
    def _call_or_attr(target: Any, names: tuple[str, ...], default: Any) -> Any:
        for name in names:
            if hasattr(target, name):
                return getattr(target, name)
        return default

    def _normalize_job(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            state = getattr(item, "state", getattr(item, "status", "unknown"))
            params = getattr(item, "params", None)
            results = getattr(item, "results", None)
            try:
                result_count = len(results) if results is not None else 0
            except Exception:
                result_count = 0
            item = {
                "job_id": getattr(item, "job_id", getattr(item, "id", "")),
                "prompt_id": getattr(item, "prompt_id", ""),
                "state": getattr(state, "name", str(state)),
                "progress": getattr(item, "progress", 0.0),
                "parameters": self._params_payload(params),
                "result_count": result_count,
                "created_at": str(getattr(item, "timestamp", "")),
            }
        prompt_id = str(item.get("prompt_id", ""))
        job_id = str(item.get("job_id", item.get("id", self.prompt_to_job.get(prompt_id, ""))))
        state = str(item.get("state", item.get("status", "unknown"))).lower()
        if state in {"running", "active", "processing"}:
            state = "executing"
        if state in {"done", "complete", "completed", "success"}:
            state = "finished"
        try:
            progress = float(item.get("progress", 0.0))
        except (TypeError, ValueError):
            progress = 0.0
        if state == "finished" and progress == 0.0:
            progress = 1.0
        progress = max(0.0, min(1.0, progress))
        return {
            "job_id": job_id,
            "prompt_id": prompt_id,
            "state": state,
            "progress": progress,
            "result_count": int(item.get("result_count", 0) or 0),
            "request_id": str(item.get("request_id", "")),
            "run_id": str(item.get("run_id", "")),
        }

    def _normalize_history(self, item: Any) -> dict[str, Any]:
        job = self._normalize_job(item)
        if not isinstance(item, dict):
            params = getattr(item, "params", None)
            item = {
                "parameters": self._params_payload(params),
                "finished_at": str(getattr(item, "timestamp", "")),
            }
        return {
            **job,
            "output_path": str(item.get("output_path", "")),
            "parameters": item.get("parameters", {}),
            "finished_at": str(item.get("finished_at", "")),
        }

    @staticmethod
    def _params_payload(params: Any) -> dict[str, Any]:
        if params is None:
            return {}
        metadata = getattr(params, "metadata", {})
        return {
            "name": str(getattr(params, "name", "")),
            "seed": getattr(params, "seed", None),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        }
