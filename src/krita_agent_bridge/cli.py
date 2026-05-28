from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bootstrap import bootstrap_test_mode
from .client import JsonEndpointClient
from .doctor import run_doctor, format_summary
from .e2e_smoke import run_smoke_workflow
from .polling_policy import MAX_POLL_SECONDS, clamp_poll_timeout
from .readiness import ReadinessProbe

DEFAULT_KRITA_API = "http://127.0.0.1:8900"
DEFAULT_COMFYUI_API = "http://127.0.0.1:8188"


def _to_jsonable(value: object) -> object:
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _print_result(name: str, result: object) -> None:
    print(json.dumps({name: result}, ensure_ascii=False, indent=2, default=_to_jsonable))


def command_status(args: argparse.Namespace) -> int:
    client = JsonEndpointClient(args.krita_api)
    result = client.get_json("/api/status")
    _print_result("krita_api", result)
    return 0 if result.ok else 2


def command_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(
        krita_api=args.krita_api,
        comfyui_api=args.comfyui_api,
        check_ports=(
            args.krita_api == DEFAULT_KRITA_API
            and args.comfyui_api == DEFAULT_COMFYUI_API
        ),
    )
    if args.json:
        print(json.dumps({"doctor": report.to_dict()}, ensure_ascii=False, indent=2))
    else:
        print(format_summary(report))
    return report.exit_code


def command_ready(args: argparse.Namespace) -> int:
    probe = ReadinessProbe(
        krita_api=args.krita_api,
        comfyui_api=args.comfyui_api,
        timeout=args.request_timeout,
    )
    if args.wait:
        report = probe.wait(
            timeout=clamp_poll_timeout(args.timeout, label="ready"),
            interval=args.interval,
            require_document=not args.no_document,
        )
    else:
        report = probe.check(require_document=not args.no_document)

    if args.json:
        print(json.dumps({"readiness": report.to_dict()}, ensure_ascii=False, indent=2))
    else:
        state = "ready" if report.ready else "not ready"
        print(f"Generation readiness: {state}")
        for check in report.checks:
            marker = "OK" if check.ok else "NG"
            print(f"  {marker} {check.name}: {check.detail}")
    return 0 if report.ready else 1


def command_smoke(args: argparse.Namespace) -> int:
    result = run_smoke_workflow(
        krita_api=args.krita_api,
        comfyui_api=args.comfyui_api,
        report_path=args.report,
        output_path=args.output,
        document_name=args.document_name,
        positive=args.positive,
        seed=args.seed,
        checkpoint=args.checkpoint,
        width=args.width,
        height=args.height,
        timeout=clamp_poll_timeout(args.timeout, label="smoke"),
        request_timeout=args.request_timeout,
        interval=args.interval,
    )
    if args.json:
        print(json.dumps({"smoke": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        state = "passed" if result.ok else "failed"
        print(f"E2E smoke {state}: {result.message}")
        print(f"  report: {Path(result.report_path)}")
        print(f"  output: {Path(result.output_path)}")
    return 0 if result.ok else 1


def command_bootstrap(args: argparse.Namespace) -> int:
    result = bootstrap_test_mode(
        krita_exe=args.krita_exe,
        krita_api=args.krita_api,
        comfyui_api=args.comfyui_api,
        document_name=args.document_name,
        width=args.width,
        height=args.height,
        timeout=clamp_poll_timeout(args.timeout, label="bootstrap"),
        interval=args.interval,
        request_timeout=args.request_timeout,
        create_document=not args.no_document,
    )
    if args.json:
        print(json.dumps({"bootstrap": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        state = "ready" if result.ok else "not ready"
        print(f"Krita test bootstrap {state}: {result.message}")
        print(f"  started_krita: {result.started_krita}")
        print(f"  document_created: {result.document_created}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="krita-agent",
        description="External automation bridge for agent-driven Krita workflows",
    )
    parser.add_argument("--krita-api", default=DEFAULT_KRITA_API)
    parser.add_argument("--comfyui-api", default=DEFAULT_COMFYUI_API)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Check the local Krita bridge status")
    status.set_defaults(func=command_status)

    doctor = sub.add_parser("doctor", help="Run basic local diagnostics")
    doctor.add_argument("--json", action="store_true", help="Output full report as JSON")
    doctor.set_defaults(func=command_doctor)

    ready = sub.add_parser(
        "ready",
        help="Check whether Krita + AI Diffusion + ComfyUI are generation-ready",
    )
    ready.add_argument("--json", action="store_true", help="Output readiness as JSON")
    ready.add_argument("--wait", action="store_true", help="Poll until ready or timeout")
    ready.add_argument(
        "--timeout",
        type=float,
        default=MAX_POLL_SECONDS,
        help=(
            f"Maximum wait time when --wait is set. Capped at {MAX_POLL_SECONDS:.0f}s "
            "(2-min SLO); set KRITA_AGENT_ALLOW_LONG_POLL=1 to bypass."
        ),
    )
    ready.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval when --wait is set",
    )
    ready.add_argument("--request-timeout", type=float, default=3.0, help="Per-request timeout")
    ready.add_argument(
        "--no-document",
        action="store_true",
        help="Do not require an active Krita document",
    )
    ready.set_defaults(func=command_ready)

    smoke = sub.add_parser("smoke", help="Run the Krita + shim + ComfyUI E2E smoke workflow")
    smoke.add_argument("--json", action="store_true", help="Output smoke report summary as JSON")
    smoke.add_argument(
        "--report",
        default="smoke_report.json",
        help="Path for the machine-readable report",
    )
    smoke.add_argument(
        "--output",
        default="smoke_output.png",
        help="Path for the exported smoke PNG",
    )
    smoke.add_argument("--document-name", default="smoke", help="Temporary Krita document name")
    smoke.add_argument(
        "--positive",
        default="1girl, test",
        help="Positive prompt for the smoke generation",
    )
    smoke.add_argument("--seed", type=int, default=42, help="Generation seed")
    smoke.add_argument(
        "--checkpoint",
        default=None,
        help="ComfyUI checkpoint name; auto-detected when omitted",
    )
    smoke.add_argument("--width", type=int, default=1024, help="Smoke document and latent width")
    smoke.add_argument("--height", type=int, default=1024, help="Smoke document and latent height")
    smoke.add_argument(
        "--timeout",
        type=float,
        default=MAX_POLL_SECONDS,
        help=(
            "Maximum wait time for readiness and job completion. "
            f"Capped at {MAX_POLL_SECONDS:.0f}s (2-min SLO); "
            "set KRITA_AGENT_ALLOW_LONG_POLL=1 to bypass."
        ),
    )
    smoke.add_argument("--interval", type=float, default=1.0, help="Polling interval")
    smoke.add_argument("--request-timeout", type=float, default=10.0, help="Per-request timeout")
    smoke.set_defaults(func=command_smoke)

    bootstrap = sub.add_parser(
        "bootstrap",
        help="Start Krita if needed, create a blank document, and wait for test readiness",
    )
    bootstrap.add_argument("--json", action="store_true", help="Output bootstrap result as JSON")
    bootstrap.add_argument(
        "--krita-exe",
        default=r"C:\Program Files\Krita (x64)\bin\krita.exe",
        help="Path to krita.exe",
    )
    bootstrap.add_argument("--document-name", default="smoke-bootstrap")
    bootstrap.add_argument("--width", type=int, default=1024)
    bootstrap.add_argument("--height", type=int, default=1024)
    bootstrap.add_argument(
        "--timeout",
        type=float,
        default=MAX_POLL_SECONDS,
        help=(
            f"Maximum wait time. Capped at {MAX_POLL_SECONDS:.0f}s (2-min SLO); "
            "set KRITA_AGENT_ALLOW_LONG_POLL=1 to bypass."
        ),
    )
    bootstrap.add_argument("--interval", type=float, default=1.0)
    bootstrap.add_argument("--request-timeout", type=float, default=3.0)
    bootstrap.add_argument(
        "--no-document",
        action="store_true",
        help="Only start Krita/shim and ComfyUI readiness; do not create a document",
    )
    bootstrap.set_defaults(func=command_bootstrap)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
