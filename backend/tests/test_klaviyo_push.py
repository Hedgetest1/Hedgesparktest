"""Tests for Klaviyo intent push eligibility, dedup, and gating."""
import os
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.time_utils import utc_now_naive
from app.models.merchant import Merchant
from app.models.opportunity_signal import OpportunitySignal
from app.core.token_crypto import encrypt_token
from tests.conftest import SHOP_A


def _setup_connected_merchant(db: Session) -> Merchant:
    """Create a merchant with a connected Klaviyo key."""
    m = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    if not m:
        m = Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active")
        db.add(m)
        db.flush()
    m.encrypted_klaviyo_key = encrypt_token("pk_test_klaviyo_12345")
    m.klaviyo_connection_status = "connected"
    db.flush()
    return m


def _insert_signal(db: Session, signal_type: str, confidence: str, strength: float):
    """Insert a test signal for SHOP_A."""
    now = utc_now_naive()
    db.add(OpportunitySignal(
        shop_domain=SHOP_A,
        product_url="/products/test-product",
        signal_type=signal_type,
        signal_strength=strength,
        signal_confidence=confidence,
        explanation="test",
        detected_at=now,
        refreshed_at=now,
        expires_at=now + timedelta(hours=24),
    ))
    db.flush()


def test_low_confidence_excluded(db, merchant_a):
    """Low-confidence (early) signals must never trigger Klaviyo push."""
    _setup_connected_merchant(db)
    # Scrub any leaked recent signals from earlier test runs in the
    # shared DB — the fixture only rolls back its own transaction, but
    # earlier tests' commits (e.g. test_strong_signal_qualifies) may
    # leave qualifying rows inside the 15-min freshness window.
    db.execute(text(
        "DELETE FROM opportunity_signals WHERE shop_domain = :s"
    ), {"s": SHOP_A})
    _insert_signal(db, "EARLY_BROWSING_NO_CART", "low", 0.15)
    db.commit()

    from app.services.klaviyo_export import push_intent_signals_to_klaviyo
    result = push_intent_signals_to_klaviyo(db, SHOP_A)
    assert result["pushed"] == 0
    assert result["signals"] == 0  # low confidence filtered out of qualifying signals


def test_strong_signal_qualifies(db, merchant_a):
    """High-confidence signal with strength >= 0.4 qualifies for push."""
    _setup_connected_merchant(db)
    _insert_signal(db, "HIGH_TRAFFIC_NO_CART", "high", 0.55)
    db.commit()

    from app.services.klaviyo_export import push_intent_signals_to_klaviyo

    # Mock the external Klaviyo API call and segment_product_visitors
    with patch("app.services.klaviyo_export.segment_product_visitors") as mock_seg, \
         patch("app.services.klaviyo_export.httpx.post") as mock_post:

        # Return a segment with one warm-top visitor
        mock_seg.return_value = {
            "hot": {"visitors": []},
            "warm": {"visitors": [
                {"visitor_id": "v_test_1", "behavioral_index": 0.45,
                 "avg_scroll": 80.0, "avg_dwell_secs": 25.0, "visit_count": 2}
            ]},
            "cold": {"visitors": []},
        }
        mock_post.return_value = type("Resp", (), {"status_code": 202, "raise_for_status": lambda self: None})()

        result = push_intent_signals_to_klaviyo(db, SHOP_A)
        assert result["signals"] >= 1


def test_anon_blocked_in_production(db, merchant_a):
    """In production mode (ALLOW_INSECURE_DEV unset), anon visitors are skipped."""
    _setup_connected_merchant(db)
    _insert_signal(db, "HIGH_ENGAGEMENT_NO_ACTION", "high", 0.60)
    db.flush()

    # Clear any dedup keys from prior tests
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            keys = []
            cursor = 0
            while True:
                cursor, batch = rc.scan(cursor, match="hs:kpush:*", count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            if keys:
                rc.delete(*keys)
    except Exception:
        pass

    old_val = os.environ.pop("ALLOW_INSECURE_DEV", None)
    try:
        from app.services.klaviyo_export import push_intent_signals_to_klaviyo

        with patch("app.services.klaviyo_export.segment_product_visitors") as mock_seg, \
             patch("app.services.klaviyo_export._resolve_visitor_emails", return_value={}):

            mock_seg.return_value = {
                "hot": {"visitors": [
                    {"visitor_id": "v_anon_1", "behavioral_index": 0.60,
                     "avg_scroll": 90.0, "avg_dwell_secs": 30.0, "visit_count": 3}
                ]},
                "warm": {"visitors": []},
                "cold": {"visitors": []},
            }

            result = push_intent_signals_to_klaviyo(db, SHOP_A)
            # Anon visitor found but not pushed (no email, production mode)
            assert result["pushed"] == 0
            assert result["anonymous"] >= 1
    finally:
        if old_val is not None:
            os.environ["ALLOW_INSECURE_DEV"] = old_val


def test_no_key_returns_skipped(db, merchant_a):
    """Merchant without Klaviyo key → skipped, not errored."""
    # merchant_a has no Klaviyo key by default
    _insert_signal(db, "HIGH_TRAFFIC_NO_CART", "high", 0.50)
    db.commit()

    from app.services.klaviyo_export import push_intent_signals_to_klaviyo
    result = push_intent_signals_to_klaviyo(db, SHOP_A)
    assert result.get("skipped") == "no_key"
    assert result["pushed"] == 0
    assert result["errors"] == 0


def test_forward_event_uses_shop_currency_not_hardcoded_eur(db, merchant_a):
    """Regression-pin for cosmetic(currency) commit f91dfe7.

    Before fix, forward_event_sync hardcoded `value_currency="EUR"` in
    every Klaviyo event payload, regardless of merchant currency.
    USD/GBP/JPY merchants saw their LTV math + segment thresholds
    corrupted in their Klaviyo accounts. This test seeds a USD shop
    and verifies the payload tags `value_currency: "USD"`."""
    m = _setup_connected_merchant(db)
    m.primary_currency = "USD"
    db.flush()
    db.commit()

    from app.services.klaviyo_events import forward_event_sync

    captured: dict = {}

    class FakeResponse:
        status_code = 202
        text = ""

        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, content=None, timeout=None):
        captured["url"] = url
        captured["body"] = content
        return FakeResponse()

    with patch("app.services.klaviyo_events.httpx.post", side_effect=fake_post):
        ok, reason = forward_event_sync(
            db,
            shop_domain=SHOP_A,
            event_name="goal_at_risk",
            email="merchant@test.com",
            properties={"goal": "monthly_revenue"},
            revenue=199.99,
        )

    assert ok, f"forward_event_sync failed: {reason}"
    assert captured.get("body"), "no payload sent to Klaviyo"

    import json as _json
    payload = _json.loads(captured["body"])
    attrs = payload["data"]["attributes"]
    assert attrs.get("value") == 199.99
    assert attrs.get("value_currency") == "USD", (
        f"value_currency must be USD for USD merchant, got "
        f"{attrs.get('value_currency')!r}"
    )
