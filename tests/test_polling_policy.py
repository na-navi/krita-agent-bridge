"""Tests for the 2-minute polling SLO clamp.

Contract under test:
- Any requested polling timeout above MAX_POLL_SECONDS (120s) gets clamped down.
- Non-positive timeouts fall back to MAX_POLL_SECONDS.
- KRITA_AGENT_ALLOW_LONG_POLL=1 disables clamping (human-supervised mode).
- Clamping always writes a visible warning to the provided stream.
"""

from __future__ import annotations

import io

import pytest

from krita_agent_bridge.polling_policy import (
    ESCAPE_ENV,
    MAX_POLL_SECONDS,
    clamp_poll_timeout,
)


@pytest.fixture(autouse=True)
def _clear_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ESCAPE_ENV, raising=False)


def test_under_limit_passthrough() -> None:
    buf = io.StringIO()
    assert clamp_poll_timeout(60.0, label="smoke", stream=buf) == 60.0
    assert buf.getvalue() == ""


def test_exactly_at_limit_passthrough() -> None:
    buf = io.StringIO()
    assert clamp_poll_timeout(MAX_POLL_SECONDS, label="smoke", stream=buf) == MAX_POLL_SECONDS
    assert buf.getvalue() == ""


def test_over_limit_clamps_with_warning() -> None:
    buf = io.StringIO()
    out = clamp_poll_timeout(300.0, label="smoke", stream=buf)
    assert out == MAX_POLL_SECONDS
    warning = buf.getvalue()
    assert "clamping" in warning
    assert "smoke" in warning
    assert ESCAPE_ENV in warning


def test_escape_env_disables_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ESCAPE_ENV, "1")
    buf = io.StringIO()
    out = clamp_poll_timeout(600.0, label="bootstrap", stream=buf)
    assert out == 600.0
    assert "human is expected" in buf.getvalue()


@pytest.mark.parametrize("raw", [0, -10, -0.5])
def test_non_positive_falls_back_to_default(raw: float) -> None:
    buf = io.StringIO()
    out = clamp_poll_timeout(raw, label="ready", stream=buf)
    assert out == MAX_POLL_SECONDS
    assert "non-positive" in buf.getvalue()


def test_garbage_input_falls_back_to_default() -> None:
    buf = io.StringIO()
    # The implementation accepts a float-coercible argument; non-numerics
    # become MAX_POLL_SECONDS silently (no crash, safe default).
    out = clamp_poll_timeout("not-a-number", label="ready", stream=buf)  # type: ignore[arg-type]
    assert out == MAX_POLL_SECONDS
