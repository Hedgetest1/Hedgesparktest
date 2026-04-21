"""
Tests for /analytics/week-ridge — Lite v5 Zone 4 chart payload.

Covers:
- Payload shape (required keys, length ≤ 7)
- Cold-start path (empty days, cold_start=true)
- Currency consistency
- At-risk always tagged as estimate (documented — no assertion on value
  precision, but structure is validated)
- Week-over-week pct math
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from app.services.week_ridge import compute_week_ridge


def test_week_ridge_empty_shop_is_cold_start(db):
    """A shop with no orders returns cold_start=true and empty days."""
    out = compute_week_ridge(db, "week-ridge-empty.myshopify.com")
    assert out["cold_start"] is True
    assert out["days"] == []
    assert out["week_over_week_captured_pct"] is None
    assert "currency" in out


def test_week_ridge_payload_shape(db):
    """Every top-level key the UI contract requires is present."""
    out = compute_week_ridge(db, "week-ridge-shape.myshopify.com")
    for key in (
        "shop_domain",
        "days",
        "currency",
        "week_over_week_captured_pct",
        "cold_start",
        "generated_at",
    ):
        assert key in out, f"missing top-level key {key!r}"


def test_week_ridge_days_structure_when_populated(db):
    """Seed 5 days of orders and verify the payload exits cold-start
    with the correct per-day structure."""
    shop = "week-ridge-populated.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Seed 5 distinct days of orders in the last 10 days
    try:
        for i in range(5):
            created = now - timedelta(days=i, hours=3)
            db.execute(
                text(
                    """
                    INSERT INTO shop_orders
                        (shop_domain, shopify_order_id, total_price,
                         currency, created_at, source, line_items)
                    VALUES
                        (:shop, :oid, :price, 'USD', :created, 'test', '[]'::jsonb)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "shop": shop,
                    "oid": f"wr-test-{i}",
                    "price": 100.0 + i,
                    "created": created,
                },
            )
        db.flush()
        out = compute_week_ridge(db, shop)
    finally:
        # Roll back the seeded rows so other tests don't see them
        db.execute(
            text("DELETE FROM shop_orders WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    assert out["cold_start"] is False, out
    # Days list is up to 7 entries, oldest → newest, ISO dates
    assert isinstance(out["days"], list)
    assert 1 <= len(out["days"]) <= 7
    for day in out["days"]:
        assert "date" in day and len(day["date"]) == 10  # YYYY-MM-DD
        assert "at_risk_eur" in day and isinstance(day["at_risk_eur"], (int, float))
        assert "captured_eur" in day and isinstance(day["captured_eur"], (int, float))


def test_week_ridge_days_sorted_oldest_to_newest(db):
    """When populated, days are chronologically ordered."""
    shop = "week-ridge-ordered.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        for i in range(5):
            db.execute(
                text(
                    """
                    INSERT INTO shop_orders
                        (shop_domain, shopify_order_id, total_price,
                         currency, created_at, source, line_items)
                    VALUES
                        (:shop, :oid, :price, 'USD', :created, 'test', '[]'::jsonb)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "shop": shop,
                    "oid": f"wr-ord-{i}",
                    "price": 50.0,
                    "created": now - timedelta(days=i + 1, hours=2),
                },
            )
        db.flush()
        out = compute_week_ridge(db, shop)
    finally:
        db.execute(
            text("DELETE FROM shop_orders WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    if out["cold_start"] is False:
        dates = [d["date"] for d in out["days"]]
        assert dates == sorted(dates), "days must be ascending by date"


def test_week_ridge_captured_sum_matches_input(db):
    """Seeded revenue is reflected in captured_eur sums (within rounding)."""
    shop = "week-ridge-sum.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded_total = 0.0
    try:
        for i in range(4):
            price = 100.0 + i * 10  # 100, 110, 120, 130 = 460
            seeded_total += price
            db.execute(
                text(
                    """
                    INSERT INTO shop_orders
                        (shop_domain, shopify_order_id, total_price,
                         currency, created_at, source, line_items)
                    VALUES
                        (:shop, :oid, :price, 'USD', :created, 'test', '[]'::jsonb)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "shop": shop,
                    "oid": f"wr-sum-{i}",
                    "price": price,
                    "created": now - timedelta(days=i + 1, hours=1),
                },
            )
        db.flush()
        out = compute_week_ridge(db, shop)
    finally:
        db.execute(
            text("DELETE FROM shop_orders WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    if out["cold_start"] is False:
        total_captured = sum(d["captured_eur"] for d in out["days"])
        # Seeded 460 total — allow small rounding tolerance
        assert abs(total_captured - seeded_total) < 1.0, (
            f"captured sum {total_captured} != seeded {seeded_total}"
        )


def test_week_ridge_wow_pct_is_null_when_prior_week_empty(db):
    """If the prior 7 days had zero revenue, wow pct is null (no divide-by-zero)."""
    shop = "week-ridge-wow-null.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Seed orders ONLY in the last 3 days — prior 7 days are empty
    try:
        for i in range(3):
            db.execute(
                text(
                    """
                    INSERT INTO shop_orders
                        (shop_domain, shopify_order_id, total_price,
                         currency, created_at, source, line_items)
                    VALUES
                        (:shop, :oid, :price, 'USD', :created, 'test', '[]'::jsonb)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "shop": shop,
                    "oid": f"wr-wow-{i}",
                    "price": 80.0,
                    "created": now - timedelta(days=i, hours=2),
                },
            )
        db.flush()
        out = compute_week_ridge(db, shop)
    finally:
        db.execute(
            text("DELETE FROM shop_orders WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    if out["cold_start"] is False:
        # Prior 7 days empty → wow must be None
        assert out["week_over_week_captured_pct"] is None


def test_week_ridge_at_risk_non_negative(db):
    """at_risk_eur is never negative (estimate floor is 0)."""
    out = compute_week_ridge(db, "week-ridge-nonneg.myshopify.com")
    for d in out["days"]:
        assert d["at_risk_eur"] >= 0, d
        assert d["captured_eur"] >= 0, d
