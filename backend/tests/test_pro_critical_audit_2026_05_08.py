"""Pin the 3 CRITICAL Pro-tier crash-class fixes from 2026-05-08 audit.

Each test reproduces the bug condition that would crash a real merchant
or surface a §0 false-claim, then asserts the fix.

BUGS:
  1. knowledge_graph._h_revenue_today / _h_why_revenue_drop:
     `n.attrs.get("created_at", "").startswith(today)` raised
     AttributeError when created_at was None. Real production trigger:
     any shop whose order rows had NULL created_at (rare but possible
     pre-Shopify-webhook backfill) would crash the chatbot intent.
  2. knowledge_graph._pull_anomalies:
     `WHERE (shop_domain = :shop OR shop_domain IS NULL)` leaked
     system-wide ops_alerts (LLM budget, Redis health, infra) into a
     merchant's knowledge graph, mis-attributing operator signals to
     paying merchants.
  3. proof_engine.summarize_holdout_proof:
     fabricated `est_incremental` for 0-order merchants by multiplying
     `cvr_delta * exp_count * FALLBACK_AOV` (€50). A new Pro merchant
     with no orders but an active holdout would see a non-zero "incremental
     revenue" number — direct §0 false-claim violation.
"""
from __future__ import annotations

from app.services.knowledge_graph import (
    KGNode,
    MerchantKG,
    _h_revenue_today,
    _h_why_revenue_drop,
)


def _make_kg(orders: list[dict]) -> MerchantKG:
    kg = MerchantKG(shop_domain="fixture-pro.myshopify.com")
    for i, o in enumerate(orders):
        kg.add_node(KGNode(entity_type="order", entity_id=str(i), attrs=o))
    return kg


def test_revenue_today_handles_null_created_at():
    """Bug #1: an order with created_at=None must not crash the chatbot."""
    kg = _make_kg([
        {"created_at": None, "total_price": 100.0},
        {"created_at": None, "total_price": 50.0},
    ])
    out = _h_revenue_today(kg, "revenue today")
    assert out["intent"] == "revenue_today"
    assert out["data"]["orders"] == 0  # null timestamps don't match today
    assert out["data"]["revenue_eur"] == 0.0


def test_why_revenue_drop_handles_null_created_at():
    """Bug #1 sibling: same crash class in the why-drop intent."""
    kg = _make_kg([
        {"created_at": None, "total_price": 200.0},
    ])
    out = _h_why_revenue_drop(kg, "why is revenue dropping")
    # Did not raise → fix is in.
    assert "intent" in out


def test_pull_anomalies_excludes_system_wide_alerts(db):
    """Bug #2: system-wide alerts (shop_domain IS NULL) must NOT
    appear in a merchant's knowledge graph. Tenant isolation breach."""
    from sqlalchemy import text
    from app.services.knowledge_graph import _pull_anomalies, MerchantKG
    from datetime import datetime, timezone

    shop = "_test_kg_anom_isolation_.myshopify.com"
    other = "_test_kg_anom_other_.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    db.execute(text("DELETE FROM ops_alerts WHERE shop_domain IN (:a, :b) OR shop_domain IS NULL AND summary LIKE 'kg-isolation-test:%'"),
               {"a": shop, "b": other})
    db.execute(text("""
        INSERT INTO ops_alerts (source, alert_type, severity, summary, shop_domain, created_at)
        VALUES
          ('test', 'shop_specific', 'warning', 'kg-isolation-test: shop alert', :shop, :now),
          ('test', 'system_wide',    'warning', 'kg-isolation-test: GLOBAL alert', NULL, :now),
          ('test', 'other_shop',     'warning', 'kg-isolation-test: other shop',  :other, :now)
    """), {"shop": shop, "other": other, "now": now})
    db.commit()

    try:
        kg = MerchantKG(shop_domain=shop)
        _pull_anomalies(db, kg, lookback_days=14)
        anomalies = [n for n in kg.nodes.values() if n.entity_type == "anomaly"]
        summaries = [n.attrs.get("summary", "") for n in anomalies]

        own_alerts = [s for s in summaries if "shop alert" in s]
        leaked_global = [s for s in summaries if "GLOBAL alert" in s]
        leaked_other = [s for s in summaries if "other shop" in s]

        assert len(own_alerts) == 1, (
            f"merchant must see their own alerts (got {len(own_alerts)})"
        )
        assert len(leaked_global) == 0, (
            f"system-wide alerts must NOT leak into merchant KG "
            f"(got {len(leaked_global)} global)"
        )
        assert len(leaked_other) == 0, (
            f"other merchants' alerts must NOT leak (got {len(leaked_other)})"
        )
    finally:
        db.execute(
            text("DELETE FROM ops_alerts WHERE summary LIKE 'kg-isolation-test:%' OR shop_domain IN (:a, :b)"),
            {"a": shop, "b": other},
        )
        db.commit()


def _seed_holdout_nudge(db, shop: str, nudge_id: int = 999_001):
    """Insert one active_nudges row + holdout_assigned event so
    `_build_holdout_proof` finds work to do. Cleanup in teardown."""
    from sqlalchemy import text
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.execute(text(
        "DELETE FROM nudge_events WHERE nudge_id = :nid"
    ), {"nid": nudge_id})
    db.execute(text(
        "DELETE FROM active_nudges WHERE id = :nid"
    ), {"nid": nudge_id})
    from datetime import timedelta
    expires = now + timedelta(days=30)
    db.execute(text("""
        INSERT INTO active_nudges (id, shop_domain, product_url, action_type,
                                    trigger_source, copy_variant, copy_config,
                                    status, created_at, updated_at, expires_at,
                                    holdout_pct, is_bootstrap)
        VALUES (:nid, :shop, 'https://example.com/p', 'show_offer',
                'manual', 'A', '{}', 'active', :now, :now, :expires,
                20, false)
    """), {"nid": nudge_id, "shop": shop, "now": now, "expires": expires})
    db.execute(text("""
        INSERT INTO nudge_events (nudge_id, shop_domain, event_type, visitor_id,
                                    product_url, created_at)
        VALUES (:nid, :shop, 'holdout_assigned', 'visitor-x',
                'https://example.com/p', :now)
    """), {"nid": nudge_id, "shop": shop, "now": now})
    db.commit()
    return nudge_id


def _cleanup_holdout_nudge(db, nudge_id: int):
    from sqlalchemy import text
    db.execute(text("DELETE FROM nudge_events WHERE nudge_id = :nid"), {"nid": nudge_id})
    db.execute(text("DELETE FROM active_nudges WHERE id = :nid"), {"nid": nudge_id})
    db.commit()


def test_proof_engine_does_not_fabricate_incremental_for_zero_order_merchant(db):
    """Bug #3: a merchant with 0 orders → FALLBACK_AOV — must NOT
    multiply CVR-delta × FALLBACK_AOV to fabricate incremental revenue.

    Real-world repro: a Pro merchant joined yesterday, has no orders,
    but a holdout exposed for a nudge created 50 visitors. Pre-fix,
    they would see "€X incremental revenue from this nudge" computed
    from a generic €50 fallback AOV — a §0 false-claim violation.
    """
    from unittest.mock import patch
    from app.services import proof_engine

    shop = "_test_proof_zero_orders_.myshopify.com"
    nudge_id = _seed_holdout_nudge(db, shop)

    fake_lift = {
        "holdout_active": True,
        "exposed_count": 100,
        "holdout_count": 50,
        "exposed_cvr": 0.04,
        "holdout_cvr": 0.02,
        "revenue_lift": {
            "exposed_revenue": 0.0,
            "estimated_incremental_revenue": 0.0,  # forces cvr-delta path
            "currency": "EUR",
        },
        "p_value": 0.02,
        "significance": "significant",
        "estimated_lift_pct": 100.0,
    }

    try:
        with patch.object(proof_engine, "get_nudge_lift_report", return_value=fake_lift), \
             patch.object(proof_engine, "get_shop_aov", return_value=proof_engine.FALLBACK_AOV), \
             patch.object(proof_engine, "get_shop_currency", return_value="EUR"), \
             patch.object(proof_engine, "_store_revenue", return_value=0.0):
            holdout = proof_engine._build_holdout_proof(db, shop, window_hours=168)

        # Pre-fix: incremental_revenue would be cvr_delta × exp_count × FALLBACK_AOV
        #          = 0.02 × 100 × 50 = €100 (FABRICATED).
        # Post-fix: must be €0 because AOV is fallback.
        assert holdout.get("incremental_revenue", -1) == 0, (
            f"§0 false-claim guard: incremental revenue with FALLBACK_AOV "
            f"must be 0, got {holdout.get('incremental_revenue')!r}"
        )
        for n in holdout.get("nudges", []):
            assert n["incremental_revenue"] == 0, (
                f"per-nudge incremental must be 0 when AOV is fallback, nudge={n}"
            )
    finally:
        _cleanup_holdout_nudge(db, nudge_id)


def test_proof_engine_uses_real_aov_when_available(db):
    """Sanity counterpart to bug #3: when AOV IS real (merchant has
    orders), the cvr-delta path must still compute a real number."""
    from unittest.mock import patch
    from app.services import proof_engine

    shop = "_test_proof_real_aov_.myshopify.com"
    nudge_id = _seed_holdout_nudge(db, shop, nudge_id=999_002)

    fake_lift = {
        "holdout_active": True,
        "exposed_count": 100,
        "holdout_count": 50,
        "exposed_cvr": 0.04,
        "holdout_cvr": 0.02,
        "revenue_lift": {
            "exposed_revenue": 0.0,
            "estimated_incremental_revenue": 0.0,
            "currency": "EUR",
        },
        "p_value": 0.02,
        "significance": "significant",
        "estimated_lift_pct": 100.0,
    }
    real_aov = 75.0  # distinct from FALLBACK_AOV (50.0)

    try:
        with patch.object(proof_engine, "get_nudge_lift_report", return_value=fake_lift), \
             patch.object(proof_engine, "get_shop_aov", return_value=real_aov), \
             patch.object(proof_engine, "get_shop_currency", return_value="EUR"), \
             patch.object(proof_engine, "_store_revenue", return_value=10_000.0):
            holdout = proof_engine._build_holdout_proof(db, shop, window_hours=168)

        # 0.02 × 100 × 75 = €150 expected (no caps because store_revenue is large).
        nudges = holdout.get("nudges", [])
        assert len(nudges) == 1, f"expected 1 nudge, got {len(nudges)}"
        # Real AOV path: should compute > 0 (would have been fabricated
        # even pre-fix; this test pins that we didn't break the happy path).
        assert nudges[0]["incremental_revenue"] > 0, (
            f"real-AOV path must produce non-zero incremental, "
            f"got {nudges[0]['incremental_revenue']}"
        )
    finally:
        _cleanup_holdout_nudge(db, nudge_id)
