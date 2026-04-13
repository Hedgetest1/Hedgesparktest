"""Tests for the staged rollout primitive."""
from __future__ import annotations

from unittest.mock import patch

from app.core import staged_rollout as sr


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.hash_store = {}
    def hset(self, key, field=None, value=None, mapping=None):
        h = self.hash_store.setdefault(key, {})
        if mapping:
            h.update({k: str(v) for k, v in mapping.items()})
        elif field is not None:
            h[field] = str(value)
    def hget(self, key, field):
        v = self.hash_store.get(key, {}).get(field)
        return v.encode() if v else None
    def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.hash_store.get(key, {}).items()}
    def hlen(self, key):
        return len(self.hash_store.get(key, {}))
    def expire(self, k, t): pass


def test_promote_if_healthy_refuses_unhealthy(monkeypatch):
    fake = FakeRedis()
    with patch("app.core.staged_rollout._slo_health_for_flag") as mock_health, \
         patch("app.core.feature_flags._redis", return_value=fake), \
         patch("app.core.staged_rollout._ring_started_at", return_value=0), \
         patch("app.core.redis_client._client", return_value=fake):
        mock_health.return_value = {"healthy": False, "reason": "breach"}
        result = sr.promote_if_healthy("night_shift_agent")
    assert result["promoted"] is False
    assert "slo_unhealthy" in result["reason"]


def test_promote_if_healthy_refuses_dwell(monkeypatch):
    fake = FakeRedis()
    import time
    with patch("app.core.staged_rollout._slo_health_for_flag", return_value={"healthy": True}), \
         patch("app.core.feature_flags._redis", return_value=fake), \
         patch("app.core.staged_rollout._ring_started_at", return_value=int(time.time())), \
         patch("app.core.redis_client._client", return_value=fake):
        # Flag currently at ring 2 with fresh dwell — cannot promote yet
        from app.core.feature_flags import set_flag
        set_flag("autonomous_loop", enabled=True, ring=2)
        result = sr.promote_if_healthy("autonomous_loop")
    assert result["promoted"] is False
    assert result["reason"] == "dwell_time_not_met"


def test_promote_if_healthy_refuses_unregistered_flag():
    result = sr.promote_if_healthy("nonexistent_flag")
    assert result["promoted"] is False
    assert result["reason"] == "not_registered"


def test_rollback_resets_ring(monkeypatch):
    fake = FakeRedis()
    with patch("app.core.feature_flags._redis", return_value=fake), \
         patch("app.core.staged_rollout._record_ring_change"), \
         patch("app.core.redis_client._client", return_value=fake):
        result = sr.rollback("night_shift_agent", "test rollback")
    assert result["rolled_back"] is True
