"""
Tests for the industry benchmarks killer feature (2026-04-11 killer sprint).

Benchmarks compute merchant percentile vs peers in the same revenue band,
loss-framed with recovery-to-p75 estimates, privacy-preserving via N>=10
k-anonymity, cached in Redis 6h.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.merchant import Merchant
from app.models.shop_order import ShopOrder
from app.services.benchmarks import (
    _classify_band,
    _percentile,
    _percentile_rank,
    _recovery_estimate_eur,
    get_merchant_benchmark_report,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_orders_for_shop(db, shop: str, count: int, price: float,
                        days_ago_start: int = 0, days_ago_end: int = 30):
    """Plant `count` orders distributed across a day window."""
    for i in range(count):
        day = days_ago_start + (i * (days_ago_end - days_ago_start)) // max(count, 1)
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"gid://{shop}/order/bench_{i}_{price}",
            total_price=price,
            currency="EUR",
            line_items=[],
            created_at=_now() - timedelta(days=day, hours=i),
        ))
    db.flush()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_classify_band_micro():
    assert _classify_band(500.0) == "micro"


def test_classify_band_small():
    assert _classify_band(5_000.0) == "small"


def test_classify_band_mid():
    assert _classify_band(25_000.0) == "mid"


def test_classify_band_large():
    assert _classify_band(75_000.0) == "large"


def test_classify_band_xlarge():
    assert _classify_band(500_000.0) == "xlarge"


def test_percentile_pure_function():
    vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert _percentile(vals, 50) == 55.0  # median between 50 and 60
    assert _percentile(vals, 25) == 32.5
    assert _percentile(vals, 75) == 77.5


def test_percentile_rank_exact_match():
    """A merchant at the median should be p50."""
    sorted_vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    rank = _percentile_rank(50, sorted_vals)
    assert 40 <= rank <= 60


def test_percentile_rank_top():
    """A merchant at the max value should be in the top decile."""
    sorted_vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    rank = _percentile_rank(100, sorted_vals)
    assert rank >= 90


def test_recovery_to_p75_zero_when_above():
    """A merchant already above p75 has zero recovery potential."""
    assert _recovery_estimate_eur(
        "monthly_revenue", current_value=15_000, p75=10_000,
        per_shop_m={"orders_per_day": 5, "aov": 50},
    ) == 0.0


def test_recovery_to_p75_for_monthly_revenue():
    """Recovery for monthly_revenue is just the gap."""
    r = _recovery_estimate_eur(
        "monthly_revenue", current_value=8_000, p75=12_000,
        per_shop_m={"orders_per_day": 5, "aov": 50},
    )
    assert r == 4_000.0


def test_recovery_to_p75_for_aov():
    """Recovery for AOV is gap * orders_per_month."""
    r = _recovery_estimate_eur(
        "aov", current_value=50, p75=70,
        per_shop_m={"orders_per_day": 10, "aov": 50},
    )
    # gap=20, orders/month = 10*30 = 300, recovery = 20*300 = 6000
    assert r == 6_000.0


# ---------------------------------------------------------------------------
# Insufficient-peers path — privacy gate
# ---------------------------------------------------------------------------

def test_insufficient_peers_returns_note(db):
    """A fresh shop with no peers returns a clean note, never fake numbers."""
    shop = "lonely-shop.myshopify.com"
    db.add(Merchant(shop_domain=shop, plan="pro", billing_active=True,
                    install_status="active", session_version=0))
    _mk_orders_for_shop(db, shop, count=10, price=100.0)
    db.commit()

    report = get_merchant_benchmark_report(db, shop)
    # Either insufficient_peers note OR insufficient_shop_data
    assert "note" in report
    assert "peer" in report["note"].lower() or "shop_data" in report["note"]


def test_insufficient_shop_data_for_empty_shop(db):
    """A shop with <5 orders returns insufficient_shop_data."""
    shop = "empty-shop.myshopify.com"
    db.add(Merchant(shop_domain=shop, plan="pro", billing_active=True,
                    install_status="active", session_version=0))
    _mk_orders_for_shop(db, shop, count=2, price=100.0)
    db.commit()

    report = get_merchant_benchmark_report(db, shop)
    assert "note" in report
    assert "insufficient_shop_data" in report["note"]


# ---------------------------------------------------------------------------
# Full benchmark path — N>=10 peers in same band
# ---------------------------------------------------------------------------

def test_full_benchmark_returns_metrics_when_enough_peers(db):
    """With 12 peers in the small band, the report returns metrics."""
    # Plant 12 small-band shops (€3k-€15k/month)
    peers = []
    for i in range(12):
        shop = f"bench-peer-{i}.myshopify.com"
        db.add(Merchant(shop_domain=shop, plan="pro", billing_active=True,
                        install_status="active", session_version=0))
        # Spread revenues across the small band: €5k to €12k
        revenue = 5_000 + i * 600
        aov = 100 + i * 5
        count = int(revenue / aov)
        _mk_orders_for_shop(db, shop, count=count, price=aov)
        peers.append(shop)

    # The target shop lands at the lower end (below p50)
    target_shop = "bench-target.myshopify.com"
    db.add(Merchant(shop_domain=target_shop, plan="pro", billing_active=True,
                    install_status="active", session_version=0))
    # €4k revenue, 40 orders at AOV 100
    _mk_orders_for_shop(db, target_shop, count=40, price=100.0)
    db.commit()

    report = get_merchant_benchmark_report(db, target_shop)
    assert report["band"] == "small"
    assert report["peer_count"] >= 10, f"expected >=10 peers, got {report}"
    assert "metrics" in report
    assert "monthly_revenue" in report["metrics"]
    m = report["metrics"]["monthly_revenue"]
    assert "percentile_rank" in m
    assert "recovery_to_p75_eur" in m
    assert "narrative" in m
    assert "status" in m
