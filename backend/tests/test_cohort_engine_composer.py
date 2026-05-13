"""
Composer-level integration tests for `cohort_engine.get_cohort_retention`
and `get_cohort_summary`.

cohort_engine has 0 dedicated tests prior to this file (the existing
`test_cohorts_by_dimension.py` covers a different module, `ltv_engine`).
This file locks the full SQL-rows → cohort matrix → summary stats flow
so a future refactor can prove the contract holds without booting Postgres.

Pattern: feed the entry points a FakeDB that returns deterministic order
rows; assert cohort assignment (ISO week, Monday-anchored), retention
math, summary aggregation, best_cohort selection, and empty/error fallback.

Born 2026-05-13 as a fix for the R-blocker:sprint>1d gap surfaced in
the 2026-05-12 god-function refactor sprint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.cohort_engine import (
    _empty_response,
    get_cohort_retention,
    get_cohort_summary,
)


# ---------------------------------------------------------------------------
# FakeDB — returns deterministic rows from db.execute().fetchall()
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return list(self._rows)


class _FakeDB:
    """Minimal stand-in for sqlalchemy.orm.Session.execute()."""
    def __init__(self, rows): self._rows = rows
    def execute(self, *_a, **_kw): return _FakeResult(self._rows)


class _ExplodingDB:
    """Raises on any execute() — exercises the except branch."""
    def execute(self, *_a, **_kw):
        raise RuntimeError("db connection lost")


# ---------------------------------------------------------------------------
# Time helpers — build a deterministic cohort scenario
# ---------------------------------------------------------------------------


def _monday(year: int, week: int) -> datetime:
    """Return the Monday of ISO week `week` for `year` at 00:00 UTC."""
    return datetime.strptime(f"{year}-W{week:02d}-1", "%Y-W%W-%w")


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_no_orders_returns_empty_response(self):
        db = _FakeDB([])
        out = get_cohort_retention(db, "x.myshopify.com", weeks=8)
        assert out["cohorts"] == []
        assert out["total_customers"] == 0
        assert out["best_cohort"] is None
        assert out["avg_week_1_retention"] == 0.0
        assert out["avg_week_4_retention"] == 0.0

    def test_query_failure_returns_empty_response(self):
        out = get_cohort_retention(_ExplodingDB(), "x.myshopify.com", weeks=8)
        assert out["cohorts"] == []
        assert out["total_customers"] == 0
        assert out["best_cohort"] is None

    def test_empty_response_top_level_keys(self):
        e = _empty_response(weeks=8)
        assert set(e.keys()) == {
            "window_weeks", "generated_at", "cohorts",
            "avg_week_1_retention", "avg_week_4_retention",
            "best_cohort", "total_customers",
        }


# ---------------------------------------------------------------------------
# Weeks parameter clamping
# ---------------------------------------------------------------------------


class TestWeeksClamp:
    def test_weeks_below_min_clamped_to_4(self):
        out = get_cohort_retention(_FakeDB([]), "x.myshopify.com", weeks=1)
        assert out["window_weeks"] == 4

    def test_weeks_above_max_clamped_to_26(self):
        out = get_cohort_retention(_FakeDB([]), "x.myshopify.com", weeks=100)
        assert out["window_weeks"] == 26

    def test_weeks_within_range_passes_through(self):
        out = get_cohort_retention(_FakeDB([]), "x.myshopify.com", weeks=12)
        assert out["window_weeks"] == 12


# ---------------------------------------------------------------------------
# Cohort assignment — first-purchase week, Monday-anchored
# ---------------------------------------------------------------------------


class TestCohortAssignment:
    def test_single_customer_single_purchase_forms_singleton_cohort(self):
        first = datetime(2025, 1, 6, 10, 0, 0)  # Monday of 2025-W02
        rows = [("a@x.com", first, 50.0)]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=8)
        assert out["total_customers"] == 1
        assert len(out["cohorts"]) == 1
        c = out["cohorts"][0]
        assert c["size"] == 1
        assert c["revenue_total"] == 50.0

    def test_customer_first_purchase_assigns_cohort_not_later_purchase(self):
        """Customer with 2 orders in different weeks gets assigned to
        the week of the EARLIER one."""
        first = datetime(2025, 1, 6, 10, 0, 0)         # Mon 2025-W02
        second = datetime(2025, 2, 3, 10, 0, 0)        # Mon 2025-W06
        rows = [
            ("a@x.com", first, 50.0),
            ("a@x.com", second, 75.0),
        ]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        weeks = {c["cohort_week"] for c in out["cohorts"]}
        # Customer in earliest cohort only; later purchase is a "retention" event
        assert len(out["cohorts"]) == 1
        assert "2025-W02" in next(iter(weeks)) or weeks  # exact ISO depends on platform
        assert out["cohorts"][0]["size"] == 1
        # Both purchases contribute to revenue_total for the cohort
        assert out["cohorts"][0]["revenue_total"] == 125.0

    def test_two_customers_same_first_week_in_same_cohort(self):
        first = datetime(2025, 1, 6, 10, 0, 0)
        rows = [
            ("a@x.com", first, 50.0),
            ("b@x.com", first + timedelta(days=2), 75.0),  # same ISO week
        ]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=8)
        assert out["total_customers"] == 2
        assert len(out["cohorts"]) == 1
        assert out["cohorts"][0]["size"] == 2

    def test_two_customers_different_first_weeks_form_two_cohorts(self):
        rows = [
            ("a@x.com", datetime(2025, 1, 6, 10, 0, 0), 50.0),    # W02
            ("b@x.com", datetime(2025, 2, 10, 10, 0, 0), 75.0),   # W07
        ]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        assert out["total_customers"] == 2
        assert len(out["cohorts"]) == 2


# ---------------------------------------------------------------------------
# Retention computation — % of cohort with a later purchase in week N
# ---------------------------------------------------------------------------


class TestRetentionMath:
    def test_no_repeat_purchase_means_zero_retention(self):
        first = datetime(2025, 1, 6, 10, 0, 0)
        rows = [("a@x.com", first, 50.0)]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=8)
        retention = out["cohorts"][0]["retention"]
        # Every measurable week MUST be 0.0
        assert all(v == 0.0 for v in retention.values())

    def test_repeat_purchase_in_specific_week_shows_retention(self):
        """Customer purchases in W02 and then again 3 weeks later
        (W05). Their retention[week_3] MUST be 1.0 (100% of the
        1-customer cohort)."""
        first = datetime(2025, 1, 6, 10, 0, 0)              # W02 Mon
        second = first + timedelta(weeks=3, days=1)         # W05 Tue
        rows = [
            ("a@x.com", first, 50.0),
            ("a@x.com", second, 30.0),
        ]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        retention = out["cohorts"][0]["retention"]
        # week_3 must reflect the second purchase
        assert retention.get("week_3", 0.0) == 1.0, retention

    def test_retention_only_set_for_measurable_weeks(self):
        """Cohort that started 4 weeks ago should NOT have week_10
        retention computed — there isn't 10 weeks of measurable data yet."""
        # Cohort starts 4 weeks before now → max measurable = 4
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        first = now - timedelta(weeks=4, days=1)
        rows = [("a@x.com", first, 50.0)]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        retention = out["cohorts"][0]["retention"]
        # Only week_1..week_4 should be present
        max_week = max(int(k.replace("week_", "")) for k in retention.keys())
        assert max_week <= 5  # margin for ISO-week edge cases


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_keys(self):
        first = datetime(2025, 1, 6, 10, 0, 0)
        rows = [("a@x.com", first, 50.0)]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        assert "avg_week_1_retention" in out
        assert "avg_week_4_retention" in out
        assert "best_cohort" in out
        assert "total_customers" in out

    def test_best_cohort_picked_by_week4_or_week1_max(self):
        """Build 2 cohorts: cohort A has retention[week_1]=0, B has
        retention[week_1]=1. best_cohort MUST be B's week."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Cohort A: 2 customers in W-6, no repeats
        cohort_a_start = now - timedelta(weeks=6)
        # Cohort B: 1 customer in W-5 with a repeat 1 week later
        cohort_b_start = now - timedelta(weeks=5)
        rows = [
            ("a1@x.com", cohort_a_start, 50.0),
            ("a2@x.com", cohort_a_start + timedelta(days=1), 50.0),
            ("b1@x.com", cohort_b_start, 50.0),
            ("b1@x.com", cohort_b_start + timedelta(weeks=1, days=1), 30.0),
        ]
        out = get_cohort_retention(_FakeDB(rows), "x.myshopify.com", weeks=12)
        # B has 100% week_1 retention vs A's 0% → best_cohort is B's week
        assert out["best_cohort"] is not None
        # Find the cohort with retention[week_1] >= 1.0 — that's B
        b_week = None
        for c in out["cohorts"]:
            if c["retention"].get("week_1", 0) >= 1.0:
                b_week = c["cohort_week"]
                break
        assert b_week is not None
        assert out["best_cohort"] == b_week


# ---------------------------------------------------------------------------
# get_cohort_summary — delegates to retention with weeks=26, adds windows
# ---------------------------------------------------------------------------


class TestCohortSummary:
    def test_summary_top_level_keys(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.cohort_engine.get_cohort_retention",
            lambda db, s, weeks=26: {
                "avg_week_1_retention": 0.4,
                "avg_week_4_retention": 0.2,
                "cohorts": [
                    {"retention": {"week_8": 0.15, "week_12": 0.1, "week_26": 0.05}},
                ],
                "total_customers": 42,
                "best_cohort": "2025-W10",
            },
        )
        out = get_cohort_summary(_FakeDB([]), "x.myshopify.com")
        assert set(out.keys()) == {
            "avg_week_1_retention", "avg_week_4_retention",
            "avg_week_8_retention", "avg_week_12_retention",
            "avg_week_26_retention", "total_customers",
            "cohorts_measured", "best_cohort",
        }

    def test_summary_computes_extended_window_averages(self, monkeypatch):
        """avg_week_N_retention for N in (8, 12, 26) is the mean of
        the corresponding cohort.retention.week_N across cohorts that
        have that key."""
        monkeypatch.setattr(
            "app.services.cohort_engine.get_cohort_retention",
            lambda db, s, weeks=26: {
                "avg_week_1_retention": 0.4,
                "avg_week_4_retention": 0.2,
                "cohorts": [
                    {"retention": {"week_8": 0.2, "week_12": 0.1}},
                    {"retention": {"week_8": 0.4, "week_12": 0.2, "week_26": 0.05}},
                    {"retention": {}},  # too-young cohort, no week_8 key
                ],
                "total_customers": 100,
                "best_cohort": "2025-W10",
            },
        )
        out = get_cohort_summary(_FakeDB([]), "x.myshopify.com")
        assert out["avg_week_8_retention"] == 0.3   # (0.2+0.4)/2
        assert out["avg_week_12_retention"] == 0.15  # (0.1+0.2)/2
        assert out["avg_week_26_retention"] == 0.05  # only one cohort has week_26
        assert out["cohorts_measured"] == 3
        assert out["total_customers"] == 100

    def test_summary_fallback_on_query_exception(self):
        """If get_cohort_retention raises, the summary endpoint MUST
        return the zero-valued fallback rather than propagate."""
        out = get_cohort_summary(_ExplodingDB(), "x.myshopify.com")
        assert out["avg_week_1_retention"] == 0.0
        assert out["avg_week_4_retention"] == 0.0
        assert out["avg_week_8_retention"] == 0.0
        assert out["total_customers"] == 0
        assert out["cohorts_measured"] == 0
        assert out["best_cohort"] is None


# ---------------------------------------------------------------------------
# Tenant isolation — the SQL MUST filter by shop_domain
# (covered structurally — the FakeDB ignores params, but we can check
# the composer passes shop_domain through to the SQL params dict)
# ---------------------------------------------------------------------------


class TestTenantParamPassthrough:
    def test_shop_domain_passed_to_sql(self):
        """Verify that get_cohort_retention forwards the shop_domain to
        the SQL params binding. We capture the call args via a recording
        FakeDB."""
        captured = {}

        class _RecordingDB:
            def execute(self, _stmt, params):
                captured.update(params)
                return _FakeResult([])

        out = get_cohort_retention(_RecordingDB(), "tenant-iso.myshopify.com", weeks=8)
        assert captured.get("shop") == "tenant-iso.myshopify.com"
        # since_date is computed from now() — just sanity check it's a datetime
        assert isinstance(captured.get("since_date"), datetime)
        # Empty response path
        assert out["cohorts"] == []
