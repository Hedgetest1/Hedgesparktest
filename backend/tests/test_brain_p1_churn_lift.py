"""Sprint A audit P1 — lift compute_churn_report OUT of the per-shop
_sense loop. Prior behavior: every tick(db, shop) called
compute_churn_report(db) which loads up to 500 merchants × ~6 queries
each. At 10k merchants × 100-tick cycles = 3M queries per cycle.
Now: compute ONCE in tick_all_active_merchants, pass `churn_levels`
dict into each _sense call.

Verifies:
  1. _sense accepts churn_levels kwarg and uses it without DB hit
  2. _sense falls back to per-call compute when churn_levels is None
  3. tick_all_active_merchants calls compute_churn_report exactly ONCE
     regardless of shops_count (the structural invariant)
"""
from __future__ import annotations


def test_sense_uses_churn_levels_dict_when_provided(db, monkeypatch):
    """When churn_levels kwarg is passed, _sense MUST NOT call
    compute_churn_report — the dict provides the lookup."""
    from app.services import merchant_churn_predictor as mcp
    from app.services import merchant_brain as mb

    call_count = {"n": 0}

    def _spy_compute(*args, **kwargs):
        call_count["n"] += 1
        return {"merchants": []}

    monkeypatch.setattr(mcp, "compute_churn_report", _spy_compute)

    levels = {"test-shop-x.myshopify.com": "high"}
    state = mb._sense(db, "test-shop-x.myshopify.com", churn_levels=levels)

    assert state.churn_risk_level == "high", (
        f"churn_risk must reflect provided dict, got {state.churn_risk_level}"
    )
    assert call_count["n"] == 0, (
        "compute_churn_report MUST NOT be called when churn_levels "
        "is provided — that defeats the Sprint A P1 fix."
    )


def test_sense_falls_back_to_compute_when_no_churn_levels(db, monkeypatch):
    """Legacy single-shop callers (no batched precompute) still get
    correct churn_level via in-call compute_churn_report."""
    from app.services import merchant_brain as mb

    call_count = {"n": 0}

    def _spy_compute(*args, **kwargs):
        call_count["n"] += 1
        return {
            "merchants": [
                {"shop_domain": "fallback-shop.myshopify.com",
                 "risk_level": "medium"},
            ]
        }

    monkeypatch.setattr(
        "app.services.merchant_churn_predictor.compute_churn_report",
        _spy_compute,
    )

    state = mb._sense(db, "fallback-shop.myshopify.com")
    assert call_count["n"] == 1, "fallback path must call compute_churn_report once"
    assert state.churn_risk_level == "medium"


def test_sense_unknown_shop_in_churn_levels_returns_unknown(db):
    """Shop not present in the batched dict → churn_risk="unknown"."""
    from app.services import merchant_brain as mb

    levels = {"other-shop.myshopify.com": "high"}
    state = mb._sense(db, "missing-shop.myshopify.com", churn_levels=levels)
    assert state.churn_risk_level == "unknown"


def test_tick_all_active_merchants_calls_compute_once(db, monkeypatch):
    """The P1 structural invariant: tick_all_active_merchants MUST
    call compute_churn_report exactly ONCE regardless of how many
    shops it ticks. If a future refactor accidentally lifts the call
    back into the per-shop loop, this test catches it."""
    import os
    from app.services import merchant_brain as mb

    # Enable brain for this test
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    # Reset the cached env-read (is_brain_enabled may cache module-level)

    # Spy on compute_churn_report
    call_count = {"n": 0}

    def _spy_compute(*args, **kwargs):
        call_count["n"] += 1
        return {"merchants": []}

    monkeypatch.setattr(
        "app.services.merchant_churn_predictor.compute_churn_report",
        _spy_compute,
    )

    # Mock the per-shop tick so we focus on the structural call count,
    # not the full SENSE→COORDINATE chain.
    def _stub_tick(db, shop_domain, churn_levels=None):
        return {"shop": shop_domain, "action_kind": "no_action_test"}

    monkeypatch.setattr(mb, "tick", _stub_tick)

    # Insert 3 test merchants so the loop has real iteration count
    from sqlalchemy import text as _sql_text
    for i in range(3):
        shop = f"p1-churn-test-{i}.myshopify.com"
        db.execute(_sql_text("""
            INSERT INTO merchants
              (shop_domain, install_status, installed_at,
               access_token, plan, onboarding_status)
            VALUES
              (:s, 'active', now(), 'test_token', 'lite', 'ready')
            ON CONFLICT (shop_domain) DO UPDATE
              SET install_status = 'active'
        """), {"s": shop})
    db.flush()

    mb.tick_all_active_merchants(db, max_shops=10)

    # The CRITICAL invariant: compute_churn_report MUST be called
    # exactly once regardless of how many shops we iterate.
    assert call_count["n"] == 1, (
        f"compute_churn_report called {call_count['n']} times — "
        f"P1 fix REGRESSED: the function MUST be hoisted out of the "
        f"per-shop loop. Each extra call = 500 merchants × 6 queries = "
        f"~3000 queries per shop iteration."
    )
