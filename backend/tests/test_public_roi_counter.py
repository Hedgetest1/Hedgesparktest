"""Tests for the public ROI counter endpoint."""
from __future__ import annotations

from unittest.mock import patch

from app.api import public_roi_counter as prc


def test_compute_returns_honest_warming_state_on_db_failure(monkeypatch):
    """No fake floor. DB failure → warming state with real (zero) numbers."""
    class FailingSession:
        def execute(self, *a, **kw):
            raise RuntimeError("db down")
        def close(self):
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: FailingSession())

    doc = prc._compute()
    assert doc["state"] == "warming"
    assert doc["prevented_eur_30d"] == 0
    assert doc["shops_contributing"] == 0
    assert "publish_thresholds" in doc


def test_compute_live_state_when_above_threshold(monkeypatch):
    """When real data is above threshold, state flips to live."""
    class RealSession:
        def __init__(self):
            self.calls = 0
        def execute(self, *a, **kw):
            self.calls += 1
            class R:
                def fetchone(self):
                    return (50_000.0, 5)
                def fetchall(self):
                    return []
            return R()
        def close(self): pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: RealSession())
    doc = prc._compute()
    assert doc["state"] == "live"
    assert doc["prevented_eur_30d"] == 50_000
    assert doc["shops_contributing"] == 5


def test_get_cached_or_compute_uses_cache(monkeypatch):
    """Second call should read from Redis without hitting _compute."""
    calls = {"n": 0}

    def fake_compute():
        calls["n"] += 1
        return {
            "prevented_eur_30d": 200_000,
            "raw_prevented_eur_30d": 200_000,
            "shops_contributing": 12,
            "by_vertical": [],
            "window_days": 30,
            "generated_at": "2026-04-13T00:00:00",
        }

    class FakeRedis:
        def __init__(self): self.store = {}
        def get(self, k): return self.store.get(k)
        def setex(self, k, ttl, v): self.store[k] = v

    fake_rc = FakeRedis()

    with patch("app.core.redis_client._client", return_value=fake_rc):
        monkeypatch.setattr(prc, "_compute", fake_compute)
        d1 = prc._get_cached_or_compute()
        d2 = prc._get_cached_or_compute()

    assert d1 == d2
    assert calls["n"] == 1
