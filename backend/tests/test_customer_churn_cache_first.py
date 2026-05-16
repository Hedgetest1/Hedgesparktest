"""
test_customer_churn_cache_first.py — locks the structural 10k fix.

CONTEXT: scripts/explain_at_scale.py PROVED `/pro/customer-churn`'s
`score_shop_customers` query intermittently resolves to an external-merge
disk sort at large-merchant scale (148ms / Disk 2024kB at 50k orders /
6,250 customers on a bloated table; plan-unstable run-to-run). The
structural fix is cache-first + stampede-lock so the heavy query runs
≤1×/TTL/shop on a cold build instead of once per request.

These tests lock the two contract properties of that fix:
  1. `_shape` slices the cached full top-N to the caller's `limit` with
     byte-identical semantics to a direct compute (no false numbers).
  2. A warm cache SHORT-CIRCUITS the heavy query — i.e. the expensive
     `score_shop_customers` call is OFF the per-request hot path on a
     cache hit. This is the entire point of the fix; if a refactor
     reintroduces a per-request heavy query, this test fails.
  3. End-to-end through REAL Redis (not a monkeypatched cache
     boundary): a cache hit issues ZERO DB queries (X-Query-Count
     header == 0). Zero queries ⟹ the get_lazy_read_db proxy never
     checks out a pooled connection (by _LazyReadSession's definition;
     proven by composition, not merely asserted). Added 2026-05-16d
     after an adversarial audit correctly flagged that the original
     tests stubbed the cache boundary and conftest overrides
     get_lazy_read_db — so the load-bearing "0 conns on hit" claim
     was exercised by zero tests.
"""
from __future__ import annotations

import pytest

from app.api import customer_churn
from app.core.deps import require_pro_session
from app.main import app as fastapi_app


def _cust(band: str, i: int) -> dict:
    return {
        "customer_email_hash": f"h{i}",
        "churn_probability": 0.9 - i * 0.001,
        "churn_score_100": 90 - i,
        "risk_band": band,
        "factors": {},
        "total_orders": 3,
    }


def _full_payload() -> dict:
    # 4 critical, 3 high, 2 medium, 1 low — desc-sorted by churn_probability.
    customers = (
        [_cust("critical", i) for i in range(4)]
        + [_cust("high", 4 + i) for i in range(3)]
        + [_cust("medium", 7 + i) for i in range(2)]
        + [_cust("low", 9)]
    )
    return {"shop_domain": "shop-a.myshopify.com",
            "customers": customers, "currency": "EUR"}


def test_shape_slices_and_recomputes_summary_exactly():
    full = _full_payload()

    # Full (limit >= len): all 10, full band breakdown.
    out = customer_churn._shape(full, 500)
    assert out["total_customers_scored"] == 10
    assert out["by_risk_band"] == {"critical": 4, "high": 3,
                                   "medium": 2, "low": 1}
    assert out["currency"] == "EUR"
    assert out["shop_domain"] == "shop-a.myshopify.com"

    # limit=5 → exactly the desc-sorted top-5 (4 critical + 1 high),
    # summary recomputed over the slice — identical to a direct
    # score_shop_customers(limit=5) (same desc-sorted prefix).
    out5 = customer_churn._shape(full, 5)
    assert out5["total_customers_scored"] == 5
    assert out5["by_risk_band"] == {"critical": 4, "high": 1,
                                    "medium": 0, "low": 0}
    assert [c["risk_band"] for c in out5["customers"]] == (
        ["critical"] * 4 + ["high"])


def test_cache_hit_skips_the_heavy_query(client, monkeypatch):
    """The structural contract: a warm cache means score_shop_customers
    (the disk-sort-prone query) is NEVER invoked on the request path."""
    fastapi_app.dependency_overrides[require_pro_session] = (
        lambda: "shop-a.myshopify.com")

    monkeypatch.setattr(customer_churn, "_read_cached",
                        lambda shop: _full_payload())

    def _boom(*a, **k):
        raise AssertionError(
            "score_shop_customers ran on a cache hit — the heavy query "
            "is back on the per-request hot path (10k regression)")

    monkeypatch.setattr(customer_churn, "score_shop_customers", _boom)

    resp = client.get("/pro/customer-churn?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_customers_scored"] == 5
    assert body["by_risk_band"] == {"critical": 4, "high": 1,
                                    "medium": 0, "low": 0}
    fastapi_app.dependency_overrides.pop(require_pro_session, None)


def test_cache_miss_computes_then_writes_back(client, monkeypatch):
    fastapi_app.dependency_overrides[require_pro_session] = (
        lambda: "shop-a.myshopify.com")
    monkeypatch.setattr(customer_churn, "_read_cached", lambda shop: None)
    monkeypatch.setattr(customer_churn, "_acquire_lock", lambda shop: True)
    monkeypatch.setattr(customer_churn, "score_shop_customers",
                        lambda db, shop, limit: _full_payload()["customers"])
    monkeypatch.setattr(customer_churn, "get_shop_currency",
                        lambda db, shop: "EUR")

    written: dict = {}
    monkeypatch.setattr(customer_churn, "_write_cached",
                        lambda shop, payload: written.update(payload))

    resp = client.get("/pro/customer-churn?limit=50")
    assert resp.status_code == 200
    # The full top-N (not the sliced view) is what gets cached, so any
    # later limit can be served from one entry.
    assert len(written["customers"]) == 10
    assert written["currency"] == "EUR"
    assert resp.json()["total_customers_scored"] == 10
    fastapi_app.dependency_overrides.pop(require_pro_session, None)


def _churn_redis_clear(shop: str) -> None:
    from app.core.redis_client import _client
    rc = _client()
    if rc is not None:
        rc.delete(customer_churn._cache_key(shop),
                  customer_churn._lock_key(shop))


def test_cache_hit_is_zero_db_queries_via_real_redis(client, monkeypatch):
    """END-TO-END through REAL Redis (cache boundary NOT stubbed): the
    miss computes + writes real Redis; the hit is served from real
    Redis with X-Query-Count == 0. Zero queries ⟹ the lazy read dep
    never checks out a pooled connection. This is the proof the prior
    tests lacked (Agent finding #6)."""
    shop = "test-shop-a.myshopify.com"
    fastapi_app.dependency_overrides[require_pro_session] = lambda: shop
    _churn_redis_clear(shop)
    # Stub ONLY the heavy compute (the SQL plan is measured by the
    # harness, not here) — the cache path itself runs against real
    # Redis. get_shop_currency stubbed so the miss is deterministic.
    monkeypatch.setattr(customer_churn, "score_shop_customers",
                        lambda db, shop, limit: _full_payload()["customers"])
    monkeypatch.setattr(customer_churn, "get_shop_currency",
                        lambda db, shop: "EUR")
    try:
        miss = client.get("/pro/customer-churn?limit=50")
        assert miss.status_code == 200

        hit = client.get("/pro/customer-churn?limit=50")
        assert hit.status_code == 200
        # THE structural proof: a real-Redis cache hit does zero DB
        # work → the get_lazy_read_db proxy opens zero connections.
        assert hit.headers.get("X-Query-Count") == "0", (
            f"cache hit issued DB queries "
            f"(X-Query-Count={hit.headers.get('X-Query-Count')}) — the "
            f"heavy query / lazy session is back on the hot path")
        # Real Redis round-trip integrity (json.dumps(default=str) →
        # json.loads): the hit body equals the miss body exactly.
        assert hit.json() == miss.json()
        assert hit.json()["by_risk_band"] == {"critical": 4, "high": 3,
                                              "medium": 2, "low": 1}
    finally:
        _churn_redis_clear(shop)
        fastapi_app.dependency_overrides.pop(require_pro_session, None)


def test_wait_for_cache_returns_payload_when_builder_holds_lock(monkeypatch):
    """Covers the stampede wait-loop against REAL Redis (zero coverage
    before, Agent finding #6): a concurrent builder holds the lock and
    has written the cache → _wait_for_cache must return that payload
    rather than recompute."""
    shop = "test-shop-wait.myshopify.com"
    _churn_redis_clear(shop)
    try:
        customer_churn._write_cached(shop, _full_payload())
        got = customer_churn._wait_for_cache(shop)
        assert got is not None
        assert got["currency"] == "EUR"
        assert len(got["customers"]) == 10
    finally:
        _churn_redis_clear(shop)
