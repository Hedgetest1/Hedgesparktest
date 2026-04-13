"""Tests for the feature flag primitive."""
from __future__ import annotations

from unittest.mock import patch

from app.core import feature_flags as ff


class FakeRedis:
    def __init__(self):
        self.store = {}
    def hgetall(self, key):
        return self.store.get(key, {})
    def hset(self, key, mapping):
        cur = self.store.setdefault(key, {})
        cur.update(mapping)
    def get(self, key): return self.store.get(key)
    def setex(self, k, t, v): self.store[k] = v


def _with_redis():
    fake = FakeRedis()
    return fake, patch("app.core.feature_flags._redis", return_value=fake)


def test_defaults_from_registry():
    # Unknown flag with no redis → conservative False
    with patch("app.core.feature_flags._redis", return_value=None):
        assert ff.is_enabled("unknown_flag", shop="a.myshopify.com") is False

    # Known flag with default enabled → True at 100%
    with patch("app.core.feature_flags._redis", return_value=None):
        assert ff.is_enabled("night_shift_agent", shop="a.myshopify.com") is True


def test_killswitch_wins():
    fake, p = _with_redis()
    with p:
        ff.set_flag("night_shift_agent", killswitch=True)
        assert ff.is_enabled("night_shift_agent", shop="any.myshopify.com") is False


def test_percentage_rollout_is_deterministic():
    """Same shop should consistently fall on the same side of a % line."""
    fake, p = _with_redis()
    with p:
        ff.set_flag("autonomous_loop", enabled=True, percentage=50)
        shop = "deterministic.myshopify.com"
        first = ff.is_enabled("autonomous_loop", shop=shop)
        # Ten calls, same answer
        for _ in range(10):
            assert ff.is_enabled("autonomous_loop", shop=shop) is first


def test_allowlist_overrides_percentage():
    fake, p = _with_redis()
    with p:
        ff.set_flag("autonomous_loop", enabled=True, percentage=0, allowlist=["vip.myshopify.com"])
        assert ff.is_enabled("autonomous_loop", shop="vip.myshopify.com") is True
        assert ff.is_enabled("autonomous_loop", shop="other.myshopify.com") is False


def test_rollout_distribution_is_approximately_even():
    """With 50% rollout, ~50% of 1000 shops should be enabled."""
    fake, p = _with_redis()
    with p:
        ff.set_flag("autonomous_loop", enabled=True, percentage=50)
        enabled_count = sum(
            1 for i in range(1000)
            if ff.is_enabled("autonomous_loop", shop=f"shop{i}.myshopify.com")
        )
    # Allow ±8% slack for 1000-sample variance
    assert 420 <= enabled_count <= 580


def test_ring_assignment():
    fake, p = _with_redis()
    with p:
        # Internal shops
        import os
        os.environ["HS_INTERNAL_SHOPS"] = "internal.myshopify.com"
        try:
            assert ff.ring_for_shop("night_shift_agent", "internal.myshopify.com") == 0
        finally:
            os.environ.pop("HS_INTERNAL_SHOPS", None)

        # Distribution across rings is non-degenerate
        rings = [
            ff.ring_for_shop("night_shift_agent", f"s{i}.myshopify.com")
            for i in range(500)
        ]
        # Some should be ring 1, some ring 2, most ring 3
        assert any(r == 1 for r in rings)
        assert any(r == 2 for r in rings)
        assert any(r == 3 for r in rings)


def test_rollout_allows_respects_ring():
    fake, p = _with_redis()
    with p:
        # max_ring=0 should only allow explicit internal/allowlist shops
        import os
        os.environ["HS_INTERNAL_SHOPS"] = "vip.myshopify.com"
        try:
            assert ff.rollout_allows("night_shift_agent", "vip.myshopify.com", max_ring=0) is True
        finally:
            os.environ.pop("HS_INTERNAL_SHOPS", None)
