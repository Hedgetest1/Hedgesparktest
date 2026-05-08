"""RARS cache stampede protection — pin the 2026-05-08 fix.

Pre-fix: when the 5-min cache expired under load, every concurrent
caller for the same shop would re-run the 5-component compute
(~700ms). At 10k Pro merchants × weekly digest fan-out this is a real
DB-pool exhaustion class.

Post-fix: a SETNX lock at `hs:rars:lock:v1:{plan}:{md5(shop)}`
serializes the recompute. Other waiters poll the cache briefly
(3s budget) then fall through.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from app.services import revenue_at_risk as rars


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, tuple[str, float]] = {}
        self._locks: set[str] = set()

    def get(self, k):
        from time import time
        v = self._store.get(k)
        if not v:
            return None
        val, expires_at = v
        if expires_at < time():
            del self._store[k]
            return None
        return val.encode() if isinstance(val, str) else val

    def set(self, k, v, nx=False, ex=None):
        from time import time
        if nx and k in self._locks:
            return False
        self._locks.add(k)
        ttl = ex if ex else 3600
        self._store[k] = (v, time() + ttl)
        return True

    def setex(self, k, ttl, v):
        from time import time
        self._store[k] = (v, time() + ttl)
        return True

    def delete(self, k):
        self._locks.discard(k)
        self._store.pop(k, None)
        return 1


def test_lock_acquired_releases_after_compute(db, monkeypatch):
    """Compute must release the lock so subsequent calls can recompute
    on TTL expiry without waiting for the 40s lock TTL.
    """
    fake = _FakeRedis()
    shop = "_test_rars_stampede_release_.myshopify.com"

    with patch("app.core.redis_client._client", return_value=fake), \
         patch.object(rars, "_compute_abandoned_high_intent",
                      return_value=rars.RARSComponent("abandoned", 0.0, "narr")), \
         patch.object(rars, "_compute_refund_decline",
                      return_value=rars.RARSComponent("refund", 0.0, "narr")), \
         patch.object(rars, "_compute_nudge_gap",
                      return_value=rars.RARSComponent("nudge", 0.0, "narr")), \
         patch.object(rars, "_compute_below_benchmark",
                      return_value=rars.RARSComponent("benchmark", 0.0, "narr")), \
         patch.object(rars, "_compute_goal_gap",
                      return_value=rars.RARSComponent("goal", 0.0, "narr")), \
         patch.object(rars, "_compute_prevented", return_value=(0.0, {})), \
         patch.object(rars, "get_shop_currency", return_value="USD"):
        rars.get_revenue_at_risk(db, shop, plan="pro")

    # After compute, lock must be released (not in _locks).
    lock_keys = [k for k in fake._locks if k.startswith("hs:rars:lock:")]
    assert lock_keys == [], (
        f"stampede lock must be released after compute, found {lock_keys}"
    )


def test_concurrent_caller_uses_cache_when_lock_held(db):
    """When the lock is already held AND the cache fills before timeout,
    the second caller returns from cache without re-computing. We simulate
    this by pre-populating the cache and then calling — first compute
    must SHORT-CIRCUIT to cache hit (the cache-hit path returns BEFORE
    the lock acquisition logic runs)."""
    import hashlib
    fake = _FakeRedis()
    shop = "_test_rars_cache_hit_.myshopify.com"
    plan_key = "pro"
    cache_key = f"{rars._CACHE_KEY_PREFIX}:{plan_key}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"

    pre_cached = {
        "shop_domain": shop,
        "total_at_risk_eur": 100.0,
        "components": [],
        "prevented_eur_this_month": 50.0,
        "net_roi_eur": -49.0,
        "generated_at": "2026-05-08T00:00:00Z",
        "headline": "test",
    }
    fake.setex(cache_key, 300, json.dumps(pre_cached))

    compute_calls = {"count": 0}
    def _spy(*a, **k):
        compute_calls["count"] += 1
        return rars.RARSComponent("x", 0.0, "narr")

    with patch("app.core.redis_client._client", return_value=fake), \
         patch.object(rars, "_compute_abandoned_high_intent", side_effect=_spy):
        result = rars.get_revenue_at_risk(db, shop, plan="pro")

    # Cache hit path: must NOT have called compute.
    assert compute_calls["count"] == 0, (
        f"cache hit must skip compute entirely, got {compute_calls['count']} calls"
    )
    assert result["total_at_risk_eur"] == 100.0
