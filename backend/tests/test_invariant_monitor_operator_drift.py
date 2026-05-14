"""Locks the `_check_operator_shop_drift` invariant in
`app/services/invariant_monitor.py` (born 2026-05-14).

The check fires invariant_regression CRITICAL when the merchants table
contains a shop matching operator-class signals (`@hedgesparkhq.com`
contact email OR `hedgespark-` shop_domain prefix) that is NOT declared
in `_OPERATOR_DEV_SHOPS`. The class was discovered after
`hedgespark-smoke.myshopify.com` accumulated 25+ noise alerts over 702h
because it was a real-merchant row but missing from the operator list.

These tests use the live DB in SAVEPOINT mode (per project doctrine).
"""
from __future__ import annotations

import json

from sqlalchemy import text

from app.services.invariant_monitor import _check_operator_shop_drift


def test_returns_clean_when_all_operator_shops_declared(db):
    """Baseline: with hedgespark-dev + hedgespark-smoke in the
    declaration AND the live merchants table containing only those
    two operator-class shops, the check writes NO alert."""
    summary: dict = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_operator_shop_drift(db, summary)
    assert summary["checked"] == 1
    assert summary["failed"] == 0, (
        "drift detected — likely a new operator-pattern shop landed in "
        "DB without _OPERATOR_DEV_SHOPS declaration. Check /ops/system-"
        "health for the active alert."
    )
    assert summary["alerts_written"] == 0


def test_fires_alert_when_undeclared_operator_shop_exists(db):
    """Insert a synthetic operator-pattern shop (not in
    _OPERATOR_DEV_SHOPS) and confirm the check writes a CRITICAL
    invariant_regression alert. The SAVEPOINT rolls back the synthetic
    shop after the test."""
    rogue_domain = "hedgespark-_test_invariant_drift.myshopify.com"
    db.execute(text(
        """
        INSERT INTO merchants
            (shop_domain, plan, billing_active, install_status,
             contact_email, installed_at)
        VALUES
            (:d, 'pro', true, 'active',
             '_test_drift@hedgesparkhq.com', NOW())
        """
    ), {"d": rogue_domain})
    db.flush()

    summary: dict = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_operator_shop_drift(db, summary)

    assert summary["checked"] == 1
    assert summary["failed"] == 1
    assert summary["alerts_written"] == 1

    # Verify the alert names the rogue shop in detail.
    alert = db.execute(text(
        """
        SELECT severity, alert_type, source, summary, detail
        FROM ops_alerts
        WHERE source = 'invariant:operator_shop_drift'
        ORDER BY created_at DESC
        LIMIT 1
        """
    )).first()
    assert alert is not None
    assert alert.severity == "critical"
    assert alert.alert_type == "invariant_regression"
    # `detail` is stored as JSON text (TEXT column with JSON-serialized
    # payload). Parse and verify the rogue shop is reported.
    detail = (
        json.loads(alert.detail)
        if isinstance(alert.detail, str) else (alert.detail or {})
    )
    drifted_domains = [
        s.get("shop_domain") for s in detail.get("drifted_shops", [])
    ]
    assert rogue_domain in drifted_domains, (
        f"rogue shop {rogue_domain} not in alert detail: {drifted_domains}"
    )


def test_email_only_match_also_triggers(db):
    """A shop that does NOT match the `hedgespark-` prefix but DOES
    have an `@hedgesparkhq.com` contact_email must still trigger —
    operator-class detection is OR over both signals."""
    rogue_domain = "_test_email_drift.myshopify.com"
    db.execute(text(
        """
        INSERT INTO merchants
            (shop_domain, plan, billing_active, install_status,
             contact_email, installed_at)
        VALUES
            (:d, 'pro', true, 'active',
             '_test2@hedgesparkhq.com', NOW())
        """
    ), {"d": rogue_domain})
    db.flush()

    summary: dict = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_operator_shop_drift(db, summary)
    assert summary["failed"] == 1, (
        "operator detection must match on contact_email too, not just "
        "shop_domain prefix"
    )


def test_real_merchant_with_random_email_does_not_trigger(db):
    """A shop without operator signals must NOT trigger the check —
    no false positive on regular merchants."""
    rogue_domain = "_test_real_merchant.myshopify.com"
    db.execute(text(
        """
        INSERT INTO merchants
            (shop_domain, plan, billing_active, install_status,
             contact_email, installed_at)
        VALUES
            (:d, 'pro', true, 'active',
             'owner@somecompany.com', NOW())
        """
    ), {"d": rogue_domain})
    db.flush()

    summary: dict = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_operator_shop_drift(db, summary)
    assert summary["failed"] == 0, (
        "false positive: operator-drift fired on a non-operator shop"
    )
