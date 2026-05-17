"""Tests for event ingestion (POST /track)."""
import time

from sqlalchemy import text

from tests.conftest import SHOP_A, now_ms


def test_track_valid_event(client, merchant_a, db):
    """J3-part-2 contract (2026-05-17): a non-purchase analytics event
    is ASYNC-BUFFERED, NOT synchronously INSERTed. POST → 200, ZERO
    request DB write to `events`, the event sits in the Redis ingest
    buffer (a singleton drain thread bulk-INSERTs it). This is what
    makes the 10k pool-cascade structurally impossible for the
    dominant write volume — the old synchronous-row contract is GONE
    BY DESIGN for analytics (purchases stay synchronous — see
    test_track_purchase_stays_synchronous)."""
    from app.core.redis_client import cache_set, _client
    cache_set(f"hs:known_shop:{SHOP_A}", True, 60)
    rc = _client()
    rc.delete("hs:ingest:buf")  # isolate (Redis is not SAVEPOINT-scoped)
    try:
        payload = {
            "shop_domain": SHOP_A,
            "visitor_id": "test_vid_001",
            "event_type": "product_view",
            "product_url": "/products/test-widget",
            "timestamp": now_ms(),
        }
        resp = client.post("/track", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # NEW contract: NOT synchronously in `events` (it is buffered).
        row = db.execute(
            text("SELECT 1 FROM events WHERE shop_domain = :s AND "
                 "visitor_id = 'test_vid_001'"),
            {"s": SHOP_A},
        ).fetchone()
        assert row is None, (
            "non-purchase event was synchronously INSERTed — the "
            "J3-part-2 async buffer is bypassed, the 10k pool-cascade "
            "is back")

        # It IS in the ingest buffer with the right fields.
        import json
        buffered = [json.loads(x) for x in rc.lrange("hs:ingest:buf", 0, -1)]
        mine = [b for b in buffered if b.get("visitor_id") == "test_vid_001"]
        assert len(mine) == 1
        assert mine[0]["event_type"] == "product_view"
        assert mine[0]["shop_domain"] == SHOP_A
    finally:
        rc.delete("hs:ingest:buf")


def test_track_rejects_invalid_shop_domain(client):
    """Non-myshopify.com domain → 400."""
    payload = {
        "shop_domain": "evil-site.com",
        "visitor_id": "test_vid_002",
        "event_type": "product_view",
    }
    resp = client.post("/track", json=payload)
    assert resp.status_code == 400


def test_track_rejects_invalid_event_type(client, merchant_a):
    """Unknown event_type → 400."""
    payload = {
        "shop_domain": SHOP_A,
        "visitor_id": "test_vid_003",
        "event_type": "hacked_event",
    }
    resp = client.post("/track", json=payload)
    assert resp.status_code == 400


def test_track_rejects_unknown_shop(client):
    """Event for a shop that never installed → 400."""
    payload = {
        "shop_domain": "unknown-shop.myshopify.com",
        "visitor_id": "test_vid_004",
        "event_type": "product_view",
    }
    resp = client.post("/track", json=payload)
    assert resp.status_code == 400
