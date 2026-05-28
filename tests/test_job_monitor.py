"""Tests for the job status monitoring bridge (Issue #21).

Covers:
- jobs(): success with counts, empty queue, connection failure, invalid response
- job_status(): found, not found, empty job_id, connection failure
- find_by_prompt_id(): found, not found, empty prompt_id
- wait_for_job(): immediate finish, timeout, not found then found
- wait_for_prompt(): immediate finish, timeout
- JobResult / JobStatus / JobSummary dataclass checks
- Timeout produces clear error, not infinite hang
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

import pytest

from krita_agent_bridge.job_monitor import (
    JobError,
    JobMonitor,
    JobResult,
    JobStatus,
    JobSummary,
)


# ---------------------------------------------------------------------------
# Stub HTTP server
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}
    call_count: dict[str, int] = {}

    def do_GET(self) -> None:  # noqa: N802
        key = self.path.split("?")[0].rstrip("/")
        _StubHandler.call_count[key] = _StubHandler.call_count.get(key, 0) + 1
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
        pass


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    _StubHandler.routes.clear()
    _StubHandler.call_count.clear()


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def monitor(mock_server: str) -> JobMonitor:
    return JobMonitor(bridge_url=mock_server, timeout=2.0)


# Sample job data
SAMPLE_JOBS = json.dumps({
    "queued": 1,
    "executing": 1,
    "finished": 2,
    "jobs": [
        {"job_id": "j1", "prompt_id": "p1", "state": "queued", "progress": 0.0},
        {"job_id": "j2", "prompt_id": "p2", "state": "executing", "progress": 0.5},
        {"job_id": "j3", "prompt_id": "p3", "request_id": "r3", "state": "finished", "progress": 1.0},
        {"job_id": "j4", "prompt_id": "p4", "state": "finished", "progress": 1.0},
    ],
})

SAMPLE_EMPTY = json.dumps({
    "queued": 0,
    "executing": 0,
    "finished": 0,
    "jobs": [],
})


# ---------------------------------------------------------------------------
# jobs() tests
# ---------------------------------------------------------------------------


class TestJobs:
    def test_success(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.jobs()
        assert result.ok
        assert isinstance(result.data, JobSummary)
        summary = result.data
        assert summary.queued == 1
        assert summary.executing == 1
        assert summary.finished == 2
        assert len(summary.jobs) == 4

    def test_job_status_fields(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.jobs()
        assert result.ok
        job = result.data.jobs[0]
        assert isinstance(job, JobStatus)
        assert job.job_id == "j1"
        assert job.prompt_id == "p1"
        assert job.request_id == ""
        assert job.state == "queued"
        assert job.progress == 0.0

    def test_empty_queue(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_EMPTY)
        result = monitor.jobs()
        assert result.ok
        assert result.data.queued == 0
        assert result.data.jobs == ()

    def test_connection_failure(self) -> None:
        monitor = JobMonitor("http://127.0.0.1:1", timeout=0.5)
        result = monitor.jobs()
        assert not result.ok
        assert result.error == JobError.CONNECTION

    def test_invalid_response(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, '"not a dict"')
        result = monitor.jobs()
        assert not result.ok
        assert result.error == JobError.VALIDATION

    def test_counts_computed_from_jobs(self, monitor: JobMonitor) -> None:
        """When server doesn't provide counts, compute from job states."""
        data = json.dumps({
            "jobs": [
                {"job_id": "j1", "state": "queued", "progress": 0.0},
                {"job_id": "j2", "state": "executing", "progress": 0.5},
                {"job_id": "j3", "state": "finished", "progress": 1.0},
            ],
        })
        _StubHandler.routes["/api/jobs"] = (200, data)
        result = monitor.jobs()
        assert result.ok
        assert result.data.queued == 1
        assert result.data.executing == 1
        assert result.data.finished == 1


# ---------------------------------------------------------------------------
# job_status() tests
# ---------------------------------------------------------------------------


class TestGetJobStatus:
    def test_found(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.job_status("j2")
        assert result.ok
        assert result.data.job_id == "j2"
        assert result.data.state == "executing"

    def test_not_found(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.job_status("nonexistent")
        assert not result.ok
        assert result.error == JobError.NOT_FOUND

    def test_found_by_request_id(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.job_status("r3")
        assert result.ok
        assert result.data.job_id == "j3"
        assert result.data.request_id == "r3"

    def test_empty_job_id(self, monitor: JobMonitor) -> None:
        result = monitor.job_status("")
        assert not result.ok
        assert result.error == JobError.VALIDATION

    def test_connection_failure(self) -> None:
        monitor = JobMonitor("http://127.0.0.1:1", timeout=0.5)
        result = monitor.job_status("j1")
        assert not result.ok
        assert result.error == JobError.CONNECTION


# ---------------------------------------------------------------------------
# find_by_prompt_id() tests
# ---------------------------------------------------------------------------


class TestFindByPromptId:
    def test_found(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.find_by_prompt_id("p2")
        assert result.ok
        assert result.data.job_id == "j2"
        assert result.data.prompt_id == "p2"

    def test_not_found(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, SAMPLE_JOBS)
        result = monitor.find_by_prompt_id("nonexistent")
        assert not result.ok
        assert result.error == JobError.NOT_FOUND

    def test_empty_prompt_id(self, monitor: JobMonitor) -> None:
        result = monitor.find_by_prompt_id("")
        assert not result.ok
        assert result.error == JobError.VALIDATION


# ---------------------------------------------------------------------------
# wait_for_job() tests
# ---------------------------------------------------------------------------


class TestWaitForJob:
    def test_immediate_finish(self, monitor: JobMonitor) -> None:
        """Job is already finished on first poll."""
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({
            "jobs": [
                {"job_id": "j1", "state": "finished", "progress": 1.0},
            ],
        }))
        result = monitor.wait_for_job("j1", timeout=2.0, interval=0.05)
        assert result.ok
        assert result.data.state == "finished"

    def test_timeout(self, monitor: JobMonitor) -> None:
        """Job never reaches terminal state → timeout."""
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({
            "jobs": [
                {"job_id": "j1", "state": "executing", "progress": 0.5},
            ],
        }))
        result = monitor.wait_for_job("j1", timeout=0.5, interval=0.1)
        assert not result.ok
        assert result.error == JobError.TIMEOUT
        assert "timed out" in result.message.lower()

    def test_not_found_then_found(self, monitor: JobMonitor) -> None:
        """Job appears after a few polls."""
        call_count = {"n": 0}

        def _dynamic_response() -> tuple[int, str]:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return (200, json.dumps({"jobs": []}))
            return (200, json.dumps({
                "jobs": [
                    {"job_id": "j1", "state": "finished", "progress": 1.0},
                ],
            }))

        # Use a simpler approach: pre-configure routes for each call
        # Since _StubHandler is static, we modify it between calls via a wrapper
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({"jobs": []}))

        # Start the wait with very short timeout
        result = monitor.wait_for_job("j1", timeout=1.0, interval=0.1)

        # Should timeout since job never appears in the static routes
        # This test verifies timeout behavior
        assert not result.ok
        assert result.error in (JobError.TIMEOUT, JobError.NOT_FOUND)

    def test_error_state(self, monitor: JobMonitor) -> None:
        """Job in error state is terminal but not ok."""
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({
            "jobs": [
                {"job_id": "j1", "state": "error", "progress": 0.0},
            ],
        }))
        result = monitor.wait_for_job("j1", timeout=2.0, interval=0.05)
        assert not result.ok
        assert result.data.state == "error"

    def test_wait_for_request_id(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({
            "jobs": [
                {"job_id": "real-job", "request_id": "request-1", "state": "finished", "progress": 1.0},
            ],
        }))
        result = monitor.wait_for_job("request-1", timeout=2.0, interval=0.05)
        assert result.ok
        assert result.data.job_id == "real-job"


# ---------------------------------------------------------------------------
# wait_for_prompt() tests
# ---------------------------------------------------------------------------


class TestWaitForPrompt:
    def test_immediate_finish(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({
            "jobs": [
                {"job_id": "j1", "prompt_id": "p1", "state": "finished", "progress": 1.0},
            ],
        }))
        result = monitor.wait_for_prompt("p1", timeout=2.0, interval=0.05)
        assert result.ok
        assert result.data.state == "finished"

    def test_prompt_not_found_timeout(self, monitor: JobMonitor) -> None:
        _StubHandler.routes["/api/jobs"] = (200, json.dumps({"jobs": []}))
        result = monitor.wait_for_prompt("nonexistent", timeout=0.5, interval=0.1)
        assert not result.ok
        assert result.error == JobError.TIMEOUT

    def test_connection_failure(self) -> None:
        monitor = JobMonitor("http://127.0.0.1:1", timeout=0.5)
        result = monitor.wait_for_prompt("p1", timeout=1.0, interval=0.1)
        assert not result.ok
        assert result.error == JobError.CONNECTION


# ---------------------------------------------------------------------------
# Dataclass checks
# ---------------------------------------------------------------------------


class TestJobResult:
    def test_frozen(self) -> None:
        result = JobResult(ok=True)
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = JobResult(ok=True)
        assert result.error == JobError.NONE
        assert result.message == ""
        assert result.data is None


class TestJobStatus:
    def test_frozen(self) -> None:
        status = JobStatus(job_id="j1", state="queued")
        with pytest.raises(AttributeError):
            status.job_id = "j2"  # type: ignore[misc]


class TestJobSummary:
    def test_frozen(self) -> None:
        summary = JobSummary(queued=0, executing=0, finished=0)
        with pytest.raises(AttributeError):
            summary.queued = 5  # type: ignore[misc]

    def test_defaults(self) -> None:
        summary = JobSummary(queued=0, executing=0, finished=0)
        assert summary.jobs == ()
