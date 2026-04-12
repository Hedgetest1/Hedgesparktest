"""
Tests for F3 — Goals/targets system.

Verifies Redis-backed CRUD, progress computation with forecast projection,
at-risk/off-track classification, and ops_alert emission on risky goals.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

import pytest

from app.models.shop_order import ShopOrder
from app.services.goals import (
    Goal,
    GoalProgress,
    _classify_goal,
    _compute_current_value,
    _project_end_of_month,
    compute_goal_progress,
    delete_goal,
    get_goals,
    set_goal,
    check_goals_at_risk,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_orders_this_month(db, shop: str, count: int, price: float):
    """Plant orders dated in the current month so month-to-date aggregation sees them."""
    now = _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for i in range(count):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"gid://{shop}/order/goal_{i}",
            total_price=price,
            currency="EUR",
            line_items=[],
            created_at=month_start + timedelta(hours=i),
        ))
    db.flush()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_classify_goal_on_track():
    assert _classify_goal(0) == "on_track"
    assert _classify_goal(-5) == "on_track"
    assert _classify_goal(5) == "at_risk"
    assert _classify_goal(29) == "at_risk"
    assert _classify_goal(30) == "off_track"
    assert _classify_goal(100) == "off_track"


def test_project_end_of_month_linear():
    # 15 days in, 30-day month → projected = current * 2
    result = _project_end_of_month(current_value=5000, day_of_month=15, days_in_month=30)
    assert result == 10_000
    # No days elapsed edge case
    assert _project_end_of_month(100, 0, 30) == 100


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_set_and_get_goal(db):
    shop = "goal-crud-shop.myshopify.com"
    g = set_goal(shop, metric="monthly_revenue", target_value=50_000.0, note="Q1 push")
    if g is None:
        pytest.skip("redis unavailable in test env")

    goals = get_goals(shop)
    assert len(goals) == 1
    assert goals[0].metric == "monthly_revenue"
    assert goals[0].target_value == 50_000.0
    assert goals[0].note == "Q1 push"


def test_set_goal_replaces_existing_same_metric(db):
    shop = "goal-replace-shop.myshopify.com"
    g1 = set_goal(shop, metric="aov", target_value=80.0)
    if g1 is None:
        pytest.skip("redis unavailable")
    g2 = set_goal(shop, metric="aov", target_value=100.0)
    assert g2 is not None

    goals = get_goals(shop)
    aov_goals = [g for g in goals if g.metric == "aov"]
    assert len(aov_goals) == 1
    assert aov_goals[0].target_value == 100.0


def test_set_goal_rejects_unsupported_metric():
    with pytest.raises(ValueError):
        set_goal("any-shop.myshopify.com", metric="not_a_metric", target_value=100)


def test_set_goal_rejects_non_positive_target():
    with pytest.raises(ValueError):
        set_goal("any-shop.myshopify.com", metric="monthly_revenue", target_value=0)
    with pytest.raises(ValueError):
        set_goal("any-shop.myshopify.com", metric="monthly_revenue", target_value=-1)


def test_delete_goal(db):
    shop = "goal-delete-shop.myshopify.com"
    g = set_goal(shop, metric="monthly_orders", target_value=500)
    if g is None:
        pytest.skip("redis unavailable")

    removed = delete_goal(shop, "monthly_orders")
    assert removed is True
    assert get_goals(shop) == []

    # Deleting something that doesn't exist returns False
    assert delete_goal(shop, "monthly_orders") is False


# ---------------------------------------------------------------------------
# Progress computation
# ---------------------------------------------------------------------------

def test_compute_progress_returns_empty_for_no_goals(db):
    shop = "no-goals-shop.myshopify.com"
    # Ensure no goals exist
    delete_goal(shop, "monthly_revenue")
    progress = compute_goal_progress(db, shop)
    assert progress == []


def test_compute_progress_classifies_off_track(db):
    """Shop at €1000 MTD vs €100k target → projected is way short → off_track."""
    shop = "off-track-shop.myshopify.com"
    _mk_orders_this_month(db, shop, count=10, price=100.0)  # ~€1k MTD
    db.commit()

    g = set_goal(shop, metric="monthly_revenue", target_value=100_000.0)
    if g is None:
        pytest.skip("redis unavailable")

    progress = compute_goal_progress(db, shop)
    assert len(progress) == 1
    assert progress[0].metric == "monthly_revenue"
    assert progress[0].status == "off_track"
    assert progress[0].projected_value < 100_000
    assert progress[0].gap_pct > 30


def test_compute_progress_classifies_on_track(db):
    """Shop doing well vs low target → on_track."""
    shop = "on-track-shop.myshopify.com"
    _mk_orders_this_month(db, shop, count=50, price=100.0)  # €5k MTD
    db.commit()

    g = set_goal(shop, metric="monthly_revenue", target_value=1_000.0)
    if g is None:
        pytest.skip("redis unavailable")

    progress = compute_goal_progress(db, shop)
    assert len(progress) == 1
    assert progress[0].status == "on_track"


def test_check_goals_at_risk_emits_alert(db):
    """Risky goal triggers an ops_alert via the self-healing bridge."""
    from app.models.ops_alert import OpsAlert

    shop = "at-risk-alert-shop.myshopify.com"
    _mk_orders_this_month(db, shop, count=5, price=100.0)  # €500 MTD
    db.commit()

    g = set_goal(shop, metric="monthly_revenue", target_value=50_000.0)
    if g is None:
        pytest.skip("redis unavailable")

    risky = check_goals_at_risk(db, shop)
    assert len(risky) >= 1

    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "goal_at_risk",
            OpsAlert.source == f"goals:{shop}",
        )
        .first()
    )
    assert alert is not None
    assert "at risk" in alert.summary


# ---------------------------------------------------------------------------
# Current value extraction
# ---------------------------------------------------------------------------

def test_compute_current_value_monthly_revenue(db):
    shop = "current-value-shop.myshopify.com"
    _mk_orders_this_month(db, shop, count=10, price=100.0)
    db.commit()
    value = _compute_current_value(db, shop, "monthly_revenue")
    assert value == 1000.0


def test_compute_current_value_aov(db):
    shop = "aov-shop.myshopify.com"
    _mk_orders_this_month(db, shop, count=5, price=80.0)
    db.commit()
    value = _compute_current_value(db, shop, "aov")
    assert value == 80.0
