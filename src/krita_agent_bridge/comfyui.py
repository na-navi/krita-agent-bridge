"""ComfyUI adapter for krita-agent-bridge.

Issue #6: Direct ComfyUI adapter for backend diagnostics and optional
workflow execution.

Provides:
- /object_info inspection
- /queue status retrieval
- /history retrieval
- /prompt submission with schema validation
- Output file resolution

Error classification:
- ConnectionFailure: ComfyUI server unreachable
- ValidationError: prompt schema doesn't match object_info
- ExecutionFailure: workflow submitted but failed during execution
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client import JsonEndpointClient


class AdapterError(Enum):
    CONNECTION = "connection"
    VALIDATION = "validation"
    EXECUTION = "execution"
    NONE = "none"


@dataclass(frozen=True)
class ComfyUIResult:
    """Result from a ComfyUI adapter operation."""
    ok: bool
    error: AdapterError = AdapterError.NONE
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class NodeSchema:
    """Schema for a single ComfyUI node type."""
    name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[dict[str, Any]] = field(default_factory=list)


class ComfyUIAdapter:
    """Adapter for ComfyUI server API.

    Distinguishes connection failure, validation failure, and
    execution failure across all operations.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout: float = 10.0) -> None:
        self.client = JsonEndpointClient(base_url, timeout=timeout)
        self.base_url = base_url.rstrip("/")

    # -----------------------------------------------------------------------
    # /object_info — node schema inspection
    # -----------------------------------------------------------------------

    def object_info(self, node_filter: str | None = None) -> ComfyUIResult:
        """Fetch node definitions from /object_info.

        Args:
            node_filter: If set, only return info for this specific node type.
        """
        path = "/object_info"
        if node_filter:
            path = f"/object_info/{node_filter}"

        result = self.client.get_json(path)
        if not result.ok:
            return ComfyUIResult(
                ok=False,
                error=AdapterError.CONNECTION,
                message=f"ComfyUI unreachable: {result.error}",
            )

        if not isinstance(result.data, dict):
            return ComfyUIResult(
                ok=False,
                error=AdapterError.VALIDATION,
                message="Expected dict response from /object_info",
            )

        return ComfyUIResult(ok=True, data=result.data)

    def get_node_schema(self, node_type: str) -> ComfyUIResult:
        """Get the schema for a specific node type."""
        result = self.object_info(node_filter=node_type)
        if not result.ok:
            return result

        if node_type not in result.data:
            return ComfyUIResult(
                ok=False,
                error=AdapterError.VALIDATION,
                message=f"Node type '{node_type}' not found in object_info",
            )

        node_info = result.data[node_type]
        inputs = node_info.get("input", {}).get("required", {})
        outputs = node_info.get("output", [])
        return ComfyUIResult(
            ok=True,
            data=NodeSchema(
                name=node_type,
                inputs=inputs,
                outputs=outputs,
            ),
        )

    # -----------------------------------------------------------------------
    # /queue — queue status
    # -----------------------------------------------------------------------

    def queue_status(self) -> ComfyUIResult:
        """Get current queue status from /queue."""
        result = self.client.get_json("/queue")
        if not result.ok:
            return ComfyUIResult(
                ok=False,
                error=AdapterError.CONNECTION,
                message=f"ComfyUI unreachable: {result.error}",
            )
        return ComfyUIResult(ok=True, data=result.data)

    # -----------------------------------------------------------------------
    # /history — execution history
    # -----------------------------------------------------------------------

    def history(self, prompt_id: str | None = None) -> ComfyUIResult:
        """Get execution history.

        Args:
            prompt_id: If set, get history for a specific prompt.
        """
        path = "/history"
        if prompt_id:
            path = f"/history/{prompt_id}"

        result = self.client.get_json(path)
        if not result.ok:
            return ComfyUIResult(
                ok=False,
                error=AdapterError.CONNECTION,
                message=f"ComfyUI unreachable: {result.error}",
            )
        return ComfyUIResult(ok=True, data=result.data)

    # -----------------------------------------------------------------------
    # /prompt — submit workflow
    # -----------------------------------------------------------------------

    def validate_prompt(self, workflow: dict[str, Any]) -> ComfyUIResult:
        """Validate a workflow prompt against known node schemas.

        Checks that all node types referenced in the workflow exist
        in /object_info.
        """
        # Fetch all node info
        info_result = self.object_info()
        if not info_result.ok:
            return info_result

        available_nodes = set(info_result.data.keys())

        # Extract node types from the workflow
        nodes = workflow.get("nodes", [])
        for node in nodes:
            node_type = node.get("type")
            if node_type and node_type not in available_nodes:
                return ComfyUIResult(
                    ok=False,
                    error=AdapterError.VALIDATION,
                    message=f"Unknown node type: '{node_type}'",
                )

        return ComfyUIResult(ok=True, message="Workflow validated")

    def submit_prompt(self, prompt: dict[str, Any]) -> ComfyUIResult:
        """Submit a prompt to ComfyUI via POST /prompt.

        The prompt dict should have the ComfyUI API format:
        {"prompt": {...}, "client_id": "..."}

        For now this validates + returns instructions, since POST
        is not yet supported by JsonEndpointClient (GET only).
        """
        # Validate node types first
        nodes = prompt.get("prompt", prompt).get("nodes", [])
        info_result = self.object_info()
        if not info_result.ok:
            return info_result

        available_nodes = set(info_result.data.keys())
        for node in nodes:
            node_type = node.get("type")
            if node_type and node_type not in available_nodes:
                return ComfyUIResult(
                    ok=False,
                    error=AdapterError.VALIDATION,
                    message=f"Unknown node type: '{node_type}'",
                )

        return ComfyUIResult(
            ok=True,
            message="Prompt validated (submission requires POST support)",
        )

    # -----------------------------------------------------------------------
    # Output file resolution
    # -----------------------------------------------------------------------

    def resolve_outputs(self, prompt_id: str) -> ComfyUIResult:
        """Resolve output file paths from a completed execution.

        Returns absolute paths to generated files.
        """
        hist_result = self.history(prompt_id)
        if not hist_result.ok:
            return hist_result

        history_data = hist_result.data
        if not isinstance(history_data, dict):
            return ComfyUIResult(
                ok=False,
                error=AdapterError.VALIDATION,
                message="History response is not a dict",
            )

        prompt_history = history_data.get(prompt_id, history_data)

        # Extract outputs
        outputs = prompt_history.get("outputs", {})
        files: list[str] = []

        for _node_id, node_outputs in outputs.items():
            if not isinstance(node_outputs, dict):
                continue
            for key in ("images", "gifs", "videos", "audio"):
                for item in node_outputs.get(key, []):
                    filename = item.get("filename", "")
                    subfolder = item.get("subfolder", "")
                    if filename:
                        # Build absolute path from ComfyUI output directory
                        # ComfyUI default: output/ under its working directory
                        path = os.path.join("output", subfolder, filename)
                        files.append(os.path.abspath(path))

        return ComfyUIResult(ok=True, data=files)
