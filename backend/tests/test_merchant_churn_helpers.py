"""
Unit tests for the pure helpers extracted from `compute_churn_score`
in the 2026-05-13 A3 refactor.

This is the first test coverage for merchant_churn_predictor.py.
The composer is locked by test_merchant_churn_composer.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.merchant_churn_predictor import (
    _classify_revenue_change,
    _classify_risk_level,
    _score_digest,
    _score_onboarding,
    _score_revenue,
    _score_tenure_billing,
    _score_tracker,
)


# ---------------------------------------------------------------------------
# FakeDB helpers
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, *values): self._values = values
    def __getitem__(self, i): return self._values[i]


class _FakeDB:
    def __init__(self, row): self._row = row
    def execute(self, *_a, **_kw):
        class _R:
            def __init__(s, row): s._row = row
            def fetchone(s): return s._row
        return _R(self._row)


class _ExplodingDB:
    def execute(self, *_a, **_kw):
        raise RuntimeError("db down")


# ---------------------------------------------------------------------------
# _classify_revenue_change — sentinel-value edge cases
# ---------------------------------------------------------------------------


class TestClassifyRevenueChange:
    def test_normal_growth(self):
        # +50% growth
        assert _classify_revenue_change(150.0, 100.0) == 50.0

    def test_normal_decline(self):
        assert _classify_revenue_change(50.0, 100.0) == -50.0

    def test_no_prior_with_recent_yields_100(self):
        # New revenue from 0 → +100 (clean growth, not div-by-0)
        assert _classify_revenue_change(50.0, 0.0) == 100

    def test_both_zero_yields_minus_100(self):
        # No revenue at all = high risk anchor
        assert _classify_revenue_change(0.0, 0.0) == -100


# ---------------------------------------------------------------------------
# _score_revenue — 30-pt scorer
# ---------------------------------------------------------------------------


class TestScoreRevenue:
    def test_collapse_yields_30(self):
        # rev_recent=10, rev_prior=100 → -90% → collapse band
        db = _FakeDB(_FakeRow(10.0, 100.0, 5, 50))
        components, signals = _score_revenue(db, "x.myshopify.com", datetime.now())
        assert components == {"revenue": 30}
        assert signals[0]["signal"] == "revenue_collapse"
        assert signals[0]["weight"] == 30

    def test_declining_yields_20(self):
        # -30% drop
        db = _FakeDB(_FakeRow(70.0, 100.0, 5, 5))
        components, signals = _score_revenue(db, "x.myshopify.com", datetime.now())
        assert components == {"revenue": 20}
        assert signals[0]["signal"] == "revenue_declining"

    def test_flat_yields_10(self):
        # 0% change
        db = _FakeDB(_FakeRow(100.0, 100.0, 5, 5))
        components, signals = _score_revenue(db, "x.myshopify.com", datetime.now())
        assert components == {"revenue": 10}
        assert signals[0]["signal"] == "revenue_flat"

    def test_growth_yields_zero_with_no_signal(self):
        db = _FakeDB(_FakeRow(150.0, 100.0, 10, 5))
        components, signals = _score_revenue(db, "x.myshopify.com", datetime.now())
        assert components == {"revenue": 0}
        assert signals == []

    def test_zero_orders_overrides_to_25(self):
        # Even with revenue collapse → if BOTH order counts are 0,
        # the override kicks in with weight 25 (and signal stacks)
        db = _FakeDB(_FakeRow(0.0, 0.0, 0, 0))
        components, signals = _score_revenue(db, "x.myshopify.com", datetime.now())
        assert components == {"revenue": 25}
        signal_names = [s["signal"] for s in signals]
        # Both collapse + zero_orders signals present
        assert "zero_orders" in signal_names

    def test_query_failure_yields_unknown_15(self):
        components, signals = _score_revenue(_ExplodingDB(), "x.myshopify.com", datetime.now())
        assert components == {"revenue": 15}
        assert signals == []


# ---------------------------------------------------------------------------
# _score_tracker — 25-pt scorer
# ---------------------------------------------------------------------------


class TestScoreTracker:
    def test_dead_tracker_yields_25(self):
        now = datetime.now()
        last_event = now - timedelta(days=20)
        db = _FakeDB(_FakeRow(0, 0, last_event))
        components, signals = _score_tracker(db, "x.myshopify.com", now)
        assert components == {"tracker": 25}
        assert signals[0]["signal"] == "tracker_dead"

    def test_declining_yields_15(self):
        now = datetime.now()
        last_event = now - timedelta(days=10)
        db = _FakeDB(_FakeRow(50, 5, last_event))
        components, signals = _score_tracker(db, "x.myshopify.com", now)
        assert components == {"tracker": 15}
        assert signals[0]["signal"] == "tracker_declining"

    def test_low_traffic_yields_10(self):
        now = datetime.now()
        last_event = now - timedelta(hours=1)
        db = _FakeDB(_FakeRow(50, 5, last_event))
        components, signals = _score_tracker(db, "x.myshopify.com", now)
        assert components == {"tracker": 10}
        assert signals[0]["signal"] == "low_traffic"

    def test_healthy_traffic_yields_zero(self):
        now = datetime.now()
        last_event = now - timedelta(hours=1)
        db = _FakeDB(_FakeRow(500, 100, last_event))
        components, signals = _score_tracker(db, "x.myshopify.com", now)
        assert components == {"tracker": 0}
        assert signals == []

    def test_no_events_at_all_yields_dead(self):
        # last_event=None → days_silent=999 → dead
        db = _FakeDB(_FakeRow(0, 0, None))
        components, _ = _score_tracker(db, "x.myshopify.com", datetime.now())
        assert components == {"tracker": 25}

    def test_query_failure_yields_unknown_12(self):
        components, signals = _score_tracker(_ExplodingDB(), "x.myshopify.com", datetime.now())
        assert components == {"tracker": 12}
        assert signals == []


# ---------------------------------------------------------------------------
# _score_digest — 20-pt scorer
# ---------------------------------------------------------------------------


class TestScoreDigest:
    def test_ignored_yields_20(self):
        # 10 sent, 0 opens → 0% open rate
        db = _FakeDB(_FakeRow(10, 0, 0))
        components, signals = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 20}
        assert signals[0]["signal"] == "digest_ignored"

    def test_low_engagement_yields_10(self):
        # 10 sent, 2 opens → 20% open rate → between 10% and 30%
        db = _FakeDB(_FakeRow(10, 2, 0))
        components, signals = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 10}
        assert signals[0]["signal"] == "digest_low_engagement"

    def test_opens_no_clicks_yields_5(self):
        # 10 sent, 5 opens (>30%), 0 clicks → 0% click rate
        db = _FakeDB(_FakeRow(10, 5, 0))
        components, signals = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 5}
        assert signals[0]["signal"] == "digest_opens_no_clicks"

    def test_healthy_yields_zero(self):
        # 10 sent, 5 opens, 2 clicks → 20% click rate
        db = _FakeDB(_FakeRow(10, 5, 2))
        components, signals = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 0}
        assert signals == []

    def test_too_few_sends_yields_5(self):
        # Only 2 sends → can't judge
        db = _FakeDB(_FakeRow(2, 0, 0))
        components, signals = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 5}
        assert signals == []

    def test_no_row_yields_10(self):
        db = _FakeDB(None)
        components, _ = _score_digest(db, "x.myshopify.com")
        assert components == {"digest": 10}

    def test_query_failure_yields_unknown_10(self):
        components, signals = _score_digest(_ExplodingDB(), "x.myshopify.com")
        assert components == {"digest": 10}
        assert signals == []


# ---------------------------------------------------------------------------
# _score_tenure_billing — 15 + 10 pt scorer
# ---------------------------------------------------------------------------


class TestScoreTenureBilling:
    def test_new_install_yields_tenure_15(self):
        now = datetime.now()
        installed_at = now - timedelta(days=7)
        db = _FakeDB(_FakeRow(installed_at, True, "lite"))
        components, signals = _score_tenure_billing(db, "x.myshopify.com", now)
        assert components["tenure"] == 15
        assert components["billing"] == 0
        signal_names = [s["signal"] for s in signals]
        assert "new_install" in signal_names

    def test_early_tenure_yields_8(self):
        now = datetime.now()
        installed_at = now - timedelta(days=20)
        db = _FakeDB(_FakeRow(installed_at, True, "lite"))
        components, signals = _score_tenure_billing(db, "x.myshopify.com", now)
        assert components["tenure"] == 8
        signal_names = [s["signal"] for s in signals]
        assert "early_tenure" in signal_names

    def test_mature_tenure_yields_zero(self):
        now = datetime.now()
        installed_at = now - timedelta(days=90)
        db = _FakeDB(_FakeRow(installed_at, True, "lite"))
        components, _ = _score_tenure_billing(db, "x.myshopify.com", now)
        assert components["tenure"] == 0

    def test_billing_inactive_yields_10(self):
        now = datetime.now()
        installed_at = now - timedelta(days=90)
        db = _FakeDB(_FakeRow(installed_at, False, "lite"))
        components, signals = _score_tenure_billing(db, "x.myshopify.com", now)
        assert components["billing"] == 10
        signal_names = [s["signal"] for s in signals]
        assert "billing_inactive" in signal_names

    def test_no_merchant_row_yields_55_default(self):
        db = _FakeDB(None)
        components, _ = _score_tenure_billing(db, "x.myshopify.com", datetime.now())
        assert components == {"tenure": 5, "billing": 5}

    def test_query_failure_yields_50_unknown(self):
        components, _ = _score_tenure_billing(_ExplodingDB(), "x.myshopify.com", datetime.now())
        assert components == {"tenure": 5, "billing": 0}


# ---------------------------------------------------------------------------
# _score_onboarding — 10 pt scorer
# ---------------------------------------------------------------------------


class TestScoreOnboarding:
    def test_incomplete_stage_yields_10(self):
        db = _FakeDB(_FakeRow("setup_pending"))
        components, signals = _score_onboarding(db, "x.myshopify.com")
        assert components == {"onboarding": 10}
        assert signals[0]["signal"] == "onboarding_incomplete"

    def test_active_stage_yields_zero(self):
        db = _FakeDB(_FakeRow("active"))
        components, signals = _score_onboarding(db, "x.myshopify.com")
        assert components == {"onboarding": 0}
        assert signals == []

    def test_activated_lite_yields_zero(self):
        db = _FakeDB(_FakeRow("activated_lite"))
        components, _ = _score_onboarding(db, "x.myshopify.com")
        assert components == {"onboarding": 0}

    def test_no_journey_yields_5(self):
        db = _FakeDB(None)
        components, _ = _score_onboarding(db, "x.myshopify.com")
        assert components == {"onboarding": 5}

    def test_query_failure_yields_5(self):
        components, _ = _score_onboarding(_ExplodingDB(), "x.myshopify.com")
        assert components == {"onboarding": 5}


# ---------------------------------------------------------------------------
# _classify_risk_level — score → (level, action)
# ---------------------------------------------------------------------------


class TestClassifyRiskLevel:
    def test_critical_at_70(self):
        level, action = _classify_risk_level(70)
        assert level == "critical"
        assert "Immediate outreach" in action

    def test_critical_at_100(self):
        level, _ = _classify_risk_level(100)
        assert level == "critical"

    def test_high_at_50(self):
        level, action = _classify_risk_level(50)
        assert level == "high"
        assert "re-engagement" in action

    def test_high_at_69(self):
        level, _ = _classify_risk_level(69)
        assert level == "high"

    def test_moderate_at_30(self):
        level, action = _classify_risk_level(30)
        assert level == "moderate"
        assert "Monitor" in action

    def test_moderate_at_49(self):
        level, _ = _classify_risk_level(49)
        assert level == "moderate"

    def test_low_at_29(self):
        level, action = _classify_risk_level(29)
        assert level == "low"
        assert "healthy" in action

    def test_low_at_0(self):
        level, _ = _classify_risk_level(0)
        assert level == "low"
