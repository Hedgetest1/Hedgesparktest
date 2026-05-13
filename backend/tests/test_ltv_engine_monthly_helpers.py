"""
Unit tests for the pure helpers extracted from `get_monthly_cohorts`
in the 2026-05-13 A3 refactor.

The composer is locked by test_ltv_engine_monthly_composer.py.
The 26 prior tests in test_ltv_engine_cohort_helpers.py cover
`get_cohorts_by_dimension` helpers (a different function in the
same module) — those stay untouched.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.services.ltv_engine import (
    _assign_cohorts,
    _build_cohort_record,
    _build_customer_timelines,
    _compute_cumulative_revenue,
    _compute_overall_metrics,
    _count_repeat_customers_in_cohort,
)


# ---------------------------------------------------------------------------
# _build_customer_timelines — order rows → timeline + identifiable count
# ---------------------------------------------------------------------------


class TestBuildCustomerTimelines:
    def test_identified_orders_collected(self):
        rows = [
            (123, "a@x.com", datetime(2025, 1, 1), 50.0),
            (123, "a@x.com", datetime(2025, 1, 5), 30.0),
        ]
        timelines, identifiable = _build_customer_timelines(rows)
        assert identifiable == 2
        # Both orders attributed to the same customer_id-keyed entry
        assert len(timelines) == 1
        key = next(iter(timelines))
        assert key.startswith("id:")
        assert len(timelines[key]) == 2

    def test_email_fallback_when_no_id(self):
        rows = [
            (None, "a@x.com", datetime(2025, 1, 1), 50.0),
        ]
        timelines, identifiable = _build_customer_timelines(rows)
        assert identifiable == 1
        key = next(iter(timelines))
        assert key.startswith("email:")

    def test_unidentifiable_excluded(self):
        # Order with NEITHER id NOR email → excluded
        rows = [
            (None, None, datetime(2025, 1, 1), 50.0),
            (1, "a@x.com", datetime(2025, 1, 2), 30.0),
        ]
        timelines, identifiable = _build_customer_timelines(rows)
        assert identifiable == 1
        assert len(timelines) == 1

    def test_null_price_treated_as_zero(self):
        rows = [
            (1, "a@x.com", datetime(2025, 1, 1), None),
        ]
        timelines, _ = _build_customer_timelines(rows)
        key = next(iter(timelines))
        assert timelines[key][0] == (datetime(2025, 1, 1), 0.0)


# ---------------------------------------------------------------------------
# _assign_cohorts — first-order month
# ---------------------------------------------------------------------------


class TestAssignCohorts:
    def test_single_customer_single_month(self):
        timelines = {"id:1": [(datetime(2025, 3, 15), 50.0)]}
        out = _assign_cohorts(timelines)
        assert "2025-03" in out
        assert out["2025-03"] == ["id:1"]

    def test_multi_purchase_uses_first_month(self):
        timelines = {
            "id:1": [
                (datetime(2025, 3, 15), 50.0),
                (datetime(2025, 5, 10), 30.0),
            ],
        }
        out = _assign_cohorts(timelines)
        # Customer's cohort = first-order month = 2025-03
        assert "2025-03" in out
        assert "2025-05" not in out

    def test_two_customers_different_cohorts(self):
        timelines = {
            "id:1": [(datetime(2025, 3, 15), 50.0)],
            "id:2": [(datetime(2025, 5, 1), 30.0)],
        }
        out = _assign_cohorts(timelines)
        assert "2025-03" in out
        assert "2025-05" in out


# ---------------------------------------------------------------------------
# _count_repeat_customers_in_cohort
# ---------------------------------------------------------------------------


class TestCountRepeatCustomers:
    def test_single_order_not_repeat(self):
        timelines = {"id:1": [(datetime(2025, 3, 1), 50.0)]}
        assert _count_repeat_customers_in_cohort(["id:1"], timelines) == 0

    def test_two_orders_same_month_not_repeat(self):
        """DISTINCT-MONTHS semantics: 2 orders in the same month do NOT
        count as repeat. The customer must have returned in a different
        month."""
        timelines = {
            "id:1": [
                (datetime(2025, 3, 1), 50.0),
                (datetime(2025, 3, 15), 30.0),
            ],
        }
        assert _count_repeat_customers_in_cohort(["id:1"], timelines) == 0

    def test_two_orders_different_months_is_repeat(self):
        timelines = {
            "id:1": [
                (datetime(2025, 3, 1), 50.0),
                (datetime(2025, 5, 15), 30.0),
            ],
        }
        assert _count_repeat_customers_in_cohort(["id:1"], timelines) == 1

    def test_partial_repeats(self):
        timelines = {
            "id:1": [
                (datetime(2025, 3, 1), 50.0),
                (datetime(2025, 5, 15), 30.0),
            ],
            "id:2": [(datetime(2025, 3, 1), 50.0)],
        }
        assert _count_repeat_customers_in_cohort(["id:1", "id:2"], timelines) == 1


# ---------------------------------------------------------------------------
# _compute_cumulative_revenue
# ---------------------------------------------------------------------------


class TestCumulativeRevenue:
    def test_age_zero_only_returns_one_row(self):
        timelines = {"id:1": [(datetime(2025, 3, 15), 100.0)]}
        cohort_start = datetime(2025, 3, 1)
        out = _compute_cumulative_revenue(["id:1"], timelines, cohort_start, max_age=0)
        assert len(out) == 1
        assert out[0]["month_age"] == 0
        assert out[0]["revenue"] == 100.0
        assert out[0]["customers_active"] == 1

    def test_revenue_accumulates_across_ages(self):
        timelines = {
            "id:1": [
                (datetime(2025, 3, 15), 100.0),  # age 0
                (datetime(2025, 4, 10), 50.0),   # age 1
                (datetime(2025, 5, 5), 25.0),    # age 2
            ],
        }
        cohort_start = datetime(2025, 3, 1)
        out = _compute_cumulative_revenue(["id:1"], timelines, cohort_start, max_age=3)
        assert len(out) == 4
        assert out[0]["revenue"] == 100.0
        assert out[1]["revenue"] == 150.0
        assert out[2]["revenue"] == 175.0
        # Age 3 had no orders
        assert out[3]["revenue"] == 175.0
        assert out[3]["month_revenue"] == 0.0
        assert out[3]["customers_active"] == 0

    def test_customers_active_dedup(self):
        # Single customer with 2 orders in the same month → 1 active
        timelines = {
            "id:1": [
                (datetime(2025, 3, 5), 50.0),
                (datetime(2025, 3, 25), 30.0),
            ],
        }
        cohort_start = datetime(2025, 3, 1)
        out = _compute_cumulative_revenue(["id:1"], timelines, cohort_start, max_age=0)
        assert out[0]["customers_active"] == 1


# ---------------------------------------------------------------------------
# _build_cohort_record
# ---------------------------------------------------------------------------


class TestBuildCohortRecord:
    def test_empty_members_returns_none(self):
        out = _build_cohort_record("2025-03", [], {}, months=6, now=datetime(2025, 12, 1))
        assert out is None

    def test_invalid_month_str_returns_none(self):
        timelines = {"id:1": [(datetime(2025, 3, 1), 100.0)]}
        out = _build_cohort_record("not-a-month", ["id:1"], timelines, months=6, now=datetime(2025, 12, 1))
        assert out is None

    def test_record_shape(self):
        timelines = {
            "id:1": [(datetime(2025, 3, 1), 100.0)],
            "id:2": [(datetime(2025, 3, 5), 50.0)],
        }
        out = _build_cohort_record("2025-03", ["id:1", "id:2"], timelines, months=6, now=datetime(2025, 12, 1))
        assert out is not None
        assert out["cohort_month"] == "2025-03"
        assert out["size"] == 2
        assert out["revenue_total"] == 150.0
        assert out["orders_total"] == 2
        assert out["orders_per_customer"] == 1.0
        assert out["revenue_per_customer"] == 75.0
        assert out["repeat_rate"] == 0.0  # neither customer has 2 distinct months
        assert isinstance(out["cumulative_revenue"], list)


# ---------------------------------------------------------------------------
# _compute_overall_metrics
# ---------------------------------------------------------------------------


class TestOverallMetrics:
    def test_empty_yields_zeros(self):
        out = _compute_overall_metrics({})
        assert out == {
            "total_customers": 0, "repeat_customers": 0, "repeat_rate": 0.0,
            "avg_orders_per_customer": 0.0, "avg_revenue_per_customer": 0.0,
        }

    def test_single_customer_one_order(self):
        timelines = {"id:1": [(datetime(2025, 3, 1), 100.0)]}
        out = _compute_overall_metrics(timelines)
        assert out["total_customers"] == 1
        assert out["repeat_customers"] == 0
        assert out["repeat_rate"] == 0.0
        assert out["avg_orders_per_customer"] == 1.0
        assert out["avg_revenue_per_customer"] == 100.0

    def test_repeat_customer_counted(self):
        # 2 customers, one with 2+ orders → 50% repeat rate
        timelines = {
            "id:1": [
                (datetime(2025, 3, 1), 100.0),
                (datetime(2025, 5, 1), 50.0),
            ],
            "id:2": [(datetime(2025, 4, 1), 25.0)],
        }
        out = _compute_overall_metrics(timelines)
        assert out["total_customers"] == 2
        assert out["repeat_customers"] == 1
        assert out["repeat_rate"] == 0.5
        assert out["avg_orders_per_customer"] == 1.5
        assert out["avg_revenue_per_customer"] == 87.5  # (150 + 25) / 2
