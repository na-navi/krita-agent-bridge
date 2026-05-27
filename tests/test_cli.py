"""Tests for krita-agent-bridge CLI and client layer.

Covers Issue #2 and Issue #7 acceptance criteria:
- CLI runs without third-party dependencies
- Detects Krita bridge status
- Detects ComfyUI object info availability
- Errors are readable by both humans and agents
- Report is JSON plus readable summary
- Exit code distinguishes OK (0), recoverable (1), fatal (2)
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.cli import build_parser, main
from krita_agent_bridge.client import EndpointResult, JsonEndpointClient
from krita_agent_bridge.doctor import (
    DoctorReport,
    CheckResult,
    Severity,
    check_krita_bridge,
    check_ai_diffusion,
    check_comfyui,
    format_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """Configurable request handler for integration-style tests."""

    # Class-level config set per test
    routes: dict[str, tuple[int, str]] = {}

    def do_GET(self) -> None:  # noqa: N802
        key = self.path.rstrip("/")
        if key in self.routes:
            status, body = self.routes[key]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def log_message(self, format: Any, *args: Any) -> None:  # noqa: A002
        pass  # silence request logs


@pytest.fixture()
def mock_server():
    """Spin up a local HTTP server on an ephemeral port, yield its base_url."""
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# EndpointResult / JsonEndpointClient unit tests (Issue #2)
# ---------------------------------------------------------------------------


class TestEndpointResult:
    def test_frozen_dataclass(self) -> None:
        r = EndpointResult(True, "http://x", 200, {"a": 1})
        assert r.ok is True
        with pytest.raises(AttributeError):
            r.ok = False  # type: ignore[misc]

    def test_error_result(self) -> None:
        r = EndpointResult(False, "http://x", None, None, "Connection refused")
        assert r.ok is False
        assert r.status is None
        assert r.error == "Connection refused"


class TestJsonEndpointClient:
    def test_success(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (200, '{"running":true}')
        client = JsonEndpointClient(mock_server, timeout=2)
        result = client.get_json("/api/status")
        assert result.ok
        assert result.status == 200
        assert result.data == {"running": True}
        assert result.error is None

    def test_http_error(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (500, '{"error":"boom"}')
        client = JsonEndpointClient(mock_server, timeout=2)
        result = client.get_json("/api/status")
        assert not result.ok
        assert result.status == 500
        assert result.error is not None

    def test_connection_refused(self) -> None:
        client = JsonEndpointClient("http://127.0.0.1:1", timeout=0.5)
        result = client.get_json("/api/status")
        assert not result.ok
        assert result.status is None
        assert result.error is not None
        # error must be human-readable
        assert len(result.error) > 0

    def test_path_join_strips_slashes(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (200, '{}')
        client = JsonEndpointClient(mock_server + "/", timeout=2)
        result = client.get_json("/api/status")
        assert result.ok
        assert result.url == f"{mock_server}/api/status"


# ---------------------------------------------------------------------------
# Parser tests (Issue #2)
# ---------------------------------------------------------------------------


class TestParser:
    def test_parser_accepts_status(self) -> None:
        args = build_parser().parse_args(["status"])
        assert args.command == "status"

    def test_parser_accepts_doctor(self) -> None:
        args = build_parser().parse_args(["doctor"])
        assert args.command == "doctor"

    def test_doctor_json_flag(self) -> None:
        args = build_parser().parse_args(["doctor", "--json"])
        assert args.json is True

    def test_default_krita_api(self) -> None:
        args = build_parser().parse_args(["status"])
        assert args.krita_api == "http://127.0.0.1:8900"

    def test_default_comfyui_api(self) -> None:
        args = build_parser().parse_args(["doctor"])
        assert args.comfyui_api == "http://127.0.0.1:8188"

    def test_custom_api_urls(self) -> None:
        args = build_parser().parse_args([
            "--krita-api", "http://localhost:9999",
            "--comfyui-api", "http://localhost:8888",
            "doctor",
        ])
        assert args.krita_api == "http://localhost:9999"
        assert args.comfyui_api == "http://localhost:8888"


# ---------------------------------------------------------------------------
# command_status tests (Issue #2)
# ---------------------------------------------------------------------------


class TestCommandStatus:
    def test_status_ok(self, mock_server: str, capsys: pytest.CaptureFixture[str]) -> None:
        _StubHandler.routes["/api/status"] = (200, '{"running":true}')
        exit_code = main(["--krita-api", mock_server, "status"])
        assert exit_code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["krita_api"]["ok"] is True
        assert output["krita_api"]["data"]["running"] is True

    def test_status_connection_refused(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main(["--krita-api", "http://127.0.0.1:1", "status"])
        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["krita_api"]["ok"] is False
        assert output["krita_api"]["error"] is not None


# ---------------------------------------------------------------------------
# Individual doctor checks (Issue #7)
# ---------------------------------------------------------------------------


class TestDoctorChecks:
    def test_krita_bridge_ok(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (200, '{"running":true}')
        client = JsonEndpointClient(mock_server, timeout=2)
        check = check_krita_bridge(client)
        assert check.ok
        assert check.severity == Severity.OK

    def test_krita_bridge_down(self) -> None:
        client = JsonEndpointClient("http://127.0.0.1:1", timeout=0.5)
        check = check_krita_bridge(client)
        assert not check.ok
        assert check.severity == Severity.FATAL
        assert check.hint != ""

    def test_ai_diffusion_available(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (
            200, '{"running":true,"ai_diffusion_available":true}'
        )
        client = JsonEndpointClient(mock_server, timeout=2)
        check = check_ai_diffusion(client)
        assert check.ok
        assert check.severity == Severity.OK

    def test_ai_diffusion_not_available(self, mock_server: str) -> None:
        _StubHandler.routes["/api/status"] = (200, '{"running":true}')
        client = JsonEndpointClient(mock_server, timeout=2)
        check = check_ai_diffusion(client)
        assert not check.ok
        assert check.severity == Severity.RECOVERABLE
        assert "AI Diffusion" in check.hint

    def test_ai_diffusion_bridge_down(self) -> None:
        client = JsonEndpointClient("http://127.0.0.1:1", timeout=0.5)
        check = check_ai_diffusion(client)
        assert not check.ok
        assert check.severity == Severity.RECOVERABLE

    def test_comfyui_ok(self, mock_server: str) -> None:
        _StubHandler.routes["/object_info"] = (200, '{"KSampler":{},"CLIPTextEncode":{}}')
        client = JsonEndpointClient(mock_server, timeout=2)
        check = check_comfyui(client)
        assert check.ok
        assert check.severity == Severity.OK
        assert "2 nodes" in check.detail

    def test_comfyui_down(self) -> None:
        client = JsonEndpointClient("http://127.0.0.1:1", timeout=0.5)
        check = check_comfyui(client)
        assert not check.ok
        assert check.severity == Severity.RECOVERABLE
        assert check.hint != ""


# ---------------------------------------------------------------------------
# DoctorReport and exit code tests (Issue #7)
# ---------------------------------------------------------------------------


class TestDoctorReport:
    def test_all_ok_exit_code_0(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.OK, True, "ok"),
            CheckResult("b", Severity.OK, True, "ok"),
        ])
        assert report.exit_code == 0
        assert report.worst_severity == Severity.OK

    def test_recoverable_exit_code_1(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.OK, True, "ok"),
            CheckResult("b", Severity.RECOVERABLE, False, "issue"),
        ])
        assert report.exit_code == 1
        assert report.worst_severity == Severity.RECOVERABLE

    def test_fatal_exit_code_2(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.OK, True, "ok"),
            CheckResult("b", Severity.FATAL, False, "bad"),
        ])
        assert report.exit_code == 2
        assert report.worst_severity == Severity.FATAL

    def test_to_dict_structure(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("test", Severity.OK, True, "detail", "hint"),
        ])
        d = report.to_dict()
        assert d["exit_code"] == 0
        assert d["severity"] == "ok"
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "test"


# ---------------------------------------------------------------------------
# Format summary tests (Issue #7)
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def test_all_ok_header(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.OK, True, "ok"),
        ])
        summary = format_summary(report)
        assert "All checks passed" in summary
        assert "✓" in summary

    def test_recoverable_header(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.RECOVERABLE, False, "issue", "fix it"),
        ])
        summary = format_summary(report)
        assert "recoverable" in summary
        assert "⚠" in summary
        assert "Hint: fix it" in summary

    def test_fatal_header(self) -> None:
        report = DoctorReport(checks=[
            CheckResult("a", Severity.FATAL, False, "bad"),
        ])
        summary = format_summary(report)
        assert "Fatal" in summary
        assert "✗" in summary


# ---------------------------------------------------------------------------
# command_doctor integration tests (Issue #7)
# ---------------------------------------------------------------------------


class TestCommandDoctor:
    def test_all_ok(
        self, mock_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _StubHandler.routes["/api/status"] = (
            200, '{"running":true,"ai_diffusion_available":true}'
        )
        _StubHandler.routes["/object_info"] = (200, '{"KSampler":{}}')
        exit_code = main([
            "--krita-api", mock_server,
            "--comfyui-api", mock_server,
            "doctor", "--json",
        ])
        assert exit_code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["doctor"]["exit_code"] == 0
        assert output["doctor"]["severity"] == "ok"

    def test_krita_down(
        self, mock_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _StubHandler.routes["/object_info"] = (200, '{}')
        exit_code = main([
            "--krita-api", "http://127.0.0.1:1",
            "--comfyui-api", mock_server,
            "doctor", "--json",
        ])
        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["doctor"]["severity"] == "fatal"
        checks = {c["name"]: c for c in output["doctor"]["checks"]}
        assert checks["krita_bridge"]["ok"] is False

    def test_comfyui_down(
        self, mock_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _StubHandler.routes["/api/status"] = (
            200, '{"running":true,"ai_diffusion_available":true}'
        )
        exit_code = main([
            "--krita-api", mock_server,
            "--comfyui-api", "http://127.0.0.1:1",
            "doctor", "--json",
        ])
        assert exit_code == 1
        output = json.loads(capsys.readouterr().out)
        assert output["doctor"]["severity"] == "recoverable"
        checks = {c["name"]: c for c in output["doctor"]["checks"]}
        assert checks["comfyui"]["ok"] is False
        assert checks["krita_bridge"]["ok"] is True

    def test_both_down(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main([
            "--krita-api", "http://127.0.0.1:1",
            "--comfyui-api", "http://127.0.0.1:1",
            "doctor", "--json",
        ])
        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["doctor"]["severity"] == "fatal"

    def test_human_readable_default(
        self, mock_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _StubHandler.routes["/api/status"] = (
            200, '{"running":true,"ai_diffusion_available":true}'
        )
        _StubHandler.routes["/object_info"] = (200, '{"KSampler":{}}')
        exit_code = main([
            "--krita-api", mock_server,
            "--comfyui-api", mock_server,
            "doctor",
        ])
        assert exit_code == 0
        stdout = capsys.readouterr().out
        assert "All checks passed" in stdout
        assert "✓" in stdout

    def test_human_readable_with_issue(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main([
            "--krita-api", "http://127.0.0.1:1",
            "--comfyui-api", "http://127.0.0.1:1",
            "doctor",
        ])
        assert exit_code == 2
        stdout = capsys.readouterr().out
        assert "Fatal" in stdout
        assert "Hint:" in stdout
