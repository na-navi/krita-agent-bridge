"""Doctor mode diagnostics for krita-agent-bridge.

Issue #7: One-command diagnostic report with human-readable summary,
structured JSON, and clear exit codes.

Exit codes:
  0 = all checks passed
  1 = recoverable issue (some endpoints down, workflow possible)
  2 = fatal issue (core dependency unavailable)
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client import EndpointResult, JsonEndpointClient


class Severity(str, Enum):
    OK = "ok"
    RECOVERABLE = "recoverable"
    FATAL = "fatal"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    ok: bool
    detail: str
    hint: str = ""
    raw: EndpointResult | None = None


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def worst_severity(self) -> Severity:
        if any(c.severity == Severity.FATAL for c in self.checks):
            return Severity.FATAL
        if any(c.severity == Severity.RECOVERABLE for c in self.checks):
            return Severity.RECOVERABLE
        return Severity.OK

    @property
    def exit_code(self) -> int:
        return {Severity.OK: 0, Severity.RECOVERABLE: 1, Severity.FATAL: 2}[self.worst_severity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "severity": self.worst_severity.value,
            "checks": [
                {
                    "name": c.name,
                    "severity": c.severity.value,
                    "ok": c.ok,
                    "detail": c.detail,
                    "hint": c.hint,
                }
                for c in self.checks
            ],
        }


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is accepting connections (no data sent)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_krita_bridge(client: JsonEndpointClient) -> CheckResult:
    """Check Krita agent bridge at /api/status."""
    result = client.get_json("/api/status")
    if result.ok:
        return CheckResult(
            name="krita_bridge",
            severity=Severity.OK,
            ok=True,
            detail="Krita agent bridge is responding",
            raw=result,
        )
    severity = Severity.FATAL
    hint = "Start the Krita AI Diffusion plugin and enable the Agent API in settings."
    detail = f"Krita bridge unreachable: {result.error}"
    return CheckResult(
        name="krita_bridge",
        severity=severity,
        ok=False,
        detail=detail,
        hint=hint,
        raw=result,
    )


def check_ai_diffusion(client: JsonEndpointClient) -> CheckResult:
    """Check AI Diffusion availability via Krita bridge."""
    result = client.get_json("/api/status")
    if not result.ok:
        return CheckResult(
            name="ai_diffusion",
            severity=Severity.RECOVERABLE,
            ok=False,
            detail="Cannot determine AI Diffusion status (bridge unreachable)",
            hint="Resolve Krita bridge first.",
            raw=result,
        )
    data = result.data if isinstance(result.data, dict) else {}
    ai_available = data.get("ai_diffusion_available", False)
    if ai_available:
        return CheckResult(
            name="ai_diffusion",
            severity=Severity.OK,
            ok=True,
            detail="AI Diffusion plugin is active",
            raw=result,
        )
    return CheckResult(
        name="ai_diffusion",
        severity=Severity.RECOVERABLE,
        ok=False,
        detail="AI Diffusion plugin not detected",
        hint="Install or enable the AI Diffusion plugin in Krita.",
        raw=result,
    )


def check_comfyui(client: JsonEndpointClient) -> CheckResult:
    """Check ComfyUI availability via /object_info."""
    result = client.get_json("/object_info")
    if result.ok:
        node_count = len(result.data) if isinstance(result.data, dict) else 0
        return CheckResult(
            name="comfyui",
            severity=Severity.OK,
            ok=True,
            detail=f"ComfyUI is responding ({node_count} nodes available)",
            raw=result,
        )
    severity = Severity.RECOVERABLE
    hint = "Start ComfyUI: python main.py --listen 127.0.0.1"
    detail = f"ComfyUI unreachable: {result.error}"
    return CheckResult(
        name="comfyui",
        severity=severity,
        ok=False,
        detail=detail,
        hint=hint,
        raw=result,
    )


def check_port(host: str, port: int, label: str) -> CheckResult:
    """Check if a TCP port is open."""
    if _port_open(host, port):
        return CheckResult(
            name=f"port_{port}",
            severity=Severity.OK,
            ok=True,
            detail=f"Port {port} ({label}) is open",
        )
    return CheckResult(
        name=f"port_{port}",
        severity=Severity.RECOVERABLE,
        ok=False,
        detail=f"Port {port} ({label}) is not responding",
        hint=f"Ensure the {label} service is running and listening on {host}:{port}.",
    )


def run_doctor(
    krita_api: str = "http://127.0.0.1:8900",
    comfyui_api: str = "http://127.0.0.1:8188",
    timeout: float = 3.0,
    check_ports: bool = True,
) -> DoctorReport:
    """Run all diagnostic checks and return a DoctorReport."""
    report = DoctorReport()

    # Port checks (skip when using non-default ports, e.g. in tests)
    if check_ports:
        report.checks.append(check_port("127.0.0.1", 8900, "Krita bridge"))
        report.checks.append(check_port("127.0.0.1", 8188, "ComfyUI"))

    # API checks
    krita_client = JsonEndpointClient(krita_api, timeout=timeout)
    comfy_client = JsonEndpointClient(comfyui_api, timeout=timeout)

    report.checks.append(check_krita_bridge(krita_client))
    report.checks.append(check_ai_diffusion(krita_client))
    report.checks.append(check_comfyui(comfy_client))

    return report


def format_summary(report: DoctorReport) -> str:
    """Format a human-readable summary from the report."""
    lines: list[str] = []

    severity_icon = {
        Severity.OK: "✓",
        Severity.RECOVERABLE: "⚠",
        Severity.FATAL: "✗",
    }

    for check in report.checks:
        icon = severity_icon[check.severity]
        lines.append(f"  {icon} {check.name}: {check.detail}")
        if check.hint:
            lines.append(f"      Hint: {check.hint}")

    header_map = {
        Severity.OK: "All checks passed.",
        Severity.RECOVERABLE: "Some issues detected (recoverable).",
        Severity.FATAL: "Fatal issues detected.",
    }
    header = header_map[report.worst_severity]

    return f"{header}\n\n{chr(10).join(lines)}\n"
