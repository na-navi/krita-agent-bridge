"""Polling timeout policy for krita-agent-bridge E2E automation.

Design contract (revised 2026-05-29):

We have two distinct constraints:

1. **Per-task cap (PER_TASK_SECONDS = 60s).** Any single polling wait
   (readiness, job completion, bootstrap, ...) must not exceed 60s on its own.
   60s is comfortably above the worst expected single-stage delay
   (ComfyUI dying mid-job and self-recovering empirically completes
   well within a minute when it is going to recover at all). Anything
   above that means "something unusual happened, hand back to a human".

2. **Total budget (N x 60s).** An automation entry point that polls
   multiple times in sequence (e.g. the smoke runner does readiness +
   job_wait = 2 tasks) gets a budget of N * 60s for the *whole* run.
   The budget is a shared pool: early stages that finish quickly free
   up their unused share, but no single stage may ever exceed the
   per-task cap. This bounds total wall time at N minutes and prevents
   a single hang from starving later stages.

3. **Escape hatch (KRITA_AGENT_ALLOW_LONG_POLL=1).** When set, both
   caps are disabled for the duration of the process. This is an
   explicit human-supervised mode and emits a stderr notice for CI
   visibility.

The module exposes:

- ``PER_TASK_SECONDS`` / ``MAX_POLL_SECONDS`` (alias kept for back-compat).
- ``clamp_poll_timeout()`` for a single-shot wait
  (used by bootstrap, ready --wait, and any one-shot caller).
- ``PollBudget`` for multi-stage runs (used by the smoke runner) which
  carves out per-stage allowances from a shared deadline.

This module is intentionally tiny and dependency-free so it can be
imported from anywhere (CLI, smoke runner, bootstrap, readiness)
without circular deps.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

PER_TASK_SECONDS: float = 60.0
# Back-compat alias. External code (and a previous version of this module)
# referred to the cap as MAX_POLL_SECONDS; keep the name working but point
# it at the new per-task cap.
MAX_POLL_SECONDS: float = PER_TASK_SECONDS
ESCAPE_ENV: str = "KRITA_AGENT_ALLOW_LONG_POLL"


def long_poll_allowed() -> bool:
    """Public predicate so callers can decide whether to disable internal caps."""
    return os.environ.get(ESCAPE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _warn(message: str, stream) -> None:
    out = stream if stream is not None else sys.stderr
    print(f"[polling-policy] {message}", file=out)


def clamp_poll_timeout(
    requested: float,
    *,
    label: str = "polling",
    stream=None,
) -> float:
    """Clamp a single-shot polling timeout to PER_TASK_SECONDS.

    Args:
        requested: Timeout in seconds as requested by the caller / CLI flag.
        label: Short label used in the warning message (e.g. "smoke", "bootstrap").
        stream: Stream to write the warning to (defaults to sys.stderr).

    Returns:
        The effective timeout to actually use. Equal to ``requested`` when it
        is already within the cap, or when the long-poll escape hatch is set.
    """
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = PER_TASK_SECONDS

    if value <= 0:
        _warn(
            f"{label}: requested timeout {requested!r} is non-positive; "
            f"using {PER_TASK_SECONDS:.0f}s.",
            stream,
        )
        return PER_TASK_SECONDS

    if value <= PER_TASK_SECONDS:
        return value

    if long_poll_allowed():
        _warn(
            f"{label}: {value:.0f}s exceeds the {PER_TASK_SECONDS:.0f}s per-task cap, "
            f"but {ESCAPE_ENV}=1 is set; honoring the requested value. "
            f"A human is expected to be watching this run.",
            stream,
        )
        return value

    _warn(
        f"{label}: requested timeout {value:.0f}s exceeds the "
        f"{PER_TASK_SECONDS:.0f}s per-task cap; clamping to {PER_TASK_SECONDS:.0f}s. "
        f"Set {ESCAPE_ENV}=1 to bypass (human-supervised).",
        stream,
    )
    return PER_TASK_SECONDS


@dataclass
class PollBudget:
    """A shared deadline for a multi-stage poll-heavy run.

    Each automation entry point that performs N sequential polling stages
    creates one ``PollBudget(task_count=N)`` at the start. Stages then ask
    for their slice via :meth:`take`:

    - The stage receives ``min(per_task_cap, remaining_total_budget)``.
    - Stages that finish early leave time in the pool; later stages can
      use it but **never** exceed the per-task cap.
    - When the escape env is set, the budget becomes unbounded and ``take``
      returns the caller-requested fallback (used as "no limit" indicator).

    Why a class and not a closure: makes the per-stage decision testable
    and gives nice introspection (``elapsed``, ``remaining``) for logs.
    """

    task_count: int
    per_task: float = PER_TASK_SECONDS
    stream: object = None
    _started: float = 0.0
    _bypass: bool = False

    def __post_init__(self) -> None:
        if self.task_count < 1:
            self.task_count = 1
        self._started = time.monotonic()
        self._bypass = long_poll_allowed()
        if self._bypass:
            _warn(
                f"poll-budget: {ESCAPE_ENV}=1; total/per-task caps disabled "
                f"(human-supervised mode).",
                self.stream,
            )

    @property
    def total(self) -> float:
        """Total wall-time budget for this run (task_count * per_task)."""
        return self.task_count * self.per_task

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def remaining(self) -> float:
        """Remaining shared budget. Clamped to >= 0."""
        return max(0.0, self.total - self.elapsed)

    def take(self, label: str, *, fallback: float = PER_TASK_SECONDS) -> float:
        """Return the timeout the next polling stage should use.

        Args:
            label: Stage name for log messages (e.g. "readiness", "job_wait").
            fallback: Timeout to use when the escape env is set
                (in that case both caps are disabled and we just honor it).

        Returns:
            ``min(per_task, remaining_total)`` or ``fallback`` when bypassed.
            Always >= 1 second when the budget is not yet exhausted, to avoid
            handing a 0s "instant timeout" to a polling loop.
        """
        if self._bypass:
            return max(float(fallback), 1.0)

        remaining = self.remaining
        if remaining <= 0:
            _warn(
                f"poll-budget[{label}]: total budget of {self.total:.0f}s exhausted "
                f"(elapsed {self.elapsed:.1f}s); stage will fail fast.",
                self.stream,
            )
            return 0.0

        allowance = min(self.per_task, remaining)
        # Don't hand a zero or sub-second timeout to a polling loop unless
        # the pool really is empty; that just produces noisy spurious timeouts.
        return max(allowance, 1.0)


__all__ = [
    "PER_TASK_SECONDS",
    "MAX_POLL_SECONDS",
    "ESCAPE_ENV",
    "clamp_poll_timeout",
    "long_poll_allowed",
    "PollBudget",
]
