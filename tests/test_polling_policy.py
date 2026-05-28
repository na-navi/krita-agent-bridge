"""Tests for the per-task / dynamic polling policy.

Contract under test:
- ``clamp_poll_timeout`` clamps any single-shot wait to PER_TASK_SECONDS (60s).
- Non-positive timeouts fall back to PER_TASK_SECONDS.
- KRITA_AGENT_ALLOW_LONG_POLL=1 disables the clamp (human-supervised mode).
- ``PollBudget`` enforces both per-task cap and N * per_task total budget,
  releases unused budget from earlier stages to later ones, but never lets
  a single stage exceed the per-task cap.
- Bypass env disables both caps on the budget too.
"""

from __future__ import annotations

import io
import time

import pytest

from krita_agent_bridge.polling_policy import (
    ESCAPE_ENV,
    MAX_POLL_SECONDS,
    PER_TASK_SECONDS,
    PollBudget,
    clamp_poll_timeout,
    long_poll_allowed,
)


@pytest.fixture(autouse=True)
def _clear_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ESCAPE_ENV, raising=False)


# ---------------------------------------------------------------------------
# clamp_poll_timeout
# ---------------------------------------------------------------------------


def test_max_alias_equals_per_task() -> None:
    """MAX_POLL_SECONDS is kept as a back-compat alias for PER_TASK_SECONDS."""
    assert MAX_POLL_SECONDS == PER_TASK_SECONDS == 60.0


def test_under_limit_passthrough() -> None:
    buf = io.StringIO()
    assert clamp_poll_timeout(30.0, label="smoke", stream=buf) == 30.0
    assert buf.getvalue() == ""


def test_exactly_at_limit_passthrough() -> None:
    buf = io.StringIO()
    assert clamp_poll_timeout(PER_TASK_SECONDS, label="smoke", stream=buf) == PER_TASK_SECONDS
    assert buf.getvalue() == ""


def test_over_limit_clamps_with_warning() -> None:
    buf = io.StringIO()
    out = clamp_poll_timeout(300.0, label="smoke", stream=buf)
    assert out == PER_TASK_SECONDS
    warning = buf.getvalue()
    assert "clamping" in warning
    assert "smoke" in warning
    assert ESCAPE_ENV in warning


def test_escape_env_disables_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ESCAPE_ENV, "1")
    assert long_poll_allowed() is True
    buf = io.StringIO()
    out = clamp_poll_timeout(600.0, label="bootstrap", stream=buf)
    assert out == 600.0
    assert "human is expected" in buf.getvalue()


@pytest.mark.parametrize("raw", [0, -10, -0.5])
def test_non_positive_falls_back_to_default(raw: float) -> None:
    buf = io.StringIO()
    out = clamp_poll_timeout(raw, label="ready", stream=buf)
    assert out == PER_TASK_SECONDS
    assert "non-positive" in buf.getvalue()


def test_garbage_input_falls_back_to_default() -> None:
    buf = io.StringIO()
    out = clamp_poll_timeout("not-a-number", label="ready", stream=buf)  # type: ignore[arg-type]
    assert out == PER_TASK_SECONDS


# ---------------------------------------------------------------------------
# PollBudget
# ---------------------------------------------------------------------------


def test_budget_total_is_task_count_times_per_task() -> None:
    b = PollBudget(task_count=2, stream=io.StringIO())
    assert b.total == 2 * PER_TASK_SECONDS


def test_budget_task_count_floor_is_one() -> None:
    b = PollBudget(task_count=0, stream=io.StringIO())
    assert b.task_count == 1
    assert b.total == PER_TASK_SECONDS


def test_budget_first_stage_capped_at_per_task() -> None:
    """Even with a 10-task pool, no single stage may exceed PER_TASK_SECONDS."""
    b = PollBudget(task_count=10, stream=io.StringIO())
    first = b.take("readiness")
    assert first == PER_TASK_SECONDS  # capped at per-task, not 10*per-task


def test_budget_unused_time_flows_to_later_stages() -> None:
    """If stage 1 finishes fast, stage 2 still gets the per-task cap
    because (total - tiny_elapsed) > per_task."""
    b = PollBudget(task_count=2, stream=io.StringIO())
    first = b.take("readiness")
    assert first == PER_TASK_SECONDS
    # Simulate that stage 1 actually took ~0s (no sleep), pool still ~120s.
    second = b.take("job_wait")
    assert second == PER_TASK_SECONDS


def test_budget_later_stage_shrinks_when_pool_runs_low() -> None:
    """When less than per_task remains in the pool, the next take() gets
    only the remainder."""
    b = PollBudget(task_count=2, stream=io.StringIO())
    # Forge an elapsed time of (total - 10s) so the remaining pool is 10s.
    b._started = time.monotonic() - (b.total - 10.0)
    out = b.take("job_wait")
    # Should be ~10s, definitely less than the per-task cap.
    assert 0 < out <= 10.0
    assert out < PER_TASK_SECONDS


def test_budget_exhausted_returns_zero_with_warning() -> None:
    buf = io.StringIO()
    b = PollBudget(task_count=2, stream=buf)
    # Push past the deadline.
    b._started = time.monotonic() - (b.total + 1.0)
    out = b.take("job_wait")
    assert out == 0.0
    assert "exhausted" in buf.getvalue()


def test_budget_min_allowance_is_one_second_when_pool_has_time() -> None:
    """Polling loops should not be handed a 0.0001s timeout when there is
    still meaningful budget; floor at 1s."""
    b = PollBudget(task_count=2, stream=io.StringIO())
    b._started = time.monotonic() - (b.total - 0.2)  # ~0.2s left
    out = b.take("job_wait")
    assert out == 1.0  # floored


def test_budget_bypass_env_disables_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ESCAPE_ENV, "1")
    buf = io.StringIO()
    b = PollBudget(task_count=2, stream=buf)
    # Bypass notice should be emitted at construction time.
    assert "human-supervised" in buf.getvalue()
    # take() ignores the per-task cap and honors fallback.
    assert b.take("readiness", fallback=999.0) == 999.0


def test_budget_bypass_floors_fallback_to_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ESCAPE_ENV, "1")
    b = PollBudget(task_count=2, stream=io.StringIO())
    # Even in bypass mode, a 0s fallback gets floored to 1s so a polling
    # loop is not handed an instant-timeout.
    assert b.take("readiness", fallback=0.0) == 1.0
