"""
Phase Ω killer #1 — anomaly fusion tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.shop_order import ShopOrder
from app.models.ops_alert import OpsAlert
from app.services.anomaly_fusion import (
    AtomicSignal,
    FusionAlert,
    fuse,
    _classify_severity,
    _signal_revenue_drop,
    _signal_anomaly_volume,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


SHOP = "fusion-test.myshopify.com"


def test_classify_severity_thresholds():
    assert _classify_severity(80) == "critical"
    assert _classify_severity(60) == "warning"
    assert _classify_severity(20) == "info"


def test_atomic_signal_dataclass():
    s = AtomicSignal(name="x", severity=0.5, value=1, baseline=2, delta_pct=-50, window_hours=24)
    assert s.severity == 0.5
    assert s.detail == {}


def test_fusion_alert_to_dict():
    a = FusionAlert(
        pattern="demand_softening",
        fusion_score=80.0,
        severity="critical",
        contributors=[],
        window_hours=24,
        recommended_action="x",
        narrative="y",
        detected_at="now",
    )
    d = a.to_dict()
    assert d["pattern"] == "demand_softening"
    assert d["fusion_score"] == 80.0


def _plant_revenue(db, shop, recent_revenue, baseline_per_day):
    """Plant 1 order today + 7 days of baseline orders."""
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"gid://{shop}/o/today",
        total_price=recent_revenue,
        currency="EUR",
        line_items=[],
        created_at=_now() - timedelta(hours=2),
    ))
    for d in range(1, 8):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"gid://{shop}/o/d{d}",
            total_price=baseline_per_day,
            currency="EUR",
            line_items=[],
            created_at=_now() - timedelta(days=d, hours=12),
        ))
    db.flush()


def test_signal_revenue_drop_fires_when_below_baseline(db):
    _plant_revenue(db, SHOP, recent_revenue=10.0, baseline_per_day=100.0)
    s = _signal_revenue_drop(db, SHOP)
    assert s is not None
    assert s.name == "revenue_drop_24h"
    assert s.delta_pct < -50  # well below baseline
    assert s.severity > 0.5


def test_signal_revenue_drop_silent_when_normal(db):
    _plant_revenue(db, SHOP, recent_revenue=100.0, baseline_per_day=100.0)
    s = _signal_revenue_drop(db, SHOP)
    assert s is None  # within noise floor


def test_signal_anomaly_volume_fires(db):
    # 5 alerts in last 24h, 1 in prior week
    db.add(OpsAlert(
        shop_domain=None,  # global alerts also count
        source="x", alert_type="t", severity="warning", summary="x",
        detail=None, created_at=_now() - timedelta(days=5),
    ))
    for i in range(5):
        db.add(OpsAlert(
            shop_domain=SHOP, source="x", alert_type="t", severity="warning",
            summary="recent", detail=None, created_at=_now() - timedelta(hours=1 + i),
        ))
    db.flush()
    s = _signal_anomaly_volume(db, SHOP)
    assert s is not None
    assert s.delta_pct > 50


def test_fuse_returns_alerts_when_pattern_matches(db):
    # Plant a strong revenue drop — should fire the fallback general_revenue_dip
    _plant_revenue(db, SHOP, recent_revenue=5.0, baseline_per_day=100.0)
    out = fuse(db, SHOP)
    assert out["shop_domain"] == SHOP
    assert "alerts" in out
    assert len(out["alerts"]) >= 1
    patterns = [a["pattern"] for a in out["alerts"]]
    assert "general_revenue_dip" in patterns
    # Sorted descending by score
    scores = [a["fusion_score"] for a in out["alerts"]]
    assert scores == sorted(scores, reverse=True)


def test_fuse_quiet_returns_no_alerts(db):
    out = fuse(db, "quiet.myshopify.com")
    assert out["alerts"] == []
    assert out["atomic_signals"] == []


def test_fuse_dedups_general_when_specific_fires(db):
    """When demand_softening fires, general_revenue_dip is suppressed."""
    # Plant revenue drop + repeat-rate drop scenario
    # 30+ baseline orders with repeat customers, then 30+ with mostly fresh customers
    for i in range(20):
        db.add(ShopOrder(
            shop_domain=SHOP,
            shopify_order_id=f"gid://{SHOP}/o/p{i}",
            total_price=50.0,
            currency="EUR",
            customer_id=f"prior_{i % 5}",  # 5 customers, 4 orders each → high repeat
            line_items=[],
            created_at=_now() - timedelta(days=45 + (i % 30)),
        ))
    for i in range(20):
        db.add(ShopOrder(
            shop_domain=SHOP,
            shopify_order_id=f"gid://{SHOP}/o/r{i}",
            total_price=50.0,
            currency="EUR",
            customer_id=f"new_{i}",  # 20 unique customers → low repeat
            line_items=[],
            created_at=_now() - timedelta(days=i % 28),
        ))
    # Plant strong revenue drop
    _plant_revenue(db, SHOP, recent_revenue=2.0, baseline_per_day=80.0)
    out = fuse(db, SHOP)
    # If both signals fire, general should be suppressed
    patterns = [a["pattern"] for a in out["alerts"]]
    if "demand_softening" in patterns:
        assert "general_revenue_dip" not in patterns


def test_api_fusion_endpoint(client, auth_a):
    r = client.get("/pro/anomalies/fusion", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert "alerts" in body
    assert "atomic_signals" in body
