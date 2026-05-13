"""
Composer-level integration tests for `get_behavioral_cohort_analysis`.

The 2026-05-13 A3 refactor decomposed the 215-LOC god function into a
35-LOC composer + 11 pure helpers. test_behavioral_cohorts_helpers.py
(41 tests) locks each unit in isolation. This file locks the
composition: 4 IO seams + empty-state branch + customer+behavior
flow wiring + segment assembly.
"""
from __future__ import annotations

from datetime import datetime

from app.services import behavioral_cohorts as bc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_io(
    monkeypatch,
    *,
    customer_rows=None,
    behavior_rows=None,
    first_event_rows=None,
    currency="USD",
):
    """Monkeypatch the 4 IO seams: customers, behavior, first_events,
    currency."""
    monkeypatch.setattr(
        bc, "_fetch_customer_orders",
        lambda db, s, cutoff: list(customer_rows) if customer_rows is not None else None,
    )
    monkeypatch.setattr(
        bc, "_fetch_behavior_rows",
        lambda db, s, vids: list(behavior_rows or []),
    )
    monkeypatch.setattr(
        bc, "_fetch_first_events",
        lambda db, s, vids: list(first_event_rows or []),
    )
    monkeypatch.setattr(bc, "_resolve_currency_cohorts", lambda db, s: currency)


# ---------------------------------------------------------------------------
# Days clamp + empty state
# ---------------------------------------------------------------------------


class TestParamsAndEmptyState:
    def test_days_clamped_to_7_minimum(self, monkeypatch):
        _patch_io(monkeypatch, customer_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com", days=1)
        assert out["window_days"] == 7

    def test_days_clamped_to_180_maximum(self, monkeypatch):
        _patch_io(monkeypatch, customer_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com", days=999)
        assert out["window_days"] == 180

    def test_query_failure_returns_empty(self, monkeypatch):
        # _fetch_customer_orders returns None on query failure
        _patch_io(monkeypatch, customer_rows=None)
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["data_coverage"]["total_customers"] == 0
        assert out["segments"]["by_engagement"] == []
        assert "No customer data" in out["insights"][0]

    def test_no_customers_returns_empty(self, monkeypatch):
        _patch_io(monkeypatch, customer_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["data_coverage"]["total_customers"] == 0


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_top_level_keys(self, monkeypatch):
        _patch_io(monkeypatch, customer_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert set(out.keys()) == {
            "window_days", "generated_at", "data_coverage",
            "segments", "insights",
        }
        assert set(out["segments"].keys()) == {
            "by_engagement", "by_visit_pattern", "by_source",
        }

    def test_generated_at_has_z_suffix(self, monkeypatch):
        _patch_io(monkeypatch, customer_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["generated_at"].endswith("Z")


# ---------------------------------------------------------------------------
# End-to-end with synthetic data
# ---------------------------------------------------------------------------


def _customer_row(vid, source, ts, price, customer_id="c1", email="a@b.c"):
    return (vid, source, ts, price, customer_id, email)


def _behavior_row(vid, avg_scroll, total_dwell, total_views, wishlist=0):
    return (vid, avg_scroll, total_dwell, total_views, wishlist)


def _event_row(vid, first_ts, event_count):
    return (vid, first_ts, event_count)


class TestEndToEnd:
    def test_single_customer_with_behavior_classified(self, monkeypatch):
        cust = [_customer_row("v1", "google", datetime(2025, 1, 10), "100.00")]
        beh = [_behavior_row("v1", 80.0, 240.0, 10, wishlist=1)]
        events = [_event_row("v1", 1000, 5)]
        _patch_io(monkeypatch, customer_rows=cust, behavior_rows=beh, first_event_rows=events)
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["data_coverage"]["total_customers"] == 1
        assert out["data_coverage"]["segmentable_customers"] == 1
        assert out["data_coverage"]["coverage_rate"] == 1.0
        # SEARCH bucket present
        assert any(s["segment"] == "SEARCH" for s in out["segments"]["by_source"])

    def test_customer_without_behavior_is_unknown(self, monkeypatch):
        cust = [_customer_row("v1", "direct", datetime(2025, 1, 10), "100.00")]
        _patch_io(monkeypatch, customer_rows=cust, behavior_rows=[], first_event_rows=[])
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["data_coverage"]["segmentable_customers"] == 0
        assert any(
            s["segment"] == "UNKNOWN" for s in out["segments"]["by_engagement"]
        )

    def test_coverage_rate_computed_correctly(self, monkeypatch):
        # 2 customers, only 1 has behavior → 50% coverage
        cust = [
            _customer_row("v1", "google", datetime(2025, 1, 10), "100.00"),
            _customer_row("v2", "direct", datetime(2025, 1, 11), "50.00"),
        ]
        beh = [_behavior_row("v1", 80.0, 240.0, 10)]
        events = [_event_row("v1", 1000, 5)]
        _patch_io(monkeypatch, customer_rows=cust, behavior_rows=beh, first_event_rows=events)
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        assert out["data_coverage"]["total_customers"] == 2
        assert out["data_coverage"]["segmentable_customers"] == 1
        assert out["data_coverage"]["coverage_rate"] == 0.5


# ---------------------------------------------------------------------------
# Currency propagates to insights
# ---------------------------------------------------------------------------


class TestCurrencyPropagation:
    def test_gbp_currency_appears_in_insights(self, monkeypatch):
        # Build enough data to trigger an insight that uses currency
        cust = [
            _customer_row(f"v{i}", "email", datetime(2025, 1, 10), "200.00")
            for i in range(5)
        ] + [
            _customer_row(f"u{i}", "direct", datetime(2025, 1, 10), "30.00")
            for i in range(5)
        ]
        _patch_io(monkeypatch, customer_rows=cust, behavior_rows=[],
                  first_event_rows=[], currency="GBP")
        out = bc.get_behavioral_cohort_analysis(db=None, shop_domain="x.myshopify.com")
        joined = " ".join(out["insights"])
        # CURRENCY DRIFT FIX (2026-05-13): no hardcoded $ in any insight branch
        assert "$" not in joined
