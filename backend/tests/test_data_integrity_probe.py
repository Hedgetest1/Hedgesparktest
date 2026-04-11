"""
Tests for the Phase-3 semantic drift probe.

The probe detects silent data corruption that backend-error-only self-healing
cannot catch: attribution collapse, order volume collapse, AOV drift, and
nudge lift decay. Each check is verified in isolation, then the full
pipeline (probe → ops_alert → triage Rule 6 → BugFixCandidate) is covered.

Tests use the production-schema SAVEPOINT fixture so shop_orders,
visitor_purchase_sessions, active_nudges, nudge_events are all available.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.models.merchant import Merchant
from app.models.ops_alert import OpsAlert
from app.models.shop_order import ShopOrder
from app.services.bugfix_pipeline import run_bug_triage
from app.services.data_integrity_probe import (
    _ATTRIBUTION_DROP_PP,
    _check_aov_drift,
    _check_attribution_drift,
    _check_order_collapse,
    run_probe,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_merchant(db, shop: str) -> Merchant:
    m = Merchant(
        shop_domain=shop,
        plan="pro",
        billing_active=True,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()
    return m


def _mk_order(db, shop: str, days_ago: int, price: float = 100.0,
              suffix: str = "") -> ShopOrder:
    """Create a minimal shop_order row. `suffix` keeps shopify_order_id unique."""
    o = ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"gid://{shop}/order/{days_ago}_{price}_{suffix}",
        total_price=price,
        currency="USD",
        line_items=[],
        created_at=_now() - timedelta(days=days_ago),
    )
    db.add(o)
    db.flush()
    return o


# ---------------------------------------------------------------------------
# Attribution drift
# ---------------------------------------------------------------------------

def test_attribution_drift_flags_sharp_drop(db):
    """Baseline 80% attributed → recent 20% attributed = 60pp drop."""
    shop = "drift-shop-1.myshopify.com"
    _mk_merchant(db, shop)

    # Baseline: 40 orders in days 8-30, 32 attributed (80%)
    from app.models.visitor_purchase_session import VisitorPurchaseSession
    for i in range(40):
        o = _mk_order(db, shop, days_ago=15, price=100.0, suffix=f"base_{i}")
        if i < 32:  # attribute 80%
            db.add(VisitorPurchaseSession(
                shop_domain=shop,
                visitor_id=f"v_base_{i}",
                shopify_order_id=o.shopify_order_id,
                confirmed_at=o.created_at,
            ))

    # Recent: 40 orders in days 0-7, only 8 attributed (20%)
    for i in range(40):
        o = _mk_order(db, shop, days_ago=3, price=100.0, suffix=f"rec_{i}")
        if i < 8:
            db.add(VisitorPurchaseSession(
                shop_domain=shop,
                visitor_id=f"v_rec_{i}",
                shopify_order_id=o.shopify_order_id,
                confirmed_at=o.created_at,
            ))
    db.flush()

    finding = _check_attribution_drift(db, shop)
    assert finding is not None
    assert finding.check == "attribution_drift"
    assert finding.severity == "critical"  # drop > 20pp
    assert finding.detail["drop_pp"] >= _ATTRIBUTION_DROP_PP
    assert "drop" in finding.summary.lower() or "dropped" in finding.summary.lower()


def test_attribution_drift_silent_when_stable(db):
    """Same rate in both windows → no finding."""
    shop = "stable-shop.myshopify.com"
    _mk_merchant(db, shop)

    from app.models.visitor_purchase_session import VisitorPurchaseSession
    for window_days, suffix in ((15, "base"), (3, "rec")):
        for i in range(40):
            o = _mk_order(db, shop, days_ago=window_days, suffix=f"{suffix}_{i}")
            if i < 30:  # 75% both windows
                db.add(VisitorPurchaseSession(
                    shop_domain=shop,
                    visitor_id=f"v_{suffix}_{i}",
                    shopify_order_id=o.shopify_order_id,
                    confirmed_at=o.created_at,
                ))
    db.flush()

    assert _check_attribution_drift(db, shop) is None


def test_attribution_drift_skips_low_volume(db):
    """Below the min sample threshold → skip, no false positive."""
    shop = "tiny-shop.myshopify.com"
    _mk_merchant(db, shop)
    # Only 5 orders total → below _MIN_ORDERS_FOR_ATTRIBUTION
    for i in range(5):
        _mk_order(db, shop, days_ago=15, suffix=f"tiny_{i}")
    db.flush()

    assert _check_attribution_drift(db, shop) is None


# ---------------------------------------------------------------------------
# Order collapse
# ---------------------------------------------------------------------------

def test_order_collapse_detected(db):
    """Baseline 10/day → recent 1/day = 90% drop."""
    shop = "collapse-shop.myshopify.com"
    _mk_merchant(db, shop)

    # Baseline: 230 orders over days 8-30 (~10/day)
    for i in range(230):
        day = 8 + (i % 23)
        _mk_order(db, shop, days_ago=day, suffix=f"base_{i}")
    # Recent: 7 orders over 7 days (1/day)
    for i in range(7):
        _mk_order(db, shop, days_ago=i, suffix=f"rec_{i}")
    db.flush()

    finding = _check_order_collapse(db, shop)
    assert finding is not None
    assert finding.check == "order_collapse"
    assert finding.detail["ratio"] < 0.5
    assert finding.severity in ("warning", "critical")


def test_order_collapse_silent_when_flat(db):
    """Same volume both windows → no finding."""
    shop = "flat-shop.myshopify.com"
    _mk_merchant(db, shop)
    for day in range(0, 30):
        for i in range(5):
            _mk_order(db, shop, days_ago=day, suffix=f"flat_{day}_{i}")
    db.flush()
    assert _check_order_collapse(db, shop) is None


# ---------------------------------------------------------------------------
# AOV drift
# ---------------------------------------------------------------------------

def test_aov_spike_detected(db):
    """Baseline AOV = 100, recent AOV = 300 → 3x spike."""
    shop = "aov-spike.myshopify.com"
    _mk_merchant(db, shop)
    # Baseline: 30 orders @ 100 in days 8-30
    for i in range(30):
        _mk_order(db, shop, days_ago=15, price=100.0, suffix=f"aov_base_{i}")
    # Recent: 30 orders @ 300 in days 0-7
    for i in range(30):
        _mk_order(db, shop, days_ago=3, price=300.0, suffix=f"aov_rec_{i}")
    db.flush()

    finding = _check_aov_drift(db, shop)
    assert finding is not None
    assert finding.check == "aov_drift"
    assert finding.detail["direction"] == "spike"
    assert finding.detail["ratio"] > 1.25


def test_aov_stable_no_finding(db):
    shop = "aov-stable.myshopify.com"
    _mk_merchant(db, shop)
    for days_ago, suffix in ((15, "base"), (3, "rec")):
        for i in range(30):
            _mk_order(db, shop, days_ago=days_ago, price=100.0, suffix=f"{suffix}_{i}")
    db.flush()
    assert _check_aov_drift(db, shop) is None


# ---------------------------------------------------------------------------
# End-to-end: probe → ops_alert → triage → BugFixCandidate
# ---------------------------------------------------------------------------

def test_probe_writes_semantic_drift_alert_and_triage_promotes_to_candidate(db):
    """Full flow: silent corruption → alert → candidate. The essential test."""
    shop = "e2e-drift.myshopify.com"
    _mk_merchant(db, shop)

    # Set up an attribution collapse scenario
    from app.models.visitor_purchase_session import VisitorPurchaseSession
    for i in range(40):
        o = _mk_order(db, shop, days_ago=15, suffix=f"e2e_base_{i}")
        if i < 35:  # 87.5% baseline
            db.add(VisitorPurchaseSession(
                shop_domain=shop, visitor_id=f"vb_{i}",
                shopify_order_id=o.shopify_order_id, confirmed_at=o.created_at,
            ))
    for i in range(40):
        o = _mk_order(db, shop, days_ago=3, suffix=f"e2e_rec_{i}")
        if i < 4:  # 10% recent
            db.add(VisitorPurchaseSession(
                shop_domain=shop, visitor_id=f"vr_{i}",
                shopify_order_id=o.shopify_order_id, confirmed_at=o.created_at,
            ))
    db.flush()

    # Run probe
    result = run_probe(db, max_shops=10)
    db.flush()

    assert result.checks_run > 0
    assert any(f.shop_domain == shop and f.check == "attribution_drift"
               for f in result.findings)

    # Verify an ops_alert was written for this specific shop/check
    expected_source = f"probe:attribution_drift:{shop}"
    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "semantic_drift",
            OpsAlert.source == expected_source,
        )
        .first()
    )
    assert alert is not None

    # Now run the triage — Rule 6 should promote this alert to a candidate
    run_bug_triage(db)

    c = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "semantic_drift",
            BugFixCandidate.source_ref == expected_source,
        )
        .first()
    )
    assert c is not None
    assert "Semantic drift" in c.title
    assert c.status == "open"
