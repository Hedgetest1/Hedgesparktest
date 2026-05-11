"""Security regression tests for POST /track/purchase-confirmed.

Born 2026-05-11 Sprint A security audit — Agent flagged C1 (CRITICAL):
the endpoint was previously open to cross-tenant attribution forgery.
Anyone with a `*.myshopify.com` domain string could overwrite
attribution rows for any installed merchant.

Defense in depth: 4 guards layered, this file tests each independently:
  1. shop_domain format (regex *.myshopify.com)
  2. _is_known_shop (shop must be installed)
  3. Per-IP+shop rate limit (60/60s)
  4. Visitor plausibility (visitor_id has ≥1 event for shop in 90d)
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

# `client` and `db` fixtures provided by tests/conftest.py — `client`
# overrides FastAPI's get_db so the test transaction is shared between
# fixture writes and TestClient requests.


@pytest.fixture
def known_shop_with_events(db):
    """Insert a Merchant + 1 Events row so the shop passes both
    `_is_known_shop` and visitor-plausibility guards. Uses db.flush()
    not commit() so SAVEPOINT cleanup still rolls back at end-of-test."""
    shop = "track-security-test.myshopify.com"
    visitor_id = "vis_security_test_abc123"

    # Invalidate Redis known-shop cache BOTH before fixture insert (in
    # case a prior test cached "not installed") AND after (so the new
    # insert takes immediate effect for the current request).
    def _purge_redis_cache():
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                rc.delete(f"hs:known_shop:{shop}")
        except Exception:
            pass

    _purge_redis_cache()

    db.execute(text("""
        INSERT INTO merchants
          (shop_domain, install_status, installed_at,
           access_token, plan, onboarding_status)
        VALUES
          (:s, 'active', now(), 'test_token', 'lite', 'ready')
        ON CONFLICT (shop_domain) DO UPDATE
          SET install_status = 'active'
    """), {"s": shop})
    # `events` is RANGE-partitioned on `timestamp` (bigint epoch ms);
    # use current epoch ms so the partition routing finds the right
    # partition.
    import time as _t
    db.execute(text("""
        INSERT INTO events
          (shop_domain, visitor_id, event_type, timestamp)
        VALUES
          (:s, :v, 'page_view', :ts)
    """), {"s": shop, "v": visitor_id, "ts": int(_t.time() * 1000)})
    db.flush()

    _purge_redis_cache()

    yield shop, visitor_id

    _purge_redis_cache()


# ---------------------------------------------------------------------------
# Guard #1: shop_domain format
# ---------------------------------------------------------------------------


def test_rejects_invalid_shop_domain_format(client):
    """Non-*.myshopify.com shop_domain → HTTP 400."""
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": "evil.com",
        "visitor_id": "vis_x",
        "shopify_order_id": "order_x",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 400
    assert "shop_domain" in response.text.lower()


def test_rejects_empty_visitor_id(client):
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": "anyshop.myshopify.com",
        "visitor_id": "   ",
        "shopify_order_id": "order_x",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 400
    assert "visitor_id" in response.text.lower()


def test_rejects_empty_order_id(client):
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": "anyshop.myshopify.com",
        "visitor_id": "vis_x",
        "shopify_order_id": "  ",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 400
    assert "shopify_order_id" in response.text.lower()


# ---------------------------------------------------------------------------
# Guard #2: shop must be installed (_is_known_shop)
# ---------------------------------------------------------------------------


def test_rejects_unknown_shop(client):
    """A well-formed but never-installed shop_domain → HTTP 404.
    Closes the C1 cross-tenant attribution forgery vector — an attacker
    enumerating shop_domains will hit 404 on every uninstalled one."""
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": "definitely-not-installed.myshopify.com",
        "visitor_id": "vis_x",
        "shopify_order_id": "order_x",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 404
    assert "installed" in response.text.lower() or "merchant" in response.text.lower()


# ---------------------------------------------------------------------------
# Guard #4: visitor plausibility (no events → reject as forgery)
# ---------------------------------------------------------------------------


def test_rejects_visitor_with_no_events_for_shop(client, known_shop_with_events):
    """An installed shop, well-formed payload, but a visitor_id that has
    NO events for this shop → HTTP 422. Closes the "valid shop_domain
    but forged visitor session" vector."""
    shop, _ = known_shop_with_events
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": shop,
        "visitor_id": "vis_NEVER_SEEN_BEFORE",
        "shopify_order_id": "order_x",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 422
    assert "visitor" in response.text.lower()


# ---------------------------------------------------------------------------
# Happy path: legitimate attribution writes correctly
# ---------------------------------------------------------------------------


def test_legitimate_attribution_writes_row(client, known_shop_with_events, db):
    """Installed shop + visitor with prior events + unique order_id →
    HTTP 200 + status=ok + row in visitor_purchase_sessions."""
    shop, visitor_id = known_shop_with_events
    order_id = "order_legit_test_xyz"

    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": shop,
        "visitor_id": visitor_id,
        "shopify_order_id": order_id,
        "timestamp": 1715000000000,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Verify row written
    row = db.execute(text("""
        SELECT shop_domain, visitor_id, shopify_order_id
          FROM visitor_purchase_sessions
         WHERE shopify_order_id = :o
    """), {"o": order_id}).fetchone()
    assert row is not None
    assert row.shop_domain == shop
    assert row.visitor_id == visitor_id


def test_duplicate_order_id_returns_duplicate_status(
    client, known_shop_with_events,
):
    """Same order_id submitted twice → second response = duplicate.
    UNIQUE constraint on shopify_order_id catches it."""
    shop, visitor_id = known_shop_with_events
    order_id = "order_dup_test_xyz"

    payload = {
        "shop_domain": shop,
        "visitor_id": visitor_id,
        "shopify_order_id": order_id,
        "timestamp": 1715000000000,
    }

    r1 = client.post("/track/purchase-confirmed", json=payload)
    r2 = client.post("/track/purchase-confirmed", json=payload)

    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


# ---------------------------------------------------------------------------
# Guard #3: per-IP+shop rate limit
# ---------------------------------------------------------------------------
# Note: testing the rate limit requires Redis interaction; we monkeypatch
# the _check_per_shop_rate helper to test the wired-in behavior without
# needing 61 real Redis INCR calls.


def test_rate_limit_returns_429(client, known_shop_with_events, monkeypatch):
    """When _check_per_shop_rate returns False (rate exceeded) → HTTP 429."""
    from app.api import track_purchase as tp

    monkeypatch.setattr(tp, "_check_per_shop_rate", lambda *a, **kw: False)

    shop, visitor_id = known_shop_with_events
    response = client.post("/track/purchase-confirmed", json={
        "shop_domain": shop,
        "visitor_id": visitor_id,
        "shopify_order_id": "order_rate_test",
        "timestamp": 1715000000000,
    })
    assert response.status_code == 429
    assert "rate" in response.text.lower() or "retry" in response.text.lower()


def test_rate_limit_helper_counts_correctly(monkeypatch):
    """The rate-limit helper itself: 60 calls pass, 61st blocked.
    Mocks Redis to avoid hitting the real instance with 61 INCRs."""
    from app.api import track_purchase as tp

    counter = {"n": 0}

    class _FakeRedis:
        def incr(self, key):
            counter["n"] += 1
            return counter["n"]
        def expire(self, key, ttl):
            pass

    monkeypatch.setattr(tp, "_redis_client", lambda: _FakeRedis())

    class _FakeReq:
        client = type("C", (), {"host": "1.2.3.4"})()
        headers = {}
    req = _FakeReq()

    # 60 calls should pass (count goes 1..60)
    for i in range(60):
        assert tp._check_per_shop_rate(req, "shop.myshopify.com") is True
    # 61st call should return False (count=61, > 60)
    assert tp._check_per_shop_rate(req, "shop.myshopify.com") is False
