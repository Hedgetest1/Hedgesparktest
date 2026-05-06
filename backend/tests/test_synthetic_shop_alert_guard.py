"""Locks the synthetic-test-shop alert guard (2026-05-06).

Before this fix, services that opened their own SessionLocal()
inside a service path (e.g. risk_forecast._maybe_emit_volatility_
alert) bypassed test SAVEPOINTs and leaked rows into ops_alerts.
A capillary audit found 1079 orphan rows accumulated over 25 days,
540 from `webhook-fail.myshopify.com` alone. The class can re-emerge
any time someone runs the test suite that exercises a write_alert
nested-session path.

After the fix:
    - app/core/test_shop_blocklist.py::is_synthetic_test_shop
      pattern-matches known fixtures.
    - app/services/alerting.py::write_alert early-returns a stub
      OpsAlert (in-memory, never persisted) when the shop is
      synthetic.

Tests pin both ends of the contract: the predicate matches the
right patterns AND write_alert behaves correctly for both real
and synthetic shops.
"""
from __future__ import annotations

from app.core.test_shop_blocklist import is_synthetic_test_shop


# ---------------------------------------------------------------------------
# Predicate tests — documented patterns must match
# ---------------------------------------------------------------------------

def test_rforecast_pattern_matches():
    """test_risk_forecast.py::_shop generates `<prefix>-<8 hex>.myshopify.com`."""
    assert is_synthetic_test_shop("rforecast-rising-d6d5b0d8.myshopify.com") is True
    assert is_synthetic_test_shop("rforecast-falling-deadbeef.myshopify.com") is True
    assert is_synthetic_test_shop("rforecast-record-12345678.myshopify.com") is True


def test_webhook_fail_pattern_matches():
    """test_signal_webhooks.py:210 — fixed shop name."""
    assert is_synthetic_test_shop("webhook-fail.myshopify.com") is True


def test_loadtest_prefix_matches():
    """CLAUDE.md §12.2 mentions `_loadtest_*` shops."""
    assert is_synthetic_test_shop("_loadtest_beauty01.myshopify.com") is True
    assert is_synthetic_test_shop("_loadtest_anything") is True


def test_legacy_dev_shop_matches():
    assert is_synthetic_test_shop("legacy.myshopify.com") is True


def test_legitimate_test_shops_do_not_match():
    """REGRESSION GUARD (2026-05-06): the initial pattern set
    over-blocked any `test-*` / `fixture-*` / `lonely-*` / `empty-*` /
    `lone-*` / `pool-*` shop, breaking tests like
    test_onboarding::test_no_token_fails and
    test_merchant_chatbot::test_bug_creates_ops_alert which
    legitimately persist alerts inside the SAVEPOINT'd test session.
    These patterns must NOT match — alerts written there roll back
    cleanly with the test."""
    assert is_synthetic_test_shop("test-shop-a.myshopify.com") is False
    assert is_synthetic_test_shop("fixture-merchant.myshopify.com") is False
    assert is_synthetic_test_shop("lonely-shop.myshopify.com") is False
    assert is_synthetic_test_shop("empty-shop.myshopify.com") is False
    assert is_synthetic_test_shop("lone-beauty.myshopify.com") is False
    assert is_synthetic_test_shop("pool-a.myshopify.com") is False


# ---------------------------------------------------------------------------
# Predicate tests — real merchants must NOT match
# ---------------------------------------------------------------------------

def test_real_shop_names_do_not_match():
    assert is_synthetic_test_shop("merchant.myshopify.com") is False
    assert is_synthetic_test_shop("hedgespark-dev.myshopify.com") is False
    assert is_synthetic_test_shop("acme-store.myshopify.com") is False
    # Edge cases that look superficially like fixtures but are real:
    assert is_synthetic_test_shop("rforecast.myshopify.com") is False  # missing suffix-hex
    assert is_synthetic_test_shop("webhook-success.myshopify.com") is False


def test_none_and_empty_do_not_match():
    assert is_synthetic_test_shop(None) is False
    assert is_synthetic_test_shop("") is False
    assert is_synthetic_test_shop("   ") is False
    # Defensive: non-str input
    assert is_synthetic_test_shop(42) is False  # type: ignore[arg-type]


def test_case_insensitive_match():
    """Patterns lowercase the input — uppercase variations of the
    KNOWN-LEAK fixtures still match."""
    assert is_synthetic_test_shop("Webhook-FAIL.MyShopify.com") is True
    assert is_synthetic_test_shop("RFORECAST-RISING-AAAAAAAA.MYSHOPIFY.COM") is True


# ---------------------------------------------------------------------------
# write_alert integration — synthetic shop never persists
# ---------------------------------------------------------------------------

def test_write_alert_synthetic_shop_returns_stub_without_persist():
    """The integration contract: synthetic shop write_alert returns
    a typed OpsAlert object (so callers don't NoneType-error) but the
    object is never added to the DB session — id stays None."""
    from unittest.mock import MagicMock
    from app.services.alerting import write_alert

    fake_db = MagicMock()
    result = write_alert(
        fake_db,
        severity="info",
        source="test",
        alert_type="rars_volatility_projected",
        summary="should not persist",
        shop_domain="rforecast-rising-aaaaaaaa.myshopify.com",
    )
    # Returned a typed OpsAlert
    assert result is not None
    assert result.alert_type == "rars_volatility_projected"
    assert result.shop_domain == "rforecast-rising-aaaaaaaa.myshopify.com"
    # Never persisted: id is None (no flush) and db.add was never called
    assert result.id is None
    fake_db.add.assert_not_called()
    fake_db.flush.assert_not_called()


def test_write_alert_real_shop_still_persists(monkeypatch):
    """The flip side: a real-merchant alert still walks the full path."""
    from unittest.mock import MagicMock
    from app.services.alerting import write_alert

    fake_db = MagicMock()
    # Force dedup helpers to return None so write_alert reaches the persist path.
    monkeypatch.setattr("app.services.alerting._check_dedup", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.alerting._check_chronic", lambda *a, **kw: None)
    # Avoid calling out to Slack / Telegram / record_alert_telemetry.
    monkeypatch.setattr(
        "app.core.alert_delivery.deliver_alert_externally",
        lambda *a, **kw: False,
    )

    write_alert(
        fake_db,
        severity="info",
        source="test",
        alert_type="something_real",
        summary="should persist",
        shop_domain="acme-store.myshopify.com",
    )
    # db.add WAS called for real shop
    fake_db.add.assert_called()
    # at least one flush call (post-add)
    assert fake_db.flush.called
