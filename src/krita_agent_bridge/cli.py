from __future__ import annotations

import argparse
import json
from .client import JsonEndpointClient
from .doctor import run_doctor, format_summary
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
            timeout=args.timeout,
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

    ready = sub.add_parser("ready", help="Check whether Krita + AI Diffusion + ComfyUI are generation-ready")
    ready.add_argument("--json", action="store_true", help="Output readiness as JSON")
    ready.add_argument("--wait", action="store_true", help="Poll until ready or timeout")
    ready.add_argument("--timeout", type=float, default=120.0, help="Maximum wait time when --wait is set")
    ready.add_argument("--interval", type=float, default=1.0, help="Polling interval when --wait is set")
    ready.add_argument("--request-timeout", type=float, default=3.0, help="Per-request timeout")
    ready.add_argument("--no-document", action="store_true", help="Do not require an active Krita document")
    ready.set_defaults(func=command_ready)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
