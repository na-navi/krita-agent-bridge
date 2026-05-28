"""AI Diffusion plugin boundary for the Krita shim.

The upstream plugin does not expose a stable public automation API, so this
module treats every internal access as optional and returns structured errors.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DiffusionResult:
    ok: bool
    data: dict[str, Any]
    status: int = 200


class AIDiffusionBridge:
    """Defensive adapter over optional AI Diffusion internals."""

    def __init__(self, provider: Any = None, executor: Any = None, request_timeout: float = 10.0) -> None:
        self.provider = provider
        self.executor = executor
        self.request_timeout = request_timeout
        self._module: Any | None = None
        self._module_error = ""

    def status_fields(self) -> dict[str, Any]:
        detected = self._provider_or_module()
        available = detected is not None
        snapshot = self._snapshot()
        model = self._model_from_snapshot(snapshot)
        return {
            "ai_diffusion_available": available,
            "ai_diffusion_version": self._version(detected) if available else "",
            "ai_diffusion_mode": self._mode(detected) if available else "",
            "active_model": model.get("model", "") if available else "",
        }

    def styles(self) -> DiffusionResult:
        ai_styles = self._ai_diffusion_styles()
        if ai_styles is not None:
            return DiffusionResult(True, {"styles": ai_styles})

        target = self._provider_or_module()
        if target is None:
            return self._unavailable("styles")
        try:
            styles = self._call_or_attr(target, ("styles", "style_names", "available_styles"), [])
            if isinstance(styles, dict):
                styles = list(styles.keys())
            if callable(styles):
                styles = styles()
            if styles is None:
                styles = []
            return DiffusionResult(True, {"styles": [str(item) for item in styles]})
        except Exception as exc:
            return self._internal_error("styles", exc)

    def model(self) -> DiffusionResult:
        snapshot = self._snapshot()
        model = self._model_from_snapshot(snapshot)
        if model["model"] or snapshot is not None:
            return DiffusionResult(True, model)

        target = self._provider_or_module()
        if target is None:
            return self._unavailable("model")
        try:
            return DiffusionResult(True, self._model(target))
        except Exception as exc:
            return self._internal_error("model", exc)

    def get_mode(self) -> DiffusionResult:
        mode = self._mode_from_external_store()
        if mode is not None:
            return DiffusionResult(True, {"mode": mode})

        target = self._provider_or_module()
        if target is None:
            return self._unavailable("mode")
        try:
            return DiffusionResult(True, {"mode": self._mode(target)})
        except Exception as exc:
            return self._internal_error("mode", exc)

    def set_mode(self, mode: str) -> DiffusionResult:
        valid = ("manual", "watch", "auto")
        if mode not in valid:
            return DiffusionResult(
                False,
                {
                    "error": "invalid_mode",
                    "message": "Mode must be manual, watch, or auto",
                    "valid_modes": list(valid),
                },
                status=400,
            )
        target = self._provider_or_module()
        if target is None:
            return self._unavailable("mode")
        try:
            store = self._external_store()
            if store is not None and hasattr(store, "set_mode"):
                ok, value = store.set_mode(mode)
                if not ok:
                    return DiffusionResult(False, {"error": "mode_unavailable", "message": value})
                mode = str(value)
            elif hasattr(target, "set_mode"):
                target.set_mode(mode)
            elif hasattr(target, "mode"):
                setattr(target, "mode", mode)
            else:
                return DiffusionResult(
                    False,
                    {
                        "error": "mode_unavailable",
                        "message": "AI Diffusion mode setter not found",
                    },
                )
            return DiffusionResult(True, {"mode": mode, "status": "ok"})
        except Exception as exc:
            return self._internal_error("mode", exc)

    def params(self) -> DiffusionResult:
        snapshot = self._snapshot()
        model = snapshot.get("model") if isinstance(snapshot, dict) else None
        if isinstance(model, dict):
            return DiffusionResult(
                True,
                {
                    "style": model.get("style"),
                    "seed": model.get("seed"),
                    "strength": model.get("strength"),
                    "workspace": model.get("workspace"),
                    "error": model.get("error"),
                },
            )

        target = self._provider_or_module()
        if target is None:
            return self._unavailable("params")
        try:
            params = self._call_or_attr(target, ("params", "parameters", "generation_params"), {})
            if callable(params):
                params = params()
            if not isinstance(params, dict):
                params = {}
            return DiffusionResult(True, params)
        except Exception as exc:
            return self._internal_error("params", exc)

    def generate(self, request: dict[str, Any]) -> DiffusionResult:
        bridge_result = self._generate_via_ai_bridge(request)
        if bridge_result is not None:
            if bridge_result.ok or "bridge not initialized" not in str(bridge_result.data.get("message", "")):
                return bridge_result

        root_result = self._generate_via_ai_root(request)
        if root_result is not None:
            return root_result

        if bridge_result is not None:
            return bridge_result

        target = self._provider_or_module()
        if target is None:
            return self._unavailable("generate")
        try:
            for method_name in ("generate", "enqueue", "submit"):
                method = getattr(target, method_name, None)
                if callable(method):
                    raw = method(**request)
                    return DiffusionResult(True, self._job_payload(raw))
            return DiffusionResult(
                False,
                {
                    "error": "generate_unavailable",
                    "message": "AI Diffusion generation entrypoint not found",
                },
            )
        except Exception as exc:
            return self._internal_error("generate", exc)

    def _call_on_executor(self, func: Callable[[], DiffusionResult]) -> DiffusionResult:
        if self.executor is None or not hasattr(self.executor, "call"):
            return func()
        return self.executor.call(func, timeout=self.request_timeout)

    def _provider_or_module(self) -> Any:
        if self.provider is not None:
            return self.provider
        if self._module is not None:
            return self._module
        for module_name in ("krita_ai_diffusion", "ai_diffusion"):
            try:
                self._module = importlib.import_module(module_name)
                return self._module
            except Exception as exc:
                self._module_error = str(exc)
        return None

    def _import_ai_module(self, module_name: str) -> Any | None:
        try:
            return importlib.import_module(f"ai_diffusion.{module_name}")
        except Exception as exc:
            self._module_error = str(exc)
            return None

    def _external_store(self) -> Any | None:
        module = self._import_ai_module("pi_external")
        return getattr(module, "store", None) if module is not None else None

    def _snapshot(self) -> dict[str, Any] | None:
        module = self._import_ai_module("pi_api_bridge")
        if module is None or not hasattr(module, "request_snapshot"):
            return None
        try:
            snapshot = module.request_snapshot(timeout=2.0)
        except Exception as exc:
            self._module_error = str(exc)
            return None
        return snapshot if isinstance(snapshot, dict) else None

    def _ai_diffusion_styles(self) -> list[str] | None:
        module = self._import_ai_module("style")
        if module is None or not hasattr(module, "Styles"):
            return None
        try:
            styles = module.Styles.list().filtered()
            return [str(style.filename) for style in styles]
        except Exception as exc:
            self._module_error = str(exc)
            return None

    def _mode_from_external_store(self) -> str | None:
        store = self._external_store()
        if store is None or not hasattr(store, "get_mode"):
            return None
        try:
            return str(store.get_mode())
        except Exception as exc:
            self._module_error = str(exc)
            return None

    def _generate_via_ai_bridge(self, request: dict[str, Any]) -> DiffusionResult | None:
        module = self._import_ai_module("pi_api_bridge")
        if module is None:
            return None
        if not hasattr(module, "request_prepare") or not hasattr(module, "request_trigger"):
            return None

        try:
            prepare = module.request_prepare(
                {"fields": request, "replace": True, "ttl_seconds": 300},
                timeout=5.0,
            )
            if not isinstance(prepare, dict):
                return DiffusionResult(
                    False,
                    {"error": "prepare_error", "message": "Invalid prepare response"},
                )
            if prepare.get("error"):
                return DiffusionResult(
                    False,
                    {"error": "prepare_error", "message": str(prepare.get("error"))},
                    status=int(prepare.get("http_code", 400)),
                )

            trigger = module.request_trigger({}, timeout=5.0)
            if not isinstance(trigger, dict):
                return DiffusionResult(
                    False,
                    {"error": "trigger_error", "message": "Invalid trigger response"},
                )
            if trigger.get("error"):
                return DiffusionResult(
                    False,
                    {"error": "trigger_error", "message": str(trigger.get("error"))},
                    status=int(trigger.get("http_code", 400)),
                )

            data = trigger.get("data") or {}
            request_id = str(data.get("request_id", prepare.get("data", {}).get("request_id", "")))
            return DiffusionResult(
                True,
                {"job_id": request_id, "prompt_id": "", "status": str(data.get("state", "queued"))},
                status=int(trigger.get("http_code", 202)),
            )
        except Exception as exc:
            return self._internal_error("generate", exc)

    def _generate_via_ai_root(self, request: dict[str, Any]) -> DiffusionResult | None:
        pi_external = self._import_ai_module("pi_external")
        root_module = self._import_ai_module("root")
        if pi_external is None or root_module is None:
            return None

        try:
            allowed = set(getattr(pi_external, "ALLOWED_FIELDS", ()))
            fields_raw = {key: value for key, value in request.items() if not allowed or key in allowed}
            validate = getattr(pi_external, "validate_prepare_fields", None)
            if callable(validate):
                fields, reason = validate(fields_raw)
                if fields is None:
                    return DiffusionResult(
                        False,
                        {"error": "validation", "message": str(reason)},
                        status=400,
                    )
            else:
                fields = fields_raw

            style_name = fields.get("style") if isinstance(fields, dict) else None
            if style_name:
                styles = self._import_ai_module("style")
                style_list = getattr(getattr(styles, "Styles", None), "list", None)
                if callable(style_list) and style_list().find(str(style_name)) is None:
                    return DiffusionResult(
                        False,
                        {"error": "style_not_found", "message": f"Style not found: {style_name}"},
                        status=404,
                    )

            def run_generate() -> DiffusionResult:
                root = getattr(root_module, "root", None)
                if root is None or not hasattr(root, "model_for_active_document"):
                    return DiffusionResult(
                        False,
                        {
                            "error": "generate_unavailable",
                            "message": "AI Diffusion active document model is unavailable",
                        },
                    )
                model = root.model_for_active_document()
                if model is None:
                    return DiffusionResult(
                        False,
                        {"error": "document_unavailable", "message": "No active Krita document"},
                        status=400,
                    )
                jobs = getattr(model, "jobs", None)
                if jobs is not None and hasattr(jobs, "any_executing") and jobs.any_executing():
                    return DiffusionResult(
                        False,
                        {"error": "busy", "message": "AI Diffusion already has an executing job"},
                        status=409,
                    )

                now = time.time()
                request_id = str(getattr(pi_external, "new_request_id", lambda: "")() or "")
                pending_type = getattr(pi_external, "PendingRequest", None)
                if not request_id or pending_type is None:
                    return DiffusionResult(
                        False,
                        {"error": "generate_unavailable", "message": "AI Diffusion pending request API is unavailable"},
                    )

                document = getattr(model, "document", None)
                document_id = str(getattr(document, "id", "") or getattr(getattr(model, "_doc", None), "id", "") or "")
                pending = pending_type(
                    request_id=request_id,
                    document_id=document_id,
                    fields=dict(fields),
                    replace=True,
                    created_at=now,
                    expires_at=now + 300,
                    requested_trigger="api",
                )
                store = getattr(pi_external, "store", None)
                if store is None or not hasattr(store, "set_pending"):
                    return DiffusionResult(
                        False,
                        {"error": "generate_unavailable", "message": "AI Diffusion pending store is unavailable"},
                    )
                ok, reason = store.set_pending(pending, replace=True)
                if not ok:
                    return DiffusionResult(
                        False,
                        {"error": "prepare_error", "message": str(reason)},
                        status=409,
                    )

                trigger_type = getattr(pi_external, "TriggerEntry", None)
                if trigger_type is not None and hasattr(store, "record_trigger"):
                    store.record_trigger(
                        trigger_type(request_id=request_id, state="scheduled", created_at=now)
                    )

                existing_job_ids = self._model_job_ids(model)
                model.generate()
                job_id = request_id
                prompt_id = ""
                new_job_ids = [
                    item for item in self._model_job_ids(model) if item not in existing_job_ids
                ]
                if new_job_ids:
                    job_id = new_job_ids[0]
                    prompt_id = new_job_ids[0]
                snapshot = store.snapshot() if hasattr(store, "snapshot") else {}
                if isinstance(snapshot, dict):
                    committed = snapshot.get("last_committed")
                    if isinstance(committed, dict) and committed.get("request_id") == request_id:
                        job_ids = committed.get("job_ids")
                        if isinstance(job_ids, list) and job_ids:
                            job_id = str(job_ids[0])
                            prompt_id = job_id
                    run_to_jobs = snapshot.get("run_to_jobs")
                    if prompt_id == "" and isinstance(run_to_jobs, dict):
                        for ids in run_to_jobs.values():
                            if isinstance(ids, list) and ids:
                                job_id = str(ids[-1])
                                prompt_id = job_id
                return DiffusionResult(
                    True,
                    {
                        "job_id": job_id,
                        "prompt_id": prompt_id,
                        "request_id": request_id,
                        "job_ids": new_job_ids,
                        "_existing_job_ids": existing_job_ids,
                        "status": "scheduled",
                    },
                    status=202,
                )

            result = self._call_on_executor(run_generate)
            if result.ok and not result.data.get("job_ids"):
                existing_ids = set(result.data.pop("_existing_job_ids", []))
                time.sleep(1.0)
                try:
                    new_job_ids = self._call_on_executor(
                        lambda: [
                            item
                            for item in self._active_model_job_ids(root_module)
                            if item not in existing_ids
                        ]
                    )
                except Exception:
                    new_job_ids = []
                if new_job_ids:
                    result.data["job_id"] = new_job_ids[0]
                    result.data["prompt_id"] = new_job_ids[0]
                    result.data["job_ids"] = new_job_ids
            else:
                result.data.pop("_existing_job_ids", None)
            return result
        except Exception as exc:
            return self._internal_error("generate", exc)

    def _active_model_job_ids(self, root_module: Any) -> list[str]:
        root = getattr(root_module, "root", None)
        if root is None or not hasattr(root, "model_for_active_document"):
            return []
        model = root.model_for_active_document()
        return self._model_job_ids(model)

    @staticmethod
    def _model_job_ids(model: Any) -> list[str]:
        jobs = getattr(model, "jobs", None)
        if jobs is None:
            return []
        try:
            return [str(getattr(job, "id", "") or "") for job in list(jobs) if getattr(job, "id", "")]
        except Exception:
            return []

    def _unavailable(self, feature: str) -> DiffusionResult:
        message = "AI Diffusion plugin is not available"
        if self._module_error:
            message = f"{message}: {self._module_error}"
        return DiffusionResult(
            False,
            {"error": "ai_diffusion_unavailable", "feature": feature, "message": message},
        )

    @staticmethod
    def _internal_error(feature: str, exc: Exception) -> DiffusionResult:
        return DiffusionResult(False, {"error": f"{feature}_error", "message": str(exc)})

    @staticmethod
    def _call_or_attr(target: Any, names: tuple[str, ...], default: Any) -> Any:
        for name in names:
            if hasattr(target, name):
                value = getattr(target, name)
                return value() if callable(value) else value
        return default

    def _version(self, target: Any) -> str:
        return str(self._call_or_attr(target, ("version", "__version__"), ""))

    def _mode(self, target: Any) -> str:
        return str(self._call_or_attr(target, ("mode", "current_mode"), ""))

    def _model(self, target: Any) -> dict[str, Any]:
        model = self._call_or_attr(target, ("active_model", "model", "current_model"), "")
        if isinstance(model, dict):
            return {"model": str(model.get("name", "")), "loaded": bool(model.get("loaded", True))}
        return {"model": str(model or ""), "loaded": bool(model)}

    def _model_from_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {"model": "", "loaded": False}
        model = snapshot.get("model")
        if not isinstance(model, dict):
            return {"model": "", "loaded": False}
        active = model.get("active_model") or model.get("model") or model.get("style") or ""
        return {"model": str(active or ""), "loaded": bool(active)}

    @staticmethod
    def _job_payload(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return {
                "job_id": str(raw.get("job_id", raw.get("id", ""))),
                "prompt_id": str(raw.get("prompt_id", "")),
                "status": str(raw.get("status", "queued")),
            }
        return {"job_id": str(raw or ""), "status": "queued"}
