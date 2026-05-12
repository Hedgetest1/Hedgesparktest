"""Contract test for data_integrity_probe batched drift detection.

This is a behavioral contract test: it seeds known patterns into
shop_orders + visitor_purchase_sessions, runs the batched checks
once, and asserts the exact DriftFinding shape the functions promise
to produce for each pattern.

History: this file replaces test_data_integrity_probe_batch_parity.py,
which compared batched-vs-per-shop output. The per-shop versions
(_check_attribution_drift / _check_order_collapse / _check_aov_drift)
were deleted in the same commit — keeping them alive solely as a
parity-test backstop was a soft tampone. The batched functions are
the single source of truth; the contract is asserted directly here.

If a future schema change requires updating the batched query, the
contract assertions surface the divergence — no shadow implementation
to compare against.
"""
from sqlalchemy import text

from app.services.data_integrity_probe import (
    _batch_check_attribution_drift,
    _batch_check_order_and_aov,
)


SHOPS = [
    "drift-contract-clean.myshopify.com",
    "drift-contract-attr-drop.myshopify.com",
    "drift-contract-order-collapse.myshopify.com",
    "drift-contract-aov-spike.myshopify.com",
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


def test_attribution_drift_critical_drop_emits_finding(db):
    """90% baseline → 30% recent (60pp drop) → critical finding."""
    _seed_merchants(db, SHOPS[:2])
    # Clean shop: 90% both windows → no finding
    _seed_orders(db, SHOPS[0], recent_count=50, baseline_count=50)
    _seed_attribution(db, SHOPS[0], recent_attributed=45, baseline_attributed=45)
    # Drift shop: 30% recent vs 90% baseline → critical (≥20pp)
    _seed_orders(db, SHOPS[1], recent_count=50, baseline_count=50)
    _seed_attribution(db, SHOPS[1], recent_attributed=15, baseline_attributed=45)

    findings = _batch_check_attribution_drift(db, SHOPS[:2])

    # Exactly one finding, on the drift shop only
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "attribution_drift"
    assert f.shop_domain == SHOPS[1]
    assert f.severity == "critical"  # drop_pp=60 ≥ 20

    d = f.detail
    assert d["recent_window_days"] == 7
    assert d["baseline_window_days"] == 23
    assert d["recent_total_orders"] == 50
    assert d["recent_attributed"] == 15
    assert d["recent_rate_pct"] == 30.0
    assert d["baseline_total_orders"] == 50
    assert d["baseline_attributed"] == 45
    assert d["baseline_rate_pct"] == 90.0
    assert d["drop_pp"] == 60.0


def test_attribution_drift_below_threshold_no_finding(db):
    """Drop < 10pp → no finding."""
    shop = "drift-contract-attr-stable.myshopify.com"
    _seed_merchants(db, [shop])
    # 90% baseline, 85% recent → drop=5pp (below _ATTRIBUTION_DROP_PP=10.0)
    _seed_orders(db, shop, recent_count=40, baseline_count=40)
    _seed_attribution(db, shop, recent_attributed=34, baseline_attributed=36)

    assert _batch_check_attribution_drift(db, [shop]) == []


def test_attribution_drift_below_min_orders_no_finding(db):
    """Recent or baseline below _MIN_ORDERS_FOR_ATTRIBUTION (30) → skip."""
    shop = "drift-contract-attr-tiny.myshopify.com"
    _seed_merchants(db, [shop])
    # 10 orders both windows — below threshold even with massive drop
    _seed_orders(db, shop, recent_count=10, baseline_count=10)
    _seed_attribution(db, shop, recent_attributed=1, baseline_attributed=9)

    assert _batch_check_attribution_drift(db, [shop]) == []


def test_order_collapse_emits_warning_finding(db):
    """5 orders in 7d vs 50 in 23d (ratio≈0.33) → warning."""
    _seed_merchants(db, [SHOPS[2]])
    _seed_orders(db, SHOPS[2], recent_count=5, baseline_count=50)

    findings = _batch_check_order_and_aov(db, [SHOPS[2]])

    # Should find order_collapse, not aov_drift (AOVs are equal)
    collapse = [f for f in findings if f.check == "order_collapse"]
    assert len(collapse) == 1
    f = collapse[0]
    assert f.shop_domain == SHOPS[2]
    assert f.severity == "warning"  # ratio=0.33, > 0.2 critical threshold

    d = f.detail
    assert d["recent_orders"] == 5
    assert d["baseline_orders"] == 50
    # Numeric tolerance — daily division produces non-clean fractions
    assert abs(d["recent_per_day"] - 5 / 7) < 0.01
    assert abs(d["baseline_per_day"] - 50 / 23) < 0.01
    assert 0.32 <= d["ratio"] <= 0.34

    # No aov_drift since AOVs are equal
    assert [f for f in findings if f.check == "aov_drift"] == []


def test_aov_spike_emits_warning_finding(db):
    """4x AOV spike → warning."""
    _seed_merchants(db, [SHOPS[3]])
    _seed_orders(db, SHOPS[3], recent_count=50, baseline_count=50,
                 recent_aov=200.0, baseline_aov=50.0)

    findings = _batch_check_order_and_aov(db, [SHOPS[3]])

    # Should find aov_drift, not order_collapse (counts equal)
    aov = [f for f in findings if f.check == "aov_drift"]
    assert len(aov) == 1
    f = aov[0]
    assert f.shop_domain == SHOPS[3]
    assert f.severity == "warning"

    d = f.detail
    assert d["recent_aov"] == 200.0
    assert d["recent_n"] == 50
    assert d["baseline_aov"] == 50.0
    assert d["baseline_n"] == 50
    assert d["ratio"] == 4.0
    assert d["direction"] == "spike"

    # No order_collapse since recent_per_day ≈ baseline_per_day×3 (50/7 vs 50/23)
    assert [f for f in findings if f.check == "order_collapse"] == []


def test_aov_within_band_no_finding(db):
    """AOV ratio in [0.75, 1.25] → no finding."""
    shop = "drift-contract-aov-stable.myshopify.com"
    _seed_merchants(db, [shop])
    # recent=60, baseline=50 → ratio=1.2 (within band)
    _seed_orders(db, shop, recent_count=50, baseline_count=50,
                 recent_aov=60.0, baseline_aov=50.0)

    findings = _batch_check_order_and_aov(db, [shop])
    assert [f for f in findings if f.check == "aov_drift"] == []


def test_batch_attribution_with_empty_shop_list_returns_empty(db):
    assert _batch_check_attribution_drift(db, []) == []


def test_batch_order_aov_with_empty_shop_list_returns_empty(db):
    assert _batch_check_order_and_aov(db, []) == []
