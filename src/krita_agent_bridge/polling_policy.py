"""Polling timeout policy for krita-agent-bridge E2E automation.

Design contract (decided 2026-05-29):

- The longest tolerable polling wait in automated mode is **120 seconds (2 minutes)**.
- Rationale: the worst expected delay in this stack is ComfyUI dying mid-job and
  recovering on its own, which empirically completes within ~2 minutes.
  Anything longer is treated as "something unusual happened, a human must look".
- Therefore CLI / automation entry points clamp any user-supplied polling timeout
  to MAX_POLL_SECONDS and emit a stderr warning when clamping occurs.
- The only escape hatch is the environment variable KRITA_AGENT_ALLOW_LONG_POLL=1,
  which disables clamping for that process. This is an explicit human decision,
  not a default. It still emits a stderr notice so it is visible in CI logs.

This module is intentionally tiny and dependency-free so it can be imported from
anywhere (CLI, smoke runner, bootstrap, readiness) without circular deps.
"""

from __future__ import annotations

import os
import sys

MAX_POLL_SECONDS: float = 120.0
ESCAPE_ENV: str = "KRITA_AGENT_ALLOW_LONG_POLL"


def _long_poll_allowed() -> bool:
    return os.environ.get(ESCAPE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def clamp_poll_timeout(
    requested: float,
    *,
    label: str = "polling",
    stream=None,
) -> float:
    """Clamp a polling timeout to the project's 2-minute SLO.

    Args:
        requested: Timeout in seconds as requested by the caller / CLI flag.
        label: Short label used in the warning message (e.g. "smoke", "bootstrap").
        stream: Stream to write the warning to (defaults to sys.stderr).

    Returns:
        The effective timeout actually applied. Equal to ``requested`` when it is
        already within the SLO, or when the long-poll escape hatch is set.
    """
    out = stream if stream is not None else sys.stderr

    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = MAX_POLL_SECONDS

    if value <= 0:
        # Treat non-positive as "use the default", not as "wait forever".
        print(
            f"[polling-policy] {label}: requested timeout {requested!r} is non-positive; "
            f"using {MAX_POLL_SECONDS:.0f}s.",
            file=out,
        )
        return MAX_POLL_SECONDS

    if value <= MAX_POLL_SECONDS:
        return value

    if _long_poll_allowed():
        print(
            f"[polling-policy] {label}: {value:.0f}s exceeds the {MAX_POLL_SECONDS:.0f}s SLO, "
            f"but {ESCAPE_ENV}=1 is set; honoring the requested value. "
            f"A human is expected to be watching this run.",
            file=out,
        )
        return value

    print(
        f"[polling-policy] {label}: requested timeout {value:.0f}s exceeds the "
        f"{MAX_POLL_SECONDS:.0f}s automation SLO; clamping to {MAX_POLL_SECONDS:.0f}s. "
        f"If you really need a longer wait, set {ESCAPE_ENV}=1 and re-run "
        f"(this means a human will be watching).",
        file=out,
    )
    return MAX_POLL_SECONDS


__all__ = ["MAX_POLL_SECONDS", "ESCAPE_ENV", "clamp_poll_timeout"]
