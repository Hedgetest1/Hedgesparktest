"""Tests for event ingestion (POST /track)."""
import time

from sqlalchemy import text

from tests.conftest import SHOP_A, now_ms


def test_track_valid_event(client, merchant_a, db):
    """Valid product_view event → 200 + event persisted in DB."""
    # Pre-seed Redis known-shop cache so _is_known_shop() finds the merchant
    # (the merchant exists in the test transaction, not visible to production SessionLocal)
    try:
        from app.core.redis_client import cache_set
        cache_set(f"hs:known_shop:{SHOP_A}", True, 60)
    except Exception:
        pass

    payload = {
        "shop_domain": SHOP_A,
        "visitor_id": "test_vid_001",
        "event_type": "product_view",
        "product_url": "/products/test-widget",
        "timestamp": now_ms(),
    }
    resp = client.post("/track", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"

    # Verify event in DB
    row = db.execute(
        text("SELECT visitor_id, event_type, product_url FROM events WHERE shop_domain = :s ORDER BY id DESC LIMIT 1"),
        {"s": SHOP_A},
    ).fetchone()
    assert row is not None
    assert row[0] == "test_vid_001"
    assert row[1] == "product_view"


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
