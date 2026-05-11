"""Sprint Pro #2 — recurring buyer cadence-detection tests.

Pure-function tests on the detection algorithm + DB-backed integration
tests on the full compute_recurring_analytics pipeline.

Coverage:
  - mask_email: localpart length edge cases
  - _classify_cadence: bucket boundaries
  - _classify_buyer: regularity gate, multi-currency skip, < 3 orders
  - compute_recurring_analytics: empty / < 10 buyers floor / happy path
  - at-risk detection: overdue threshold
  - churn detection: 60d active, 30d silent
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.recurring_buyer_analytics import (
    _classify_buyer,
    _classify_cadence,
    compute_recurring_analytics,
    mask_email,
)


# ---------------------------------------------------------------------------
# mask_email
# ---------------------------------------------------------------------------


def test_mask_email_long_localpart():
    assert mask_email("johndoe@gmail.com") == "j***@gmail.com"


def test_mask_email_single_char_localpart():
    assert mask_email("a@gmail.com") == "***@gmail.com"


def test_mask_email_no_at_sign():
    assert mask_email("notanemail") == "***"


def test_mask_email_empty():
    assert mask_email("") == "***"


# ---------------------------------------------------------------------------
# _classify_cadence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gap_days,expected", [
    (5, "weekly"),
    (7, "weekly"),
    (9, "weekly"),
    (10, None),       # gap between weekly and biweekly buckets
    (14, "biweekly"),
    (16, "biweekly"),
    (20, None),       # gap between biweekly and monthly
    (25, "monthly"),
    (30, "monthly"),
    (40, "monthly"),
    (60, None),       # gap before quarterly
    (91, "quarterly"),
    (4, None),        # too frequent
    (150, None),      # too rare
])
def test_classify_cadence_buckets(gap_days, expected):
    assert _classify_cadence(gap_days) == expected


# ---------------------------------------------------------------------------
# _classify_buyer
# ---------------------------------------------------------------------------


def _orders(prices_currencies_timestamps):
    """Helper: build the (price, currency, datetime) tuples list."""
    return [(p, c, t) for p, c, t in prices_currencies_timestamps]


def test_classify_buyer_fewer_than_3_orders_skipped():
    base = datetime(2026, 1, 1)
    orders = _orders([
        (50.0, "USD", base),
        (50.0, "USD", base + timedelta(days=30)),
    ])
    assert _classify_buyer("a@b.com", orders, "USD") is None


def test_classify_buyer_monthly_regular_pattern():
    base = datetime(2026, 1, 1)
    orders = _orders([
        (50.0, "USD", base + timedelta(days=i * 30)) for i in range(4)
    ])
    buyer = _classify_buyer("a@b.com", orders, "USD")
    assert buyer is not None
    assert buyer.cadence_kind == "monthly"
    assert buyer.orders_count == 4
    assert buyer.lifetime_revenue == 200.0


def test_classify_buyer_irregular_skipped():
    """Gaps with high CV → rejected by regularity gate."""
    base = datetime(2026, 1, 1)
    orders = _orders([
        (50.0, "USD", base),
        (50.0, "USD", base + timedelta(days=10)),
        (50.0, "USD", base + timedelta(days=11)),  # tiny gap
        (50.0, "USD", base + timedelta(days=80)),  # huge gap
    ])
    assert _classify_buyer("a@b.com", orders, "USD") is None


def test_classify_buyer_mixed_currencies_skipped():
    base = datetime(2026, 1, 1)
    orders = _orders([
        (50.0, "USD", base),
        (50.0, "EUR", base + timedelta(days=30)),
        (50.0, "USD", base + timedelta(days=60)),
    ])
    assert _classify_buyer("a@b.com", orders, "USD") is None


def test_classify_buyer_at_risk_when_overdue():
    """Last order > expected + cadence/4 → at_risk=True."""
    # Build a monthly buyer with regular gaps, then last order is 60d ago
    # which is > 30d (next expected) + 7.5d (overdue threshold).
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    orders = _orders([
        (50.0, "USD", now - timedelta(days=150)),
        (50.0, "USD", now - timedelta(days=120)),
        (50.0, "USD", now - timedelta(days=90)),
        (50.0, "USD", now - timedelta(days=60)),  # last order, 60d ago
    ])
    buyer = _classify_buyer("a@b.com", orders, "USD")
    assert buyer is not None
    assert buyer.cadence_kind == "monthly"
    assert buyer.is_at_risk is True


def test_classify_buyer_not_at_risk_when_on_pace():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now - timedelta(days=120)
    orders = _orders([
        (50.0, "USD", base + timedelta(days=i * 30)) for i in range(4)
    ])
    buyer = _classify_buyer("a@b.com", orders, "USD")
    assert buyer is not None
    # Last order at base + 90d = now - 30d → still within pace
    assert buyer.is_at_risk is False


# ---------------------------------------------------------------------------
# compute_recurring_analytics — DB-integration
# ---------------------------------------------------------------------------


def _seed_order(db, shop, email, price, currency, created_at,
                shopify_order_id):
    db.execute(text("""
        INSERT INTO shop_orders
          (shop_domain, shopify_order_id, total_price, currency,
           customer_email, line_items, created_at)
        VALUES (:s, :sid, :p, :c, :e, '[]'::jsonb, :ts)
    """), {"s": shop, "sid": shopify_order_id, "p": price, "c": currency,
           "e": email, "ts": created_at})


def test_empty_shop_returns_has_data_false(db):
    report = compute_recurring_analytics(db, "empty.myshopify.com")
    assert report.has_data is False
    assert report.recurring_count == 0
    assert "No orders" in (report.note or "")


def test_below_10_buyers_returns_has_data_false(db):
    """Statistical floor: < 10 distinct customer emails → has_data=False."""
    shop = "small.myshopify.com"
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
    for i in range(5):  # only 5 distinct customers
        for j in range(3):  # 3 orders each, monthly pattern
            _seed_order(
                db, shop,
                f"customer{i}@example.com",
                100.0, "USD",
                base + timedelta(days=j * 30),
                f"order_{i}_{j}",
            )
    report = compute_recurring_analytics(db, shop)
    assert report.has_data is False
    assert "Only 5 distinct buyers" in (report.note or "")


def test_full_analytics_with_15_monthly_buyers(db):
    """15 buyers with monthly cadence → all detected as recurring."""
    shop = "big.myshopify.com"
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
    for i in range(15):
        for j in range(4):
            _seed_order(
                db, shop,
                f"buyer{i}@example.com",
                100.0, "USD",
                base + timedelta(days=j * 30 + i),  # slight stagger
                f"o_{i}_{j}",
            )
    report = compute_recurring_analytics(db, shop)
    assert report.has_data is True
    assert report.recurring_count == 15
    assert report.currency == "USD"
    # MRR roughly = 15 buyers × $100/month each
    assert 1000 < report.mrr_estimate <= 2500
    # All buyers have cadence_kind="monthly"
    assert all(b.cadence_kind == "monthly" for b in report.buyers)


def test_irregular_buyers_excluded_from_count(db):
    """Mix: 10 regular monthly + 5 irregular → recurring_count = 10."""
    shop = "mixed.myshopify.com"
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=180)
    # 10 regular monthly
    for i in range(10):
        for j in range(4):
            _seed_order(
                db, shop,
                f"reg{i}@example.com",
                100.0, "USD",
                base + timedelta(days=j * 30),
                f"reg_{i}_{j}",
            )
    # 5 irregular (random gaps)
    irregular_gaps = [3, 45, 10, 90]
    for i in range(5):
        cumulative = 0
        for j, gap in enumerate(irregular_gaps):
            cumulative += gap
            _seed_order(
                db, shop,
                f"irr{i}@example.com",
                50.0, "USD",
                base + timedelta(days=cumulative),
                f"irr_{i}_{j}",
            )
    report = compute_recurring_analytics(db, shop)
    assert report.has_data is True
    # Should be exactly 10 (the regulars). Irregulars dropped by CV gate.
    assert report.recurring_count == 10


def test_churned_count_detection(db):
    """A monthly buyer active 60d-30d ago but silent in last 30d → churned."""
    shop = "churn.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Seed: 12 buyers, 10 active (last order well within last 30d), 2 churned
    # (last order solidly between 30-60d ago). Wider margins to avoid
    # boundary jitter from datetime.now() drift between seed + compute.
    for i in range(10):
        # Active: orders at now-105d, now-75d, now-45d, now-15d (last 15d ago)
        for j in range(4):
            _seed_order(
                db, shop,
                f"active{i}@example.com",
                100.0, "USD",
                now - timedelta(days=105 - j * 30),
                f"a_{i}_{j}",
            )
    for i in range(2):
        # Churned: orders at now-165d, now-135d, now-105d, now-45d (last 45d ago)
        # 45d ago is in the (60d, 30d) churn window
        for j in range(4):
            _seed_order(
                db, shop,
                f"churned{i}@example.com",
                100.0, "USD",
                now - timedelta(days=165 - j * 40),
                f"c_{i}_{j}",
            )
    report = compute_recurring_analytics(db, shop)
    assert report.has_data is True
    assert report.churned_30d == 2


def test_at_risk_count_in_report(db):
    """At-risk buyers surfaced in aggregate."""
    shop = "atrisk.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # 12 buyers, 10 on-pace (last order recent), 2 overdue.
    for i in range(10):
        for j in range(4):
            _seed_order(
                db, shop,
                f"ok{i}@example.com",
                100.0, "USD",
                now - timedelta(days=120 - j * 30),
                f"ok_{i}_{j}",
            )
    for i in range(2):
        # Last order 50 days ago = overdue for monthly cadence
        _seed_order(db, shop, f"late{i}@example.com", 100.0, "USD",
                    now - timedelta(days=140), f"late_{i}_a")
        _seed_order(db, shop, f"late{i}@example.com", 100.0, "USD",
                    now - timedelta(days=110), f"late_{i}_b")
        _seed_order(db, shop, f"late{i}@example.com", 100.0, "USD",
                    now - timedelta(days=80), f"late_{i}_c")
        _seed_order(db, shop, f"late{i}@example.com", 100.0, "USD",
                    now - timedelta(days=50), f"late_{i}_d")
    report = compute_recurring_analytics(db, shop)
    assert report.has_data is True
    assert report.at_risk_count == 2
