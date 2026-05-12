"""P3 close: parity test — batched checks produce identical DriftFindings
as the per-shop versions when given the same data.

This test inserts synthetic shop_orders + visitor_purchase_sessions for
multiple shops with deliberate drift patterns, then runs both code
paths on the same fixture and asserts equivalence on every dimension
that emits an alert (check name, shop_domain, severity, key detail
numerics).
"""
from sqlalchemy import text

from app.services.data_integrity_probe import (
    _batch_check_attribution_drift,
    _batch_check_order_and_aov,
    _check_attribution_drift,
    _check_order_collapse,
    _check_aov_drift,
)


SHOPS = [
    "batch-parity-clean.myshopify.com",
    "batch-parity-attr-drop.myshopify.com",
    "batch-parity-order-collapse.myshopify.com",
    "batch-parity-aov-spike.myshopify.com",
]


def _seed_orders(db, shop, recent_count, baseline_count, recent_aov=50.0, baseline_aov=50.0):
    """Insert recent_count orders in last 3d window + baseline_count in 8-30d window."""
    for i in range(recent_count):
        db.execute(text("""
            INSERT INTO shop_orders (shop_domain, shopify_order_id, total_price, created_at)
            VALUES (:s, :oid, :p, NOW() - INTERVAL '3 days')
        """), {"s": shop, "oid": f"r-{shop}-{i}", "p": recent_aov})
    for i in range(baseline_count):
        db.execute(text("""
            INSERT INTO shop_orders (shop_domain, shopify_order_id, total_price, created_at)
            VALUES (:s, :oid, :p, NOW() - INTERVAL '20 days')
        """), {"s": shop, "oid": f"b-{shop}-{i}", "p": baseline_aov})


def _seed_attribution(db, shop, recent_attributed, baseline_attributed):
    """Attach VPS rows to existing orders to mark them attributed."""
    for i in range(recent_attributed):
        db.execute(text("""
            INSERT INTO visitor_purchase_sessions
                (shop_domain, visitor_id, shopify_order_id, confirmed_at, ingested_at)
            VALUES (:s, :v, :oid, NOW() - INTERVAL '3 days', NOW())
        """), {"s": shop, "v": f"v-r-{shop}-{i}", "oid": f"r-{shop}-{i}"})
    for i in range(baseline_attributed):
        db.execute(text("""
            INSERT INTO visitor_purchase_sessions
                (shop_domain, visitor_id, shopify_order_id, confirmed_at, ingested_at)
            VALUES (:s, :v, :oid, NOW() - INTERVAL '20 days', NOW())
        """), {"s": shop, "v": f"v-b-{shop}-{i}", "oid": f"b-{shop}-{i}"})


def _seed_merchants(db, shops):
    for s in shops:
        db.execute(text("""
            INSERT INTO merchants (shop_domain, install_status, installed_at)
            VALUES (:s, 'active', NOW() - INTERVAL '60 days')
            ON CONFLICT (shop_domain) DO NOTHING
        """), {"s": s})


def _finding_key(f):
    """Reduce a DriftFinding to a comparable tuple — ignores summary text
    (which may contain non-deterministic float rounding) but checks every
    numeric detail that drives the alert decision."""
    return (
        f.check,
        f.shop_domain,
        f.severity,
        # numeric detail fields — sorted for stability across versions
        tuple(sorted(
            (k, round(v, 3) if isinstance(v, (int, float)) else v)
            for k, v in f.detail.items()
        )),
    )


def test_attribution_drift_batch_matches_per_shop(db):
    _seed_merchants(db, SHOPS)
    # shop[0] clean: 90% attribution in both windows, no drift
    _seed_orders(db, SHOPS[0], recent_count=50, baseline_count=50)
    _seed_attribution(db, SHOPS[0], recent_attributed=45, baseline_attributed=45)
    # shop[1] attribution drop: 90% baseline → 30% recent (drop=60pp, critical)
    _seed_orders(db, SHOPS[1], recent_count=50, baseline_count=50)
    _seed_attribution(db, SHOPS[1], recent_attributed=15, baseline_attributed=45)

    per_shop_findings = []
    for s in SHOPS[:2]:
        f = _check_attribution_drift(db, s)
        if f is not None:
            per_shop_findings.append(f)
    batch_findings = _batch_check_attribution_drift(db, SHOPS[:2])

    assert {_finding_key(f) for f in per_shop_findings} == {
        _finding_key(f) for f in batch_findings
    }, "attribution drift findings diverge between per-shop and batched paths"
    # Confirm shop[1] critical drop was actually caught
    keys = {f.shop_domain for f in batch_findings}
    assert SHOPS[1] in keys
    assert SHOPS[0] not in keys


def test_order_and_aov_batch_matches_per_shop(db):
    _seed_merchants(db, SHOPS[2:4])
    # shop[2] order collapse: 50 baseline, 5 recent (per-day ratio ~5/7 vs 50/23 = ~0.32)
    _seed_orders(db, SHOPS[2], recent_count=5, baseline_count=50)
    # shop[3] AOV spike: 50 orders both windows, recent_aov=200, baseline_aov=50 (4x)
    _seed_orders(db, SHOPS[3], recent_count=50, baseline_count=50,
                 recent_aov=200.0, baseline_aov=50.0)

    per_shop_findings = []
    for s in SHOPS[2:4]:
        oc = _check_order_collapse(db, s)
        if oc is not None:
            per_shop_findings.append(oc)
        ad = _check_aov_drift(db, s)
        if ad is not None:
            per_shop_findings.append(ad)
    batch_findings = _batch_check_order_and_aov(db, SHOPS[2:4])

    assert {_finding_key(f) for f in per_shop_findings} == {
        _finding_key(f) for f in batch_findings
    }, "order/aov findings diverge between per-shop and batched paths"
    # Confirm the drifts were caught
    by_check = {(f.check, f.shop_domain) for f in batch_findings}
    assert ("order_collapse", SHOPS[2]) in by_check
    assert ("aov_drift", SHOPS[3]) in by_check


def test_batch_with_empty_shop_list_returns_empty(db):
    assert _batch_check_attribution_drift(db, []) == []
    assert _batch_check_order_and_aov(db, []) == []
