"""Tests for the metrics background pusher (fleet liveness keepalive).

Closes the I4 gap from the 3d87add devil's-advocate review: the bg
pusher was shipped without unit-test coverage. These tests prove:

- `start_background_pusher` is idempotent (second call is a no-op).
- The pusher thread publishes `hs:metrics:worker:{pid}` to Redis without
  any traffic — the fix for the 7dace25 fleet-gauge decay bug.
- The push contract is the one `_check_fleet_workers_reporting` reads
  from (same prefix, same key shape, correct TTL).
"""
from __future__ import annotations

import threading
import time

import pytest

from app.core import metrics
from app.core.metrics import (
    _METRICS_REDIS_PREFIX,
    _WORKER_PID,
    _push_snapshot_to_redis,
    start_background_pusher,
    stop_background_pusher,
)
from app.core.redis_client import _client


@pytest.fixture(autouse=True)
def _reset_bg_pusher_state():
    """Reset start-once guard, signal any prior daemon to exit, and
    flush bg-pusher keys before each test. Cleanup repeats post-yield
    so a flaky test cannot leak a stray daemon into the next test."""
    stop_background_pusher()
    metrics._bg_pusher_started = False
    rc = _client()
    if rc is not None:
        for key in rc.scan_iter(match=f"{_METRICS_REDIS_PREFIX}:*", count=50):
            rc.delete(key)
    # Give any running daemon time to observe the stop signal before
    # the test starts spawning new ones (defence against flaky races
    # where an old daemon's _push lands AFTER our pre-test cleanup).
    time.sleep(0.1)
    yield
    stop_background_pusher()
    metrics._bg_pusher_started = False


def test_push_snapshot_writes_key_with_correct_shape():
    _push_snapshot_to_redis(force=True)
    rc = _client()
    assert rc is not None, "test runs against real Redis (DB 15 per conftest)"
    key = f"{_METRICS_REDIS_PREFIX}:{_WORKER_PID}"
    assert rc.exists(key), "push_snapshot must write hs:metrics:worker:{pid}"
    ttl = rc.ttl(key)
    assert 0 < ttl <= metrics._METRICS_TTL_S, f"TTL must be within TTL_S, got {ttl}"


def test_start_background_pusher_is_idempotent():
    """Calling twice must not spawn a second daemon thread.

    Note: daemon threads from prior tests in the same suite cannot be
    killed, so we measure delta rather than absolute count.
    """
    baseline = sum(1 for t in threading.enumerate() if "metrics-bg-pusher-" in t.name)

    assert metrics._bg_pusher_started is False  # reset by autouse fixture
    start_background_pusher()
    assert metrics._bg_pusher_started is True
    after_first = sum(1 for t in threading.enumerate() if "metrics-bg-pusher-" in t.name)
    assert after_first == baseline + 1, "first call must start exactly one new thread"

    start_background_pusher()  # second call — must be no-op
    after_second = sum(1 for t in threading.enumerate() if "metrics-bg-pusher-" in t.name)
    assert after_second == after_first, "second call must not start another thread"


def test_background_pusher_publishes_without_traffic(monkeypatch):
    """The bug that motivated the pusher: idle workers decaying from fleet.

    With a short interval, the pusher should write the key within a few
    hundred ms even though no request has been handled.

    Flake hardening 2026-04-23: poll window raised from 1s to 3s. The
    test was seen failing rarely during full-suite runs earlier today,
    coincident with a live ghost-SQL bug in onboarding_health that was
    generating ~4800 log.warning cycles per worker log file per day.
    System noise starved the 0.05s-interval daemon thread enough that
    it sometimes missed the 1s deadline. The ghost-SQL bug was fixed
    in the same session (commit 36a5033); the test now passes 5/5 on
    full-suite stress runs. The 3s window is defence in depth — if
    any future cause of system noise re-emerges the test has headroom
    rather than flaking again.
    """
    monkeypatch.setattr(metrics, "_METRICS_BG_PUSH_INTERVAL_S", 0.05)

    rc = _client()
    assert rc is not None
    key = f"{_METRICS_REDIS_PREFIX}:{_WORKER_PID}"
    assert not rc.exists(key), "precondition: key must be absent before pusher runs"

    start_background_pusher()

    # Poll for up to 3s for the first push to land.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if rc.exists(key):
            break
        time.sleep(0.02)
    assert rc.exists(key), "bg pusher must write key within 3s even without traffic"


def test_bg_pusher_key_matches_invariant_check_prefix():
    """Contract test: fleet-workers invariant reads the same prefix the pusher writes.

    If either side drifts, `_check_fleet_workers_reporting` would count 0
    alive workers even with a healthy fleet — the exact false-positive we
    avoided by adding the pusher. This test pins the contract.
    """
    from app.services.invariant_monitor import _check_fleet_workers_reporting  # noqa: F401

    # Re-read the invariant-check source to confirm the prefix literal
    # matches — protects against accidental rename on either side.
    import inspect
    src = inspect.getsource(_check_fleet_workers_reporting)
    assert f"{_METRICS_REDIS_PREFIX}:*" in src, (
        "invariant check must scan the same prefix the pusher writes"
    )
