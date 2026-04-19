"""SLO observability tests."""
from __future__ import annotations

from unittest.mock import patch

from app.core import slo


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.counters = {}
    def pipeline(self):
        return FakePipeline(self)
    def zrange(self, key, start, stop):
        return list(self.store.get(key, {}).keys())
    def get(self, key):
        v = self.counters.get(key, 0)
        return str(v).encode()

class FakePipeline:
    def __init__(self, rc):
        self.rc = rc
        self.ops = []
    def zadd(self, key, mapping):
        store = self.rc.store.setdefault(key, {})
        store.update(mapping)
        return self
    def zremrangebyscore(self, key, lo, hi):
        return self
    def expire(self, key, ttl):
        return self
    def incr(self, key):
        self.rc.counters[key] = self.rc.counters.get(key, 0) + 1
        return self
    def execute(self):
        return self.ops


def test_quantile_edge_cases():
    assert slo._quantile([], 0.95) == 0.0
    assert slo._quantile([100.0], 0.95) == 100.0
    # Monotonic values
    values = [float(i) for i in range(1, 101)]
    assert slo._quantile(values, 0.5) == 50.5
    assert slo._quantile(values, 0.95) == 95.05


def test_record_timing_never_raises_without_redis():
    with patch("app.core.slo._redis", return_value=None):
        slo.record_timing("/test", "GET", 200, 42.5)  # should not raise


def test_route_stats_uses_redis():
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for i in range(10):
            slo.record_timing("/pro/rars", "GET", 200, 100 + i * 10)
        stats = slo.route_stats("/pro/rars", "GET", "5m")

    assert stats["observations"] >= 1
    assert stats["p50_ms"] > 0
    assert stats["p95_ms"] > 0


def test_slo_report_classifies_insufficient_data():
    """With no observations, every SLO should be insufficient_data."""
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        report = slo.slo_report()
    for entry in report:
        assert entry["health"] == "insufficient_data"
        assert entry["observations"] == 0


def test_record_timing_identical_duration_same_ms_does_not_coalesce():
    """Regression: 20 observations with identical duration fired in a
    tight loop must produce 20 distinct ZSET members, not 1.

    Prior implementation used `{now_ms}:{duration_ms}` as the member
    key, collapsing observations within the same millisecond into one
    entry. Under production load (10k+ merchants) or a hot-loop test,
    this trips the `obs < 10 → insufficient_data` gate in slo_report
    and suppresses legitimate breach alerts.

    Fix uses nanosecond precision on the member side (score stays ms
    for cheap zremrangebyscore windowing).
    """
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for _ in range(20):
            slo.record_timing("/hot", "POST", 200, 500.0)
    # Both windows should have 20 distinct members.
    for window in ("5m", "60m"):
        key = f"hs:slo:tm:{window}:POST:/hot"
        members = fake.store.get(key, {})
        assert len(members) == 20, (
            f"window={window} coalesced {20 - len(members)} observations; "
            f"members={list(members.keys())[:3]}..."
        )


def test_route_stats_parses_ns_prefixed_members():
    """route_stats must correctly extract duration from the
    nanosecond-prefixed member format `{ns}:{duration}`."""
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for _ in range(15):
            slo.record_timing("/parse-check", "GET", 200, 300.0)
        stats = slo.route_stats("/parse-check", "GET", "5m")
    assert stats["observations"] == 15
    assert stats["p95_ms"] == 300.0
    assert stats["p50_ms"] == 300.0
