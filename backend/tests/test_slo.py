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
