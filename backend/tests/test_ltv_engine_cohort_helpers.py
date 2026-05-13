"""
Unit tests for the pure helpers extracted from `get_cohorts_by_dimension`
in the 2026-05-12 A3 refactor (commit 5a8b5b6).

End-to-end coverage exists at `/pro/cohorts-by-dimension`; this file is
the structural unit gate for:
  - `_customer_key` — deterministic identity resolution (id > email)
  - `_aggregate_customer_timelines` — order-row → per-customer timeline
  - `_build_bucket` — per-dim bucket: size/repeat_rate/revenue/cohort_months
  - `_compute_best_vs_worst` — best vs worst insight composer

The cohort revenue-per-customer + repeat-rate numbers shown on the LTV
panel come directly from these helpers; any silent drift would alter
the merchant's understanding of which acquisition channel retains best.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.services.ltv_engine import (
    _aggregate_customer_timelines,
    _build_bucket,
    _compute_best_vs_worst,
    _customer_key,
)


# ---------------------------------------------------------------------------
# _customer_key — identity resolution
# ---------------------------------------------------------------------------


class TestCustomerKey:
    def test_id_wins_over_email(self):
        assert _customer_key(123, "a@b.com") == "id:123"

    def test_email_when_no_id(self):
        assert _customer_key(None, "a@b.com") == "email:a@b.com"

    def test_email_lowercased(self):
        assert _customer_key(None, "  A@B.COM  ") == "email:a@b.com"

    def test_none_when_no_identity(self):
        assert _customer_key(None, None) is None

    def test_empty_string_email_returns_none(self):
        # Empty/whitespace email is falsy, falls through to None
        assert _customer_key(None, "") is None


# ---------------------------------------------------------------------------
# _aggregate_customer_timelines
# ---------------------------------------------------------------------------


def _row(customer_id, email, created_at, price, dim):
    """Build a SQL-row-shaped tuple."""
    return (customer_id, email, created_at, price, dim)


class TestAggregateCustomerTimelines:
    def test_single_customer_single_order(self):
        rows = [_row(1, None, datetime(2026, 5, 1), 50.0, "google")]
        orders, first_dim, first_ts, identifiable = (
            _aggregate_customer_timelines(rows)
        )
        assert identifiable == 1
        assert orders == {"id:1": [(datetime(2026, 5, 1), 50.0)]}
        assert first_dim == {"id:1": "google"}
        assert first_ts == {"id:1": datetime(2026, 5, 1)}

    def test_first_order_dim_wins(self):
        rows = [
            _row(1, None, datetime(2026, 5, 1), 50.0, "google"),
            _row(1, None, datetime(2026, 5, 15), 30.0, "facebook"),
        ]
        _, first_dim, _, _ = _aggregate_customer_timelines(rows)
        # First-encountered dim wins, even if customer later orders via another channel
        assert first_dim == {"id:1": "google"}

    def test_skips_rows_without_identity(self):
        rows = [
            _row(None, None, datetime(2026, 5, 1), 50.0, "google"),
            _row(1, None, datetime(2026, 5, 2), 30.0, "facebook"),
        ]
        orders, _, _, identifiable = _aggregate_customer_timelines(rows)
        assert identifiable == 1
        assert "id:1" in orders

    def test_multiple_customers(self):
        rows = [
            _row(1, None, datetime(2026, 5, 1), 50.0, "google"),
            _row(2, None, datetime(2026, 5, 2), 30.0, "facebook"),
            _row(1, None, datetime(2026, 5, 3), 20.0, "google"),
        ]
        orders, _, _, identifiable = _aggregate_customer_timelines(rows)
        assert identifiable == 3
        assert len(orders["id:1"]) == 2
        assert len(orders["id:2"]) == 1

    def test_dim_value_falls_back_when_null(self):
        rows = [_row(1, None, datetime(2026, 5, 1), 50.0, None)]
        _, first_dim, _, _ = _aggregate_customer_timelines(rows)
        assert first_dim == {"id:1": "(unknown)"}

    def test_email_identity_used_when_no_id(self):
        rows = [_row(None, "x@y.com", datetime(2026, 5, 1), 50.0, "google")]
        orders, _, _, _ = _aggregate_customer_timelines(rows)
        assert "email:x@y.com" in orders


# ---------------------------------------------------------------------------
# _build_bucket
# ---------------------------------------------------------------------------


class TestBuildBucket:
    def test_size_equals_member_count(self):
        members = ["id:1", "id:2", "id:3"]
        customer_orders = {
            "id:1": [(datetime(2026, 5, 1), 100.0)],
            "id:2": [(datetime(2026, 5, 1), 50.0)],
            "id:3": [(datetime(2026, 5, 1), 30.0)],
        }
        customer_first_ts = {ck: datetime(2026, 5, 1) for ck in members}
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        assert b["size"] == 3

    def test_repeat_rate_zero_when_all_single_orders(self):
        members = ["id:1", "id:2"]
        customer_orders = {
            "id:1": [(datetime(2026, 5, 1), 100.0)],
            "id:2": [(datetime(2026, 5, 1), 50.0)],
        }
        customer_first_ts = {ck: datetime(2026, 5, 1) for ck in members}
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        assert b["repeat_rate"] == 0.0

    def test_repeat_rate_counts_distinct_months(self):
        members = ["id:1", "id:2"]
        customer_orders = {
            # 2 orders, same month → NOT repeat
            "id:1": [(datetime(2026, 5, 1), 50.0), (datetime(2026, 5, 20), 30.0)],
            # 2 orders, different months → repeat
            "id:2": [(datetime(2026, 5, 1), 50.0), (datetime(2026, 6, 5), 30.0)],
        }
        customer_first_ts = {ck: datetime(2026, 5, 1) for ck in members}
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        # 1 of 2 repeated → 0.5
        assert b["repeat_rate"] == 0.5

    def test_revenue_per_customer(self):
        members = ["id:1", "id:2"]
        customer_orders = {
            "id:1": [(datetime(2026, 5, 1), 100.0), (datetime(2026, 6, 1), 50.0)],
            "id:2": [(datetime(2026, 5, 1), 50.0)],
        }
        customer_first_ts = {ck: datetime(2026, 5, 1) for ck in members}
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        # Total revenue = 200; /2 customers = 100
        assert b["revenue_per_customer"] == 100.0

    def test_orders_per_customer(self):
        members = ["id:1", "id:2"]
        customer_orders = {
            "id:1": [(datetime(2026, 5, 1), 50.0), (datetime(2026, 6, 1), 50.0)],
            "id:2": [(datetime(2026, 5, 1), 50.0)],
        }
        customer_first_ts = {ck: datetime(2026, 5, 1) for ck in members}
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        # 3 orders / 2 customers = 1.5
        assert b["orders_per_customer"] == 1.5

    def test_dim_value_truncated_to_128_chars(self):
        long_dim = "x" * 200
        b = _build_bucket(long_dim, [], {}, {})
        assert len(b["dim_value"]) == 128

    def test_cohort_months_sorted_desc(self):
        members = ["id:1", "id:2", "id:3"]
        customer_orders = {ck: [(datetime(2026, 5, 1), 10.0)] for ck in members}
        customer_first_ts = {
            "id:1": datetime(2026, 5, 1),
            "id:2": datetime(2026, 4, 1),
            "id:3": datetime(2026, 6, 1),
        }
        b = _build_bucket("google", members, customer_orders, customer_first_ts)
        months = [c["cohort_month"] for c in b["cohort_months"]]
        # Descending order
        assert months == ["2026-06", "2026-05", "2026-04"]

    def test_empty_bucket(self):
        b = _build_bucket("google", [], {}, {})
        assert b["size"] == 0
        assert b["repeat_rate"] == 0.0
        assert b["revenue_per_customer"] == 0.0
        assert b["cohort_months"] == []


# ---------------------------------------------------------------------------
# _compute_best_vs_worst
# ---------------------------------------------------------------------------


def _bucket(dim_value: str, size: int, repeat_rate: float) -> dict:
    return {
        "dim_value": dim_value,
        "size": size,
        "repeat_rate": repeat_rate,
        "revenue_per_customer": 0,
        "orders_per_customer": 0,
        "cohort_months": [],
    }


class TestComputeBestVsWorst:
    def test_canonical_insight(self):
        # Google: 30% repeat; Facebook: 10% repeat; lift = (0.30-0.10)/0.10 = 200%
        buckets = [
            _bucket("google", 20, 0.30),
            _bucket("facebook", 20, 0.10),
        ]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["best_dim_value"] == "google"
        assert result["worst_dim_value"] == "facebook"
        assert result["lift_pct"] == 200.0
        # Insight uses "Lean into the channel" framing for ≥5% lift
        assert "Lean into" in result["insight"]

    def test_default_when_fewer_than_2_buckets(self):
        buckets = [_bucket("google", 20, 0.30)]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["best_dim_value"] is None
        assert "Need at least 2" in result["insight"]

    def test_default_when_buckets_too_small(self):
        # Both buckets have size<5 → cold-start guard kicks in
        buckets = [_bucket("google", 3, 0.5), _bucket("facebook", 4, 0.1)]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["best_dim_value"] is None

    def test_only_one_bucket_meets_size_floor(self):
        # google has 20 (≥5), facebook has 3 (<5) → only 1 qualifying bucket
        buckets = [_bucket("google", 20, 0.30), _bucket("facebook", 3, 0.10)]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["best_dim_value"] is None

    def test_similar_buckets_get_similar_insight(self):
        # Lift below 5% → "not a meaningful retention lever yet"
        buckets = [
            _bucket("google", 20, 0.102),  # 0.102
            _bucket("facebook", 20, 0.100),  # 0.100; lift ≈ 2%
        ]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert "similar" in result["insight"].lower()
        assert "meaningful retention lever" in result["insight"]

    def test_zero_worst_repeat_returns_none_lift(self):
        # When worst has 0 repeat, lift_pct is None (div-by-zero guard)
        buckets = [
            _bucket("google", 20, 0.5),
            _bucket("facebook", 20, 0.0),
        ]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["lift_pct"] is None

    def test_returns_default_when_best_equals_worst(self):
        # Same dim_value (e.g., all buckets are 'google') → no comparison
        buckets = [
            _bucket("google", 20, 0.30),
            _bucket("google", 20, 0.30),
        ]
        result = _compute_best_vs_worst(buckets, "first_channel")
        assert result["best_dim_value"] is None
