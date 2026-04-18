"""
Tests for MA-6 Night Shift email — the moat-amplification inbox
receipt competitors can't publish.

Locks:
  - template renders without crashing across shapes (full, minimal, zero prevented)
  - send_for_shop idempotent per UTC day
  - opt-out flag suppresses send
  - missing contact_email skips gracefully
  - render_email recognizes "night_shift_digest" email_type
"""
from __future__ import annotations

import pytest

from app.models.merchant import Merchant


def _mk_merchant(db, shop: str, email: str | None, plan: str = "pro"):
    db.add(Merchant(
        shop_domain=shop,
        contact_email=email,
        plan=plan,
        billing_active=True,
        install_status="active",
        session_version=0,
    ))
    db.flush()


@pytest.fixture(autouse=True)
def _clean_redis():
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cur = 0
            while True:
                cur, keys = rc.scan(cursor=cur, match="hs:email_optout:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cur == 0:
                    break
            cur = 0
            while True:
                cur, keys = rc.scan(cursor=cur, match="hs:ns_email_sent:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cur == 0:
                    break
    except Exception:
        pass
    yield


class TestTemplate:
    def test_renders_full_shape(self):
        from app.services.email_templates import render_email
        ctx = {
            "shop_name": "Acme",
            "headline": "Prevented €850 this shift",
            "narrative": "Three signals caught before merchant impact.",
            "sleep_score": 86,
            "sleep_label": "Sleep well",
            "prevented_eur_24h": 850.0,
            "currency": "EUR",
            "rars_total": 1200.0,
            "top_action": {
                "source": "abandoned_high_intent",
                "narrative": "12 high-intent carts recoverable via nudge",
            },
            "journal": [
                {"signal": "rars.total", "verdict": "acted", "reason": "fired recovery nudge", "weight": 8},
                {"signal": "fusion.crosssignal", "verdict": "watched", "reason": "within tolerance", "weight": 0},
            ],
        }
        subject, html, plain = render_email("night_shift_digest", ctx)
        assert "Acme" in subject
        assert "Prevented €850" in subject or "Prevented" in subject or "shift" in subject
        assert "850" in html or "Prevented" in html
        assert "Open dashboard" in html
        assert "Acme" in plain or "dashboard" in plain.lower()

    def test_renders_minimal_shape(self):
        """Zero prevented, no RARS, empty journal — still renders."""
        from app.services.email_templates import render_email
        ctx = {
            "shop_name": "Quiet Shop",
            "headline": "Quiet night — nothing to report",
            "narrative": "",
            "prevented_eur_24h": 0,
            "currency": "USD",
            "journal": [],
        }
        subject, html, plain = render_email("night_shift_digest", ctx)
        assert "Quiet" in subject
        assert "dashboard" in html.lower()
        assert isinstance(plain, str) and len(plain) > 0


class TestSendForShop:
    def test_no_report_returns_skipped(self, db):
        from app.services.night_shift_email import send_for_shop
        _mk_merchant(db, "ns-email-1.myshopify.com", "owner@example.com")
        db.commit()
        r = send_for_shop(db, "ns-email-1.myshopify.com", {})
        assert r["status"] == "skipped"
        assert r["reason"] == "no_report"

    def test_no_contact_email_returns_skipped(self, db):
        from app.services.night_shift_email import send_for_shop
        _mk_merchant(db, "ns-email-2.myshopify.com", None)
        db.commit()
        r = send_for_shop(db, "ns-email-2.myshopify.com", {"headline": "x"})
        assert r["status"] == "skipped"
        assert r["reason"] == "no_contact_email"

    def test_opted_out_returns_skipped(self, db):
        from app.services.night_shift_email import send_for_shop, set_optout
        shop = "ns-email-3.myshopify.com"
        _mk_merchant(db, shop, "owner@example.com")
        db.commit()
        assert set_optout(shop, True) is True
        r = send_for_shop(db, shop, {"headline": "x"})
        assert r["status"] == "skipped"
        assert r["reason"] == "opted_out"

    def test_happy_path_sent(self, db):
        from app.services.night_shift_email import send_for_shop
        shop = "ns-email-4.myshopify.com"
        _mk_merchant(db, shop, "owner@example.com")
        db.commit()
        report = {
            "headline": "Prevented $42 last shift",
            "narrative": "One recovery action fired.",
            "prevented_eur_24h": 42.0,
            "currency": "USD",
            "journal": [
                {"signal": "rars.total", "verdict": "acted", "reason": "fired", "weight": 5},
            ],
        }
        r = send_for_shop(db, shop, report)
        assert r["status"] == "sent"
        assert r.get("intent_id")

    def test_second_call_same_day_dedupes(self, db):
        from app.services.night_shift_email import send_for_shop
        shop = "ns-email-5.myshopify.com"
        _mk_merchant(db, shop, "owner@example.com")
        db.commit()
        report = {"headline": "x", "prevented_eur_24h": 0, "currency": "USD", "journal": []}
        first = send_for_shop(db, shop, report)
        second = send_for_shop(db, shop, report)
        assert first["status"] == "sent"
        assert second["status"] == "skipped"
        assert second["reason"] == "already_sent_today"


class TestOptoutToggle:
    def test_optout_set_then_cleared(self):
        from app.services.night_shift_email import set_optout, _is_opted_out
        shop = "ns-email-toggle.myshopify.com"
        assert _is_opted_out(shop) is False
        assert set_optout(shop, True) is True
        assert _is_opted_out(shop) is True
        assert set_optout(shop, False) is True
        assert _is_opted_out(shop) is False
