"""Synthetic tests for the holdout-lift + peer-overlay wiring on
the custom report executor (Gap #1).

These two paths fire CONDITIONALLY at runtime:
  - Holdout-lift: needs ≥30 visitors per cohort (exposed + holdout)
    in execution_tracking AND matching shop_orders via
    visitor_purchase_sessions in the report window.
  - Peer-overlay: needs ≥30 peers in the same (vertical, band) bucket
    via vertical_benchmarks pipeline.

At our pre-merchant scale neither condition is met in production. These
tests use synthetic seed data to force the path into firing and assert
that the executor populates the annotation fields correctly.

Without these tests the wiring code paths would be UNTESTED — exactly
the "shipped but never run" failure mode that
`feedback_no_lies_top1_cto.md` warns against.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.models.execution import ExecutionTracking
from app.models.shop_order import ShopOrder
from app.models.visitor_purchase_session import VisitorPurchaseSession
from tests.conftest import SHOP_A, auth_cookies


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ════════════════════════════════════════════════════════════════════════
# Holdout-lift path
# ════════════════════════════════════════════════════════════════════════


def _seed_holdout_cohort(db, shop, exec_id="t-rep-1", *, n_per=30, exposed_avg=120, holdout_avg=80):
    """Seed 60 visitors (30 exposed × 30 holdout) with VPS + ShopOrder
    rows so the holdout-lift wiring's window query produces a real diff.
    """
    now = _now_naive()
    for i in range(n_per):
        vid = f"vis-exposed-{i}"
        oid = f"o-holdout-test-e-{i}"
        db.add(ExecutionTracking(
            execution_id=exec_id,
            shop_domain=shop,
            visitor_id=vid,
            group_type="exposed",
            exposed_at=now - timedelta(hours=12),
        ))
        db.add(VisitorPurchaseSession(
            shop_domain=shop,
            visitor_id=vid,
            shopify_order_id=oid,
            confirmed_at=now - timedelta(hours=6),
        ))
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=oid,
            total_price=float(exposed_avg),
            currency="EUR",
            customer_email=f"e{i}@x.com",
            line_items=[{"price": str(exposed_avg), "quantity": 1, "title": "P"}],
            created_at=now - timedelta(hours=6),
            source="webhook",
        ))

    for i in range(n_per):
        vid = f"vis-holdout-{i}"
        oid = f"o-holdout-test-h-{i}"
        db.add(ExecutionTracking(
            execution_id=exec_id,
            shop_domain=shop,
            visitor_id=vid,
            group_type="holdout",
            exposed_at=now - timedelta(hours=12),
        ))
        db.add(VisitorPurchaseSession(
            shop_domain=shop,
            visitor_id=vid,
            shopify_order_id=oid,
            confirmed_at=now - timedelta(hours=6),
        ))
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=oid,
            total_price=float(holdout_avg),
            currency="EUR",
            customer_email=f"h{i}@x.com",
            line_items=[{"price": str(holdout_avg), "quantity": 1, "title": "P"}],
            created_at=now - timedelta(hours=6),
            source="webhook",
        ))


def test_holdout_lift_helper_fires_with_seeded_cohorts(db, merchant_a):
    """The helper should compute lift_eur > 0 when exposed mean > holdout mean."""
    _seed_holdout_cohort(db, SHOP_A, n_per=30, exposed_avg=150, holdout_avg=80)
    db.flush()

    from app.services.report_holdout_lift import holdout_lift_for_shop_window
    now = _now_naive()
    result = holdout_lift_for_shop_window(
        db, SHOP_A, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert result is not None, "wiring should fire with ≥30 per cohort"
    assert result["n_exposed"] == 30
    assert result["n_holdout"] == 30
    # mean_exposed=150, mean_holdout=80 → per-visitor delta=70
    # total lift = 70 * 30 exposed = 2100
    assert result["lift_eur"] == pytest.approx(2100.0, abs=1.0)
    assert 0.0 <= result["p_value"] <= 1.0


def test_holdout_lift_helper_no_fire_below_min_n(db, merchant_a):
    """Below the 30-per-cohort floor the helper returns None."""
    _seed_holdout_cohort(db, SHOP_A, n_per=10)  # 10 < 30 floor
    db.flush()

    from app.services.report_holdout_lift import holdout_lift_for_shop_window
    now = _now_naive()
    result = holdout_lift_for_shop_window(
        db, SHOP_A, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert result is None


def test_holdout_lift_boundary_n_29_no_fire(db, merchant_a):
    """Exact n=29 per cohort sits below the floor → None."""
    _seed_holdout_cohort(db, SHOP_A, n_per=29, exposed_avg=120, holdout_avg=80)
    db.flush()

    from app.services.report_holdout_lift import holdout_lift_for_shop_window
    now = _now_naive()
    result = holdout_lift_for_shop_window(
        db, SHOP_A, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert result is None


def test_holdout_lift_boundary_n_30_fires(db, merchant_a):
    """Exact n=30 per cohort hits the floor → fires."""
    _seed_holdout_cohort(db, SHOP_A, n_per=30, exposed_avg=120, holdout_avg=80)
    db.flush()

    from app.services.report_holdout_lift import holdout_lift_for_shop_window
    now = _now_naive()
    result = holdout_lift_for_shop_window(
        db, SHOP_A, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert result is not None
    assert result["n_exposed"] == 30
    assert result["n_holdout"] == 30


def test_holdout_lift_helper_no_active_execution(db, merchant_a):
    """No execution_tracking rows in window → None."""
    from app.services.report_holdout_lift import holdout_lift_for_shop_window
    now = _now_naive()
    result = holdout_lift_for_shop_window(
        db, SHOP_A, now - timedelta(days=1), now + timedelta(days=1)
    )
    assert result is None


def test_executor_surfaces_holdout_annotation(client, merchant_a, db):
    """End-to-end: report executor reads the seeded cohorts and surfaces
    the annotation on the response body's first row."""
    _seed_holdout_cohort(db, SHOP_A, n_per=30, exposed_avg=150, holdout_avg=80)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={
            "name": "With holdout",
            "metric": "revenue",
            "dimensions": [],
            "date_range_preset": "last_7_days",
        },
    ).json()["id"]

    resp = client.get(f"/merchant/reports/{rid}/data", cookies=cookies)
    assert resp.status_code == 200
    body = resp.json()
    first = body["rows"][0]
    assert first["holdout_lift_eur"] is not None, (
        f"holdout-lift wiring did not fire — body={body!r}"
    )
    assert first["holdout_lift_eur"] > 0
    # Note about the holdout annotation should be present
    assert any("holdout" in n.lower() for n in body["notes"])


# ════════════════════════════════════════════════════════════════════════
# Peer-network overlay path
# ════════════════════════════════════════════════════════════════════════


def test_executor_surfaces_peer_overlay_when_helper_returns_data(client, merchant_a, db):
    """The peer-overlay path is bounded by k-anonymity (≥30 peers per
    vertical+band) which we cannot meet at test scale. Mock the helper
    to return a populated report so we verify the wiring INTEGRATION
    (executor reads percentile_rank and stamps it on rows[0])."""
    from app.models.shop_order import ShopOrder
    db.add(ShopOrder(
        shop_domain=SHOP_A,
        shopify_order_id="peer-test-1",
        total_price=200.0,
        currency="EUR",
        customer_email="x@y.com",
        line_items=[{"price": "200", "quantity": 1, "title": "P"}],
        created_at=_now_naive() - timedelta(hours=4),
        source="webhook",
    ))
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={
            "name": "With peer overlay",
            "metric": "revenue",
            "dimensions": [],
            "date_range_preset": "last_7_days",
        },
    ).json()["id"]

    fake_peer = {
        "peers_status": "ok",
        "metrics": {
            "monthly_revenue": {"percentile_rank": 78},
        },
    }

    with patch(
        "app.services.benchmarks_vertical.get_vertical_benchmark_report",
        return_value=fake_peer,
    ):
        resp = client.get(f"/merchant/reports/{rid}/data", cookies=cookies)

    assert resp.status_code == 200
    body = resp.json()
    first = body["rows"][0]
    assert first["peer_percentile"] == 78, (
        f"peer-overlay wiring did not stamp percentile — body={body!r}"
    )
    assert any("peer" in n.lower() for n in body["notes"])


def test_executor_silent_when_peer_helper_says_insufficient(client, merchant_a, db):
    """When the peer pool is below k-anonymity floor, the executor
    silently omits the annotation (no spurious zero or 'unknown' label)."""
    from app.models.shop_order import ShopOrder
    db.add(ShopOrder(
        shop_domain=SHOP_A,
        shopify_order_id="peer-test-2",
        total_price=100.0,
        currency="EUR",
        customer_email="x2@y.com",
        line_items=[{"price": "100", "quantity": 1, "title": "P"}],
        created_at=_now_naive() - timedelta(hours=4),
        source="webhook",
    ))
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={
            "name": "Without peer",
            "metric": "revenue",
            "dimensions": [],
            "date_range_preset": "last_7_days",
        },
    ).json()["id"]

    fake_peer = {"peers_status": "insufficient_peers", "metrics": {}}

    with patch(
        "app.services.benchmarks_vertical.get_vertical_benchmark_report",
        return_value=fake_peer,
    ):
        resp = client.get(f"/merchant/reports/{rid}/data", cookies=cookies)

    assert resp.status_code == 200
    first = resp.json()["rows"][0]
    assert first["peer_percentile"] is None
