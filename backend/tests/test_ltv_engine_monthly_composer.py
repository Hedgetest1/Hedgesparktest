"""
Composer-level integration tests for `get_monthly_cohorts`.

The 2026-05-13 A3 refactor decomposed the 211-LOC god function into
a 30-LOC composer + 7 pure helpers. test_ltv_engine_monthly_helpers.py
(20 tests) locks each helper in isolation. This file locks the
composition: 1 IO seam + clamp + empty-state + customer-coverage
+ cohort sort/cap + overall metrics wiring.
"""
from __future__ import annotations

from datetime import datetime

from app.services import ltv_engine as lt


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _patch_fetch(monkeypatch, rows):
    """Stub _fetch_monthly_orders with deterministic rows or None."""
    monkeypatch.setattr(
        lt, "_fetch_monthly_orders",
        lambda db, s, since: list(rows) if rows is not None else None,
    )


# ---------------------------------------------------------------------------
# Months clamp
# ---------------------------------------------------------------------------


class TestMonthsClamp:
    def test_clamped_to_1_minimum(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com", months=0)
        assert out["window_months"] == 1

    def test_clamped_to_12_maximum(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com", months=99)
        assert out["window_months"] == 12

    def test_within_range_pass_through(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com", months=6)
        assert out["window_months"] == 6


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_query_failure_returns_empty(self, monkeypatch):
        # _fetch_monthly_orders returns None on failure
        _patch_fetch(monkeypatch, None)
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert out["cohorts"] == []

    def test_no_rows_returns_empty(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert out["cohorts"] == []

    def test_all_unidentifiable_returns_partial_empty(self, monkeypatch):
        # Rows exist but no customer_id AND no email → unidentifiable
        _patch_fetch(monkeypatch, [
            (None, None, datetime(2025, 3, 1), 50.0),
            (None, None, datetime(2025, 3, 2), 30.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        # Returns empty response but preserves total_orders count
        assert out["customer_coverage"]["total_orders"] == 2
        assert out["cohorts"] == []


# ---------------------------------------------------------------------------
# Customer coverage
# ---------------------------------------------------------------------------


class TestCustomerCoverage:
    def test_full_coverage_when_all_identified(self, monkeypatch):
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 50.0),
            (2, "b@x.com", datetime(2025, 3, 5), 30.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        c = out["customer_coverage"]
        assert c["total_orders"] == 2
        assert c["identifiable_orders"] == 2
        assert c["unidentifiable_orders"] == 0
        assert c["coverage_rate"] == 1.0

    def test_partial_coverage_with_mixed_identifiability(self, monkeypatch):
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 50.0),
            (None, None, datetime(2025, 3, 2), 30.0),
            (None, "c@x.com", datetime(2025, 3, 3), 20.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        c = out["customer_coverage"]
        assert c["total_orders"] == 3
        assert c["identifiable_orders"] == 2
        assert c["unidentifiable_orders"] == 1
        # 2/3 ≈ 0.667
        assert c["coverage_rate"] == 0.667


# ---------------------------------------------------------------------------
# Cohort assembly
# ---------------------------------------------------------------------------


class TestCohortAssembly:
    def test_single_customer_single_cohort(self, monkeypatch):
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 100.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert len(out["cohorts"]) == 1
        c = out["cohorts"][0]
        assert c["cohort_month"] == "2025-03"
        assert c["size"] == 1
        assert c["revenue_total"] == 100.0

    def test_multi_cohort_sorted_descending(self, monkeypatch):
        # 3 customers in 3 different months
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 100.0),
            (2, "b@x.com", datetime(2025, 5, 1), 50.0),
            (3, "c@x.com", datetime(2025, 7, 1), 25.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        months = [c["cohort_month"] for c in out["cohorts"]]
        assert months == sorted(months, reverse=True)
        # Newest cohort first
        assert months[0] == "2025-07"

    def test_cohorts_capped_at_window_months(self, monkeypatch):
        # Build 8 cohorts across 8 different months, request months=4
        rows = [
            (i + 1, f"v{i}@x.com", datetime(2025, i + 1, 1), 50.0)
            for i in range(8)
        ]
        _patch_fetch(monkeypatch, rows)
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com", months=4)
        assert len(out["cohorts"]) == 4
        assert out["window_months"] == 4

    def test_repeat_customer_assigned_to_first_month(self, monkeypatch):
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 100.0),
            (1, "a@x.com", datetime(2025, 6, 1), 50.0),  # repeat
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        # Customer's cohort = March (first order), not June
        cohort_months = {c["cohort_month"] for c in out["cohorts"]}
        assert "2025-03" in cohort_months
        assert "2025-06" not in cohort_months
        # March cohort has revenue from both orders (lifetime)
        march = next(c for c in out["cohorts"] if c["cohort_month"] == "2025-03")
        assert march["revenue_total"] == 150.0
        assert march["repeat_rate"] == 1.0  # 100% — the one customer is repeat


# ---------------------------------------------------------------------------
# Overall metrics
# ---------------------------------------------------------------------------


class TestOverall:
    def test_overall_keys_present(self, monkeypatch):
        _patch_fetch(monkeypatch, [(1, "a@x.com", datetime(2025, 3, 1), 100.0)])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert set(out["overall"].keys()) == {
            "total_customers", "repeat_customers", "repeat_rate",
            "avg_orders_per_customer", "avg_revenue_per_customer",
        }

    def test_overall_aggregates_across_cohorts(self, monkeypatch):
        # 3 customers, 1 of them a repeat
        _patch_fetch(monkeypatch, [
            (1, "a@x.com", datetime(2025, 3, 1), 100.0),
            (1, "a@x.com", datetime(2025, 6, 1), 50.0),  # repeat
            (2, "b@x.com", datetime(2025, 4, 1), 75.0),
            (3, "c@x.com", datetime(2025, 5, 1), 25.0),
        ])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        o = out["overall"]
        assert o["total_customers"] == 3
        assert o["repeat_customers"] == 1
        assert o["repeat_rate"] == round(1/3, 4)
        # 4 orders / 3 customers
        assert o["avg_orders_per_customer"] == round(4/3, 2)
        # 250 / 3
        assert o["avg_revenue_per_customer"] == round(250/3, 2)


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_top_level_keys(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert set(out.keys()) == {
            "window_months", "generated_at", "customer_coverage",
            "cohorts", "overall",
        }

    def test_generated_at_iso_with_z(self, monkeypatch):
        _patch_fetch(monkeypatch, [])
        out = lt.get_monthly_cohorts(db=None, shop_domain="x.myshopify.com")
        assert out["generated_at"].endswith("Z")
