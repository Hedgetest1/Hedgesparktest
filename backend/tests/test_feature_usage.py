"""Feature usage telemetry tests."""
from __future__ import annotations

from unittest.mock import patch

from app.core import feature_usage as fu


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}
    def pipeline(self):
        return FakePipe(self)
    def get(self, k):
        v = self.store.get(k)
        return str(v).encode() if v is not None else None
    def scard(self, k):
        return len(self.sets.get(k, set()))


class FakePipe:
    def __init__(self, rc): self.rc = rc
    def incr(self, k): self.rc.store[k] = self.rc.store.get(k, 0) + 1; return self
    def expire(self, k, t): return self
    def sadd(self, k, v): self.rc.sets.setdefault(k, set()).add(v); return self
    def set(self, k, v, ex=None): self.rc.store[k] = v; return self
    def execute(self): pass


def test_track_increments_counter():
    fake = FakeRedis()
    with patch("app.core.feature_usage._redis", return_value=fake):
        fu.track("night_shift_agent", "a.myshopify.com")
        fu.track("night_shift_agent", "a.myshopify.com")
        fu.track("night_shift_agent", "b.myshopify.com")
        s = fu.stats("night_shift_agent")

    assert s["uses_45d"] == 3
    assert s["unique_shops_45d"] == 2
    assert s["dormant"] is False


def test_stats_dormant_when_no_usage():
    fake = FakeRedis()
    with patch("app.core.feature_usage._redis", return_value=fake):
        s = fu.stats("community_marketplace")
    assert s["uses_45d"] == 0
    assert s["dormant"] is True


def test_track_never_raises_without_redis():
    with patch("app.core.feature_usage._redis", return_value=None):
        fu.track("night_shift_agent", "any.myshopify.com")  # should not raise


def test_all_stats_covers_registry():
    fake = FakeRedis()
    with patch("app.core.feature_usage._redis", return_value=fake):
        stats = fu.all_stats()
    assert len(stats) == len(fu.REGISTRY)
    names = {s["feature"] for s in stats}
    assert "night_shift_agent" in names
