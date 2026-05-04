"""
Behavior preservation tests for compute_post_execution_deltas.

Pre-refactor scaffold (item 6 wave 7) — establishes baseline coverage
for the per-opp counterfactual computation BEFORE collapsing the N+1
queries to a bulk path. Run these tests against the OLD per-opp code
first to capture the contract, then against the NEW bulk code to prove
preservation.

The function processes every executed opportunity for a shop and updates
19 columns (post_*, delta_*, exposed/holdout sample sizes, per-group
rates, lifts, confidence_label) on execution_opportunities.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.token_crypto import encrypt_token
from app.models.execution import (
    ExecutionOpportunity,
    ExecutionBaseline,
    ExecutionTracking,
)
from app.models.merchant import Merchant
from app.services.execution_engine import compute_post_execution_deltas


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_merchant(db, shop: str):
    db.add(Merchant(
        shop_domain=shop,
        access_token=encrypt_token("shpat_x"),
        plan="pro",
        install_status="active",
    ))
    db.flush()


def _mk_opp(db, shop: str, eid: str, *, executed_at, status="executed", is_active=True):
    db.add(ExecutionOpportunity(
        shop_domain=shop, execution_id=eid,
        opp_type="cross_sell", product_a="/p/a", product_b="/p/b",
        audience_size=100, holdout_pct=20, enforcement_mode="onsite",
        execution_status=status, executed_at=executed_at,
        is_active=is_active,
    ))


def _mk_baseline(db, shop: str, eid: str, *, view=0.10, purchase=0.05, ret=0.0):
    db.add(ExecutionBaseline(
        shop_domain=shop, execution_id=eid,
        view_rate=view, purchase_rate=purchase, return_rate=ret,
        tracked_count=50, audience_size=100,
    ))


def _mk_tracking(db, shop: str, eid: str, visitor: str, *,
                 group: str, exposed_at, returned=False,
                 viewed_b=False, purchased_b=False, leak=False):
    db.add(ExecutionTracking(
        shop_domain=shop, execution_id=eid, visitor_id=visitor,
        group_type=group, exposed_at=exposed_at,
        returned=returned, viewed_product_b=viewed_b,
        purchased_product_b=purchased_b, leakage_suspected=leak,
    ))


# ---------------------------------------------------------------------------
# Happy path — opp with both groups gets all 19 columns populated
# ---------------------------------------------------------------------------

def test_executed_opp_with_both_groups_populates_metrics(db):
    shop = "exec-deltas-1.myshopify.com"
    _mk_merchant(db, shop)

    executed_at = _now() - timedelta(hours=2)
    _mk_opp(db, shop, "exec_001", executed_at=executed_at)
    _mk_baseline(db, shop, "exec_001", view=0.10, purchase=0.05)

    # 25 exposed: 5 returned, 12 viewed_b, 4 purchased_b
    for i in range(25):
        _mk_tracking(
            db, shop, "exec_001", f"v_exp_{i}", group="exposed",
            exposed_at=executed_at + timedelta(minutes=10),
            returned=(i < 5), viewed_b=(i < 12), purchased_b=(i < 4),
        )
    # 10 holdout: 1 returned, 2 viewed_b, 0 purchased_b
    for i in range(10):
        _mk_tracking(
            db, shop, "exec_001", f"v_hld_{i}", group="holdout",
            exposed_at=executed_at + timedelta(minutes=15),
            returned=(i < 1), viewed_b=(i < 2), purchased_b=False,
        )
    db.flush()

    n = compute_post_execution_deltas(db, shop)
    assert n == 1

    # Re-read the opp from DB and verify all 19 fields wired
    opp = db.query(ExecutionOpportunity).filter_by(
        shop_domain=shop, execution_id="exec_001"
    ).one()
    assert opp.exposed_sample_size == 25
    assert opp.holdout_sample_size == 10
    assert opp.post_sample_size == 35
    assert opp.return_rate_exposed == pytest.approx(0.20, abs=0.001)   # 5/25
    assert opp.view_rate_exposed == pytest.approx(0.48, abs=0.001)     # 12/25
    assert opp.purchase_rate_exposed == pytest.approx(0.16, abs=0.001) # 4/25
    assert opp.return_rate_holdout == pytest.approx(0.10, abs=0.001)   # 1/10
    assert opp.view_rate_holdout == pytest.approx(0.20, abs=0.001)     # 2/10
    assert opp.purchase_rate_holdout == pytest.approx(0.0, abs=0.001)  # 0/10
    # lifts = exposed - holdout
    assert opp.lift_view_rate == pytest.approx(0.28, abs=0.001)
    assert opp.lift_purchase_rate == pytest.approx(0.16, abs=0.001)
    # post-rates combined
    assert opp.post_view_rate == pytest.approx((12 + 2) / 35, abs=0.001)
    assert opp.post_purchase_rate == pytest.approx(4 / 35, abs=0.001)
    # deltas vs baseline
    assert opp.delta_view_rate == pytest.approx(opp.post_view_rate - 0.10, abs=0.001)
    assert opp.delta_purchase_rate == pytest.approx(opp.post_purchase_rate - 0.05, abs=0.001)
    # confidence: leakage=0%, exposed=25, holdout=10, purchase_lift=0.16 (>=0.02)
    # → "strong"
    assert opp.confidence_label == "strong"


# ---------------------------------------------------------------------------
# Skip path — no tracking after executed_at means total_post=0 → skipped
# ---------------------------------------------------------------------------

def test_opp_without_post_tracking_is_skipped(db):
    shop = "exec-deltas-2.myshopify.com"
    _mk_merchant(db, shop)

    executed_at = _now() - timedelta(hours=2)
    _mk_opp(db, shop, "exec_002", executed_at=executed_at)
    # Tracking PRE-executed_at only — outside the post window
    _mk_tracking(
        db, shop, "exec_002", "v_pre", group="exposed",
        exposed_at=executed_at - timedelta(minutes=5),
        viewed_b=True,
    )
    db.flush()

    n = compute_post_execution_deltas(db, shop)
    assert n == 0  # opp is skipped (total_post=0)
    opp = db.query(ExecutionOpportunity).filter_by(
        shop_domain=shop, execution_id="exec_002"
    ).one()
    # Defaults preserved (no metric writes)
    assert opp.exposed_sample_size == 0
    assert opp.holdout_sample_size == 0
    assert opp.confidence_label is None


# ---------------------------------------------------------------------------
# Skip path — opp with executed_at IS NULL is skipped at the loop level
# ---------------------------------------------------------------------------

def test_opp_with_null_executed_at_is_skipped(db):
    shop = "exec-deltas-3.myshopify.com"
    _mk_merchant(db, shop)
    # Note: outer SELECT requires execution_status='executed', so to test
    # the executed_at IS NULL skip we keep status='executed' but leave
    # executed_at = NULL (pathological state but allowed by schema).
    _mk_opp(db, shop, "exec_003", executed_at=None)
    db.flush()

    n = compute_post_execution_deltas(db, shop)
    assert n == 0


# ---------------------------------------------------------------------------
# Two-opp independence — each opp computes its own metrics in isolation
# ---------------------------------------------------------------------------

def test_two_opps_independent_metrics(db):
    shop = "exec-deltas-multi.myshopify.com"
    _mk_merchant(db, shop)

    t1 = _now() - timedelta(hours=4)
    t2 = _now() - timedelta(hours=2)

    _mk_opp(db, shop, "exec_A", executed_at=t1)
    _mk_baseline(db, shop, "exec_A", view=0.10, purchase=0.05)

    _mk_opp(db, shop, "exec_B", executed_at=t2)
    _mk_baseline(db, shop, "exec_B", view=0.30, purchase=0.20)

    # exec_A: 12 exposed (4 viewed_b), 5 holdout (1 viewed_b)
    for i in range(12):
        _mk_tracking(db, shop, "exec_A", f"A_exp_{i}", group="exposed",
                     exposed_at=t1 + timedelta(minutes=10),
                     viewed_b=(i < 4))
    for i in range(5):
        _mk_tracking(db, shop, "exec_A", f"A_hld_{i}", group="holdout",
                     exposed_at=t1 + timedelta(minutes=15),
                     viewed_b=(i < 1))

    # exec_B: 20 exposed (10 viewed_b, 4 purchased_b), 5 holdout (3 viewed_b)
    for i in range(20):
        _mk_tracking(db, shop, "exec_B", f"B_exp_{i}", group="exposed",
                     exposed_at=t2 + timedelta(minutes=10),
                     viewed_b=(i < 10), purchased_b=(i < 4))
    for i in range(5):
        _mk_tracking(db, shop, "exec_B", f"B_hld_{i}", group="holdout",
                     exposed_at=t2 + timedelta(minutes=15),
                     viewed_b=(i < 3))
    db.flush()

    n = compute_post_execution_deltas(db, shop)
    assert n == 2

    a = db.query(ExecutionOpportunity).filter_by(
        shop_domain=shop, execution_id="exec_A"
    ).one()
    b = db.query(ExecutionOpportunity).filter_by(
        shop_domain=shop, execution_id="exec_B"
    ).one()

    assert a.exposed_sample_size == 12 and a.holdout_sample_size == 5
    assert a.view_rate_exposed == pytest.approx(4 / 12, abs=0.001)
    assert a.view_rate_holdout == pytest.approx(1 / 5, abs=0.001)

    assert b.exposed_sample_size == 20 and b.holdout_sample_size == 5
    assert b.view_rate_exposed == pytest.approx(10 / 20, abs=0.001)
    assert b.view_rate_holdout == pytest.approx(3 / 5, abs=0.001)
    assert b.purchase_rate_exposed == pytest.approx(4 / 20, abs=0.001)
