"""Contract tests for the SLO batched-flush record path
(2026-05-14 — replaces the per-request pipeline that was the proven
backend throughput bottleneck under realistic 1000+ concurrent
merchant load: 10 Redis ops/request → 1 pipeline/sec/worker).

Locks:
  1. record_timing buffers in-process (no Redis op on the request path)
  2. _flush_buffer issues a single pipeline that aggregates batched
     observations + still produces the same Redis schema (route_stats
     readers stay correct)
  3. SLO_BATCH_FLUSH=0 escape hatch reverts to the legacy per-request
     pipeline path
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from app.core import slo


@pytest.fixture(autouse=True)
def _reset_slo_buffer():
    """Clear in-process buffer + reset flusher state between tests so
    one test's batch doesn't bleed into the next."""
    with slo._BUFFER_LOCK:
        slo._BUFFER.clear()
        slo._LAST_FLUSH_MONO[0] = time.monotonic()
    yield
    with slo._BUFFER_LOCK:
        slo._BUFFER.clear()


def test_record_timing_does_not_call_redis_synchronously():
    """Hot path contract: record_timing appends to buffer and returns;
    no Redis op fires inline."""
    fake_redis = MagicMock()
    with patch.object(slo, "_redis", return_value=fake_redis):
        slo.record_timing("/dashboard/overview", "GET", 200, 32.5)
    # No pipeline / zadd / incr called from within record_timing
    fake_redis.pipeline.assert_not_called()
    fake_redis.zadd.assert_not_called()
    fake_redis.incr.assert_not_called()
    # Buffer captured the observation
    with slo._BUFFER_LOCK:
        assert len(slo._BUFFER) == 1
        entry = slo._BUFFER[0]
    assert entry[0] == "/dashboard/overview"
    assert entry[1] == "GET"
    assert entry[2] == 200
    assert entry[3] == 32.5


def test_flush_buffer_writes_pipelined_to_redis():
    """Buffer drains via a single pipeline call regardless of batch
    size. Schema stays identical to the legacy path."""
    fake_redis = MagicMock()
    fake_pipe = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe

    # Pre-populate buffer with 5 observations (2 ok, 3 err)
    with slo._BUFFER_LOCK:
        slo._BUFFER.extend([
            ("/dashboard/overview", "GET", 200, 30.0, time.time_ns()),
            ("/dashboard/overview", "GET", 200, 40.0, time.time_ns()),
            ("/dashboard/overview", "GET", 500, 250.0, time.time_ns()),
            ("/dashboard/overview", "GET", 500, 100.0, time.time_ns()),
            ("/dashboard/overview", "GET", 500, 80.0, time.time_ns()),
        ])

    with patch.object(slo, "_redis", return_value=fake_redis):
        slo._flush_buffer()

    # ONE pipeline + ONE execute (regardless of 5 observations × 2 windows)
    assert fake_redis.pipeline.call_count == 1
    assert fake_pipe.execute.call_count == 1
    # zadd called once per (route, window) — there's 1 route × 2 windows
    assert fake_pipe.zadd.call_count == 2
    # zadd's mapping contains all 5 observations packed in
    for call_args in fake_pipe.zadd.call_args_list:
        key, members = call_args[0]
        assert key.startswith("hs:slo:tm:") and key.endswith(":GET:/dashboard/overview")
        assert len(members) == 5  # all 5 observations packed
    # zremrangebyscore called once per (route, window) — 1 × 2 = 2
    assert fake_pipe.zremrangebyscore.call_count == 2
    # incrby (not incr) used for batched count: ok=2, err=3, × 2 windows
    incrby_calls = fake_pipe.incrby.call_args_list
    assert len(incrby_calls) == 4  # (ok-2w + err-2w)
    by_key = {c[0][0]: c[0][1] for c in incrby_calls}
    assert by_key["hs:slo:ok:5m:GET:/dashboard/overview"] == 2
    assert by_key["hs:slo:ok:60m:GET:/dashboard/overview"] == 2
    assert by_key["hs:slo:err:5m:GET:/dashboard/overview"] == 3
    assert by_key["hs:slo:err:60m:GET:/dashboard/overview"] == 3
    # Buffer empty post-flush
    with slo._BUFFER_LOCK:
        assert slo._BUFFER == []


def test_flush_buffer_noop_when_empty():
    """Empty buffer → no Redis op, no error."""
    fake_redis = MagicMock()
    with patch.object(slo, "_redis", return_value=fake_redis):
        slo._flush_buffer()
    fake_redis.pipeline.assert_not_called()


def test_legacy_path_engages_when_env_disabled(monkeypatch):
    """SLO_BATCH_FLUSH=0 reverts to per-request pipeline so operators
    can A/B vs the batched path under live traffic for diagnosis."""
    monkeypatch.setattr(slo, "_BATCH_FLUSH_ENABLED", False)
    fake_redis = MagicMock()
    fake_pipe = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe

    with patch.object(slo, "_redis", return_value=fake_redis):
        slo.record_timing("/dashboard/overview", "GET", 200, 32.5)

    # Legacy path: pipeline called inline (per request × per window)
    assert fake_redis.pipeline.call_count == 2  # 2 windows
    # Buffer untouched
    with slo._BUFFER_LOCK:
        assert slo._BUFFER == []


def test_flush_handles_redis_down_gracefully():
    """Redis unavailable → flush is a no-op; buffer is drained anyway
    (we don't keep stale observations around)."""
    with slo._BUFFER_LOCK:
        slo._BUFFER.append(("/x", "GET", 200, 10.0, time.time_ns()))

    with patch.object(slo, "_redis", return_value=None):
        slo._flush_buffer()
    # Buffer drained even though Redis was down (acceptable: SLO is
    # observability, lost data on Redis outage is documented degrade-open)
    with slo._BUFFER_LOCK:
        assert slo._BUFFER == []


def test_record_then_flush_round_trip_uses_correct_zset_key():
    """Integration: write via record_timing → flush → assert the exact
    Redis key shape so route_stats readers find the same observation."""
    fake_redis = MagicMock()
    fake_pipe = MagicMock()
    fake_redis.pipeline.return_value = fake_pipe

    with patch.object(slo, "_redis", return_value=fake_redis):
        slo.record_timing("/track", "POST", 200, 12.0)
        slo._flush_buffer()

    zadd_keys = [c[0][0] for c in fake_pipe.zadd.call_args_list]
    assert "hs:slo:tm:5m:POST:/track" in zadd_keys
    assert "hs:slo:tm:60m:POST:/track" in zadd_keys
