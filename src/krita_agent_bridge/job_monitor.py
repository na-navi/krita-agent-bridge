"""Job status monitoring bridge for krita-agent-bridge.

Issue #21: Bridge Krita AI Diffusion job queue state with ComfyUI
execution state so agents can monitor generation progress through
a single interface.

Provides:
- Job status query via Krita bridge /api/jobs
- prompt_id → job_id mapping
- Unified progress: queued → executing → finished
- Polling with timeout and exponential back-off

Design:
- All methods return JobResult with clear error classification
- Timeout produces a clear error, never an infinite hang
- No new runtime dependencies
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .client import JsonEndpointClient


class JobError(Enum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    NONE = "none"


@dataclass(frozen=True)
class JobResult:
    """Result from a job monitoring operation."""

    ok: bool
    error: JobError = JobError.NONE
    message: str = ""
    data: Any = None


@dataclass(frozen=True)
class JobStatus:
    """Status of a single generation job."""

    job_id: str
    state: str  # queued, executing, finished, error
    prompt_id: str = ""
    request_id: str = ""
    progress: float = 0.0  # 0.0–1.0


@dataclass(frozen=True)
class JobSummary:
    """Summary of all jobs in the queue."""

    queued: int
    executing: int
    finished: int
    jobs: tuple[JobStatus, ...] = ()


class JobMonitor:
    """Monitor generation jobs across Krita bridge and ComfyUI.

    Single interface for agents to answer "is generation done?"
    without querying two APIs separately.
    """

    def __init__(
        self,
        bridge_url: str = "http://127.0.0.1:8900",
        timeout: float = 10.0,
    ) -> None:
        self.client = JsonEndpointClient(bridge_url, timeout=timeout)

    # -----------------------------------------------------------------------
    # Job list / summary
    # -----------------------------------------------------------------------

    def jobs(self) -> JobResult:
        """Get current job queue summary from /api/jobs.

        Returns JobSummary with counts and individual job statuses.
        """
        result = self.client.get_json("/api/jobs")

        if not result.ok:
            return JobResult(
                ok=False,
                error=JobError.CONNECTION,
                message=self._connection_message(result.error),
            )

        data = result.data
        if not isinstance(data, dict):
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="Unexpected response format from /api/jobs",
            )

        try:
            job_list = data.get("jobs", [])
            job_statuses = tuple(
                JobStatus(
                    job_id=str(j["job_id"]),
                    prompt_id=str(j.get("prompt_id", "")),
                    request_id=str(j.get("request_id", "")),
                    state=str(j.get("state", "unknown")),
                    progress=float(j.get("progress", 0.0)),
                )
                for j in job_list
                if isinstance(j, dict) and "job_id" in j
            )

            summary = JobSummary(
                queued=int(data.get("queued", sum(1 for j in job_statuses if j.state == "queued"))),
                executing=int(data.get("executing", sum(1 for j in job_statuses if j.state == "executing"))),
                finished=int(data.get("finished", sum(1 for j in job_statuses if j.state == "finished"))),
                jobs=job_statuses,
            )
        except (KeyError, ValueError, TypeError) as exc:
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message=f"Invalid job data from /api/jobs: {exc}",
            )

        return JobResult(ok=True, data=summary)

    # -----------------------------------------------------------------------
    # Single job lookup
    # -----------------------------------------------------------------------

    def job_status(self, job_id: str) -> JobResult:
        """Get the status of a specific job.

        Searches the job list for the given job_id.
        """
        if not job_id:
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="job_id must not be empty",
            )

        jobs_result = self.jobs()
        if not jobs_result.ok:
            return jobs_result

        summary = jobs_result.data
        if not isinstance(summary, JobSummary):
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="Unexpected data type from jobs()",
            )

        for job in summary.jobs:
            if job.job_id == job_id or job.request_id == job_id:
                return JobResult(
                    ok=True,
                    data=job,
                    message=f"Job {job_id}: {job.state}",
                )

        return JobResult(
            ok=False,
            error=JobError.NOT_FOUND,
            message=f"Job '{job_id}' not found in current queue",
        )

    # -----------------------------------------------------------------------
    # prompt_id → job_id mapping
    # -----------------------------------------------------------------------

    def find_by_prompt_id(self, prompt_id: str) -> JobResult:
        """Find a job by its ComfyUI prompt_id.

        Maps ComfyUI prompt_id to Krita job_id.
        """
        if not prompt_id:
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="prompt_id must not be empty",
            )

        jobs_result = self.jobs()
        if not jobs_result.ok:
            return jobs_result

        summary = jobs_result.data
        if not isinstance(summary, JobSummary):
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="Unexpected data type from jobs()",
            )

        for job in summary.jobs:
            if job.prompt_id == prompt_id:
                return JobResult(
                    ok=True,
                    data=job,
                    message=f"Found job {job.job_id} for prompt {prompt_id}",
                )

        return JobResult(
            ok=False,
            error=JobError.NOT_FOUND,
            message=f"No job found for prompt_id '{prompt_id}'",
        )

    # -----------------------------------------------------------------------
    # Polling with timeout
    # -----------------------------------------------------------------------

    def wait_for_job(
        self,
        job_id: str,
        timeout: float = 120.0,
        interval: float = 1.0,
        backoff: float = 1.5,
        max_interval: float = 5.0,
    ) -> JobResult:
        """Poll until a job reaches a terminal state (finished/error) or times out.

        Args:
            job_id: The job to wait for.
            timeout: Maximum time to wait in seconds.
            interval: Initial polling interval in seconds.
            backoff: Multiplier for interval after each poll.
            max_interval: Maximum polling interval.

        Returns:
            JobResult with the final job state, or timeout error.
        """
        deadline = time.monotonic() + timeout
        current_interval = interval

        while time.monotonic() < deadline:
            result = self.job_status(job_id)

            if not result.ok:
                if result.error == JobError.NOT_FOUND:
                    # Job might not be registered yet, keep polling
                    time.sleep(current_interval)
                    current_interval = min(current_interval * backoff, max_interval)
                    continue
                return result

            job = result.data
            if not isinstance(job, JobStatus):
                return JobResult(
                    ok=False,
                    error=JobError.VALIDATION,
                    message="Unexpected data type from job_status()",
                )

            if job.state in ("finished", "error"):
                return JobResult(
                    ok=(job.state == "finished"),
                    data=job,
                    message=f"Job {job_id}: {job.state}",
                )

            time.sleep(current_interval)
            current_interval = min(current_interval * backoff, max_interval)

        return JobResult(
            ok=False,
            error=JobError.TIMEOUT,
            message=f"Timed out waiting for job '{job_id}' after {timeout}s",
        )

    def wait_for_prompt(
        self,
        prompt_id: str,
        timeout: float = 120.0,
        interval: float = 1.0,
        backoff: float = 1.5,
        max_interval: float = 5.0,
    ) -> JobResult:
        """Poll until a job matching prompt_id reaches terminal state.

        Convenience method combining find_by_prompt_id + wait_for_job.
        """
        # First, find the job
        start_time = time.monotonic()
        find_result = self.find_by_prompt_id(prompt_id)
        if not find_result.ok:
            if find_result.error == JobError.NOT_FOUND:
                # Try polling for the job to appear
                deadline = time.monotonic() + timeout
                current_interval = interval
                while time.monotonic() < deadline:
                    find_result = self.find_by_prompt_id(prompt_id)
                    if find_result.ok:
                        break
                    if find_result.error != JobError.NOT_FOUND:
                        return find_result
                    time.sleep(current_interval)
                    current_interval = min(current_interval * backoff, max_interval)
                else:
                    return JobResult(
                        ok=False,
                        error=JobError.TIMEOUT,
                        message=f"Timed out waiting for prompt_id '{prompt_id}' to appear after {timeout}s",
                    )
            else:
                return find_result

        job = find_result.data
        if not isinstance(job, JobStatus):
            return JobResult(
                ok=False,
                error=JobError.VALIDATION,
                message="Unexpected data type",
            )

        # Now wait for the job to complete
        elapsed = time.monotonic() - start_time
        remaining = max(0, timeout - elapsed)
        return self.wait_for_job(
            job.job_id,
            timeout=remaining,
            interval=interval,
            backoff=backoff,
            max_interval=max_interval,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _connection_message(raw_error: str | None) -> str:
        if raw_error:
            return f"Krita bridge unreachable: {raw_error}"
        return "Krita bridge unreachable: unknown error"
