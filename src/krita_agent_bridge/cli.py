from __future__ import annotations

import argparse
import json

from .client import JsonEndpointClient

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
    krita = JsonEndpointClient(args.krita_api).get_json("/api/status")
    comfy = JsonEndpointClient(args.comfyui_api).get_json("/object_info")
    report = {
        "krita_api": krita,
        "comfyui_api": {
            "ok": comfy.ok,
            "url": comfy.url,
            "status": comfy.status,
            "node_count": len(comfy.data) if isinstance(comfy.data, dict) else None,
            "error": comfy.error,
        },
    }
    _print_result("doctor", report)
    return 0 if krita.ok and comfy.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="krita-agent")
    parser.add_argument("--krita-api", default=DEFAULT_KRITA_API)
    parser.add_argument("--comfyui-api", default=DEFAULT_COMFYUI_API)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Check the local Krita bridge status")
    status.set_defaults(func=command_status)

    doctor = sub.add_parser("doctor", help="Run basic local diagnostics")
    doctor.set_defaults(func=command_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
