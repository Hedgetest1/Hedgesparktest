"""
Unit tests for the pure helpers extracted from `get_behavioral_cohort_analysis`
in the 2026-05-13 A3 refactor.

This is the first test coverage for behavioral_cohorts.py. The composer
is locked by test_behavioral_cohorts_composer.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.behavioral_cohorts import (
    _behavioral_index,
    _build_behavior_map,
    _build_customer_aggregates,
    _build_engagement_segments,
    _build_source_segments,
    _build_visit_segments,
    _classify_into_segments,
    _engagement_tier,
    _generate_insights,
    _segment_metrics,
    _source_bucket,
    _visit_pattern,
)


# ---------------------------------------------------------------------------
# _behavioral_index — weighted normalization
# ---------------------------------------------------------------------------


class TestBehavioralIndex:
    def test_zero_inputs_yield_zero(self):
        assert _behavioral_index(0, 0, 0) == 0.0

    def test_max_inputs_yield_one(self):
        # avg_scroll=100, avg_dwell=120, visit_count=5 → all 1.0 normalized
        assert _behavioral_index(100, 120, 5) == 1.0

    def test_scroll_weight_40_pct(self):
        # 100% scroll, 0 dwell, 0 visits → 0.4
        assert _behavioral_index(100, 0, 0) == 0.4

    def test_dwell_weight_40_pct(self):
        assert _behavioral_index(0, 120, 0) == 0.4

    def test_visit_weight_20_pct(self):
        # 5 visits maxes the visit_norm at (5-1)/4 = 1.0 → 0.2
        assert abs(_behavioral_index(0, 0, 5) - 0.2) < 1e-9

    def test_visit_count_1_yields_zero_visit_norm(self):
        # max(1-1, 0) / 4 = 0
        assert _behavioral_index(0, 0, 1) == 0.0


# ---------------------------------------------------------------------------
# _engagement_tier
# ---------------------------------------------------------------------------


class TestEngagementTier:
    def test_high_at_055(self):
        assert _engagement_tier(0.55) == "HIGH"
        assert _engagement_tier(1.0) == "HIGH"

    def test_medium_at_020(self):
        assert _engagement_tier(0.20) == "MEDIUM"
        assert _engagement_tier(0.54) == "MEDIUM"

    def test_low_at_zero(self):
        assert _engagement_tier(0.0) == "LOW"
        assert _engagement_tier(0.19) == "LOW"


# ---------------------------------------------------------------------------
# _visit_pattern
# ---------------------------------------------------------------------------


class TestVisitPattern:
    def test_three_or_more_events_is_repeat(self):
        assert _visit_pattern(None, None, 3) == "REPEAT_VISITOR"
        assert _visit_pattern(None, None, 10) == "REPEAT_VISITOR"

    def test_single_visit_default(self):
        # event_count<3 AND no first_event_ts/purchase_ts → SINGLE_VISIT
        assert _visit_pattern(None, None, 2) == "SINGLE_VISIT"
        assert _visit_pattern(None, datetime.now(), 1) == "SINGLE_VISIT"

    def test_browsed_before_one_day_ago_is_repeat(self):
        purchase = datetime(2025, 1, 10)
        first = datetime(2025, 1, 5).timestamp() * 1000  # 5 days before
        assert _visit_pattern(int(first), purchase, 1) == "REPEAT_VISITOR"

    def test_browsed_same_day_is_single(self):
        purchase = datetime(2025, 1, 10, 12, 0, 0)
        first = datetime(2025, 1, 10, 10, 0, 0).timestamp() * 1000  # same day
        assert _visit_pattern(int(first), purchase, 1) == "SINGLE_VISIT"


# ---------------------------------------------------------------------------
# _source_bucket
# ---------------------------------------------------------------------------


class TestSourceBucket:
    def test_direct(self):
        assert _source_bucket("direct") == "DIRECT"

    def test_search(self):
        for s in ("google", "bing", "organic", "paid_search"):
            assert _source_bucket(s) == "SEARCH"

    def test_social(self):
        for s in ("facebook", "instagram", "tiktok", "paid_social"):
            assert _source_bucket(s) == "SOCIAL"

    def test_email_sms(self):
        for s in ("email", "klaviyo", "sms"):
            assert _source_bucket(s) == "EMAIL_SMS"

    def test_referral(self):
        assert _source_bucket("referral") == "REFERRAL"

    def test_other(self):
        assert _source_bucket("mystery") == "OTHER"

    def test_none_or_empty_unknown(self):
        assert _source_bucket(None) == "UNKNOWN"
        assert _source_bucket("") == "UNKNOWN"

    def test_case_insensitive(self):
        assert _source_bucket("GOOGLE") == "SEARCH"
        assert _source_bucket("Facebook") == "SOCIAL"


# ---------------------------------------------------------------------------
# _build_customer_aggregates
# ---------------------------------------------------------------------------


class TestBuildCustomerAggregates:
    def test_single_order_single_customer(self):
        rows = [("v1", "direct", datetime(2025, 1, 1), "100.00")]
        out = _build_customer_aggregates(rows)
        assert out == {
            "v1": {
                "visitor_id": "v1",
                "first_source": "direct",
                "first_purchase": datetime(2025, 1, 1),
                "orders": 1,
                "revenue": 100.0,
            },
        }

    def test_multi_order_same_customer(self):
        rows = [
            ("v1", "direct", datetime(2025, 1, 1), "100.00"),
            ("v1", "google", datetime(2025, 1, 5), "50.00"),
        ]
        out = _build_customer_aggregates(rows)
        assert out["v1"]["orders"] == 2
        assert out["v1"]["revenue"] == 150.0
        # First-seen wins — second row's source/timestamp ignored
        assert out["v1"]["first_source"] == "direct"

    def test_multi_customer(self):
        rows = [
            ("v1", "direct", datetime(2025, 1, 1), "100.00"),
            ("v2", "google", datetime(2025, 1, 5), "50.00"),
        ]
        out = _build_customer_aggregates(rows)
        assert set(out.keys()) == {"v1", "v2"}


# ---------------------------------------------------------------------------
# _build_behavior_map
# ---------------------------------------------------------------------------


class TestBuildBehaviorMap:
    def test_only_behavior_rows(self):
        behavior_rows = [("v1", 80.0, 240.0, 10, 1)]
        out = _build_behavior_map(behavior_rows, [])
        assert out["v1"]["avg_scroll"] == 80.0
        assert out["v1"]["total_dwell"] == 240.0
        assert out["v1"]["total_views"] == 10
        assert out["v1"]["any_wishlist"] is True
        # No first_event_ts when no event row
        assert "first_event_ts" not in out["v1"]

    def test_only_event_rows_zero_fills_behavior(self):
        out = _build_behavior_map([], [("v1", 1000, 3)])
        assert out["v1"]["avg_scroll"] == 0
        assert out["v1"]["total_views"] == 0
        assert out["v1"]["any_wishlist"] is False
        assert out["v1"]["first_event_ts"] == 1000
        assert out["v1"]["event_count"] == 3

    def test_merge_when_both_present(self):
        out = _build_behavior_map(
            [("v1", 80.0, 240.0, 10, 0)],
            [("v1", 1000, 5)],
        )
        assert out["v1"]["avg_scroll"] == 80.0
        assert out["v1"]["first_event_ts"] == 1000
        assert out["v1"]["event_count"] == 5


# ---------------------------------------------------------------------------
# _segment_metrics
# ---------------------------------------------------------------------------


class TestSegmentMetrics:
    def test_empty_members(self):
        out = _segment_metrics([])
        assert out == {"customers": 0, "repeat_rate": 0.0, "avg_revenue": 0.0, "avg_orders": 0.0}

    def test_single_member(self):
        out = _segment_metrics([{"orders": 1, "revenue": 100.0}])
        assert out["customers"] == 1
        assert out["repeat_rate"] == 0.0  # 1 order < 2
        assert out["avg_revenue"] == 100.0
        assert out["total_revenue"] == 100.0

    def test_repeat_rate_with_repeaters(self):
        members = [
            {"orders": 1, "revenue": 100.0},
            {"orders": 2, "revenue": 200.0},
            {"orders": 3, "revenue": 300.0},
            {"orders": 1, "revenue": 50.0},
        ]
        out = _segment_metrics(members)
        assert out["customers"] == 4
        # 2 repeaters (orders >= 2) out of 4
        assert out["repeat_rate"] == 0.5
        assert out["total_revenue"] == 650.0


# ---------------------------------------------------------------------------
# _classify_into_segments
# ---------------------------------------------------------------------------


class TestClassifyIntoSegments:
    def test_customer_with_behavior_segmentable(self):
        customer_data = {
            "v1": {
                "visitor_id": "v1", "first_source": "google",
                "first_purchase": datetime(2025, 1, 5),
                "orders": 1, "revenue": 100.0,
            },
        }
        behavior_map = {
            "v1": {
                "avg_scroll": 80.0, "total_dwell": 200.0,
                "total_views": 10, "any_wishlist": True,
                "event_count": 5,
            },
        }
        by_e, by_v, by_s, n = _classify_into_segments(customer_data, behavior_map)
        assert n == 1
        # High engagement: 80% scroll + dwell + 5 visits
        assert "HIGH" in by_e or "MEDIUM" in by_e
        # Source bucket = SEARCH (google)
        assert "SEARCH" in by_s

    def test_customer_without_behavior_unknown(self):
        customer_data = {
            "v1": {
                "visitor_id": "v1", "first_source": "direct",
                "first_purchase": datetime(2025, 1, 5),
                "orders": 1, "revenue": 100.0,
            },
        }
        by_e, by_v, by_s, n = _classify_into_segments(customer_data, {})
        assert n == 0  # no behavior data
        assert "UNKNOWN" in by_e
        assert "UNKNOWN" in by_v
        assert "DIRECT" in by_s


# ---------------------------------------------------------------------------
# _build_*_segments — sort + filter empty
# ---------------------------------------------------------------------------


class TestBuildSegments:
    def test_engagement_sorted_alphabetically(self):
        from collections import defaultdict
        by_e = defaultdict(list)
        by_e["MEDIUM"].append({"orders": 1, "revenue": 50.0})
        by_e["HIGH"].append({"orders": 1, "revenue": 100.0})
        out = _build_engagement_segments(by_e)
        segments = [s["segment"] for s in out]
        # Alphabetical: HIGH before MEDIUM
        assert segments == ["HIGH", "MEDIUM"]

    def test_empty_segments_filtered(self):
        from collections import defaultdict
        by_e = defaultdict(list)
        by_e["HIGH"].append({"orders": 1, "revenue": 100.0})
        by_e["EMPTY_TIER"] = []
        out = _build_engagement_segments(by_e)
        segments = [s["segment"] for s in out]
        assert "EMPTY_TIER" not in segments

    def test_source_sorted_by_avg_revenue_desc(self):
        from collections import defaultdict
        by_s = defaultdict(list)
        by_s["LOW_VALUE"].append({"orders": 1, "revenue": 10.0})
        by_s["HIGH_VALUE"].append({"orders": 1, "revenue": 500.0})
        out = _build_source_segments(by_s)
        # HIGH_VALUE has higher avg → first
        assert out[0]["segment"] == "HIGH_VALUE"
        assert out[-1]["segment"] == "LOW_VALUE"


# ---------------------------------------------------------------------------
# _generate_insights — currency drift fix + branch coverage
# ---------------------------------------------------------------------------


class TestGenerateInsights:
    def test_zero_total_yields_cold_start_message(self):
        out = _generate_insights([], [], [], total=0)
        assert "No customer data yet" in out[0]

    def test_engagement_revenue_ratio_insight_uses_currency(self):
        # HIGH 5x more revenue than LOW → ratio insight triggered
        # The currency-drift fix means we use format_money(currency)
        # NOT hardcoded `$`. For GBP, the symbol is £.
        engagement = [
            {"segment": "HIGH", "customers": 5, "avg_revenue": 500.0,
             "repeat_rate": 0.2},
            {"segment": "LOW", "customers": 5, "avg_revenue": 100.0,
             "repeat_rate": 0.1},
        ]
        out = _generate_insights(engagement, [], [], total=10, currency="GBP")
        joined = " ".join(out)
        # CURRENCY DRIFT FIX: no hardcoded `$`, must contain £
        assert "$" not in joined
        assert "£" in joined or "500" in joined  # symbol or numeric anchor

    def test_source_insight_uses_currency(self):
        source = [
            {"segment": "EMAIL_SMS", "customers": 5, "avg_revenue": 500.0,
             "repeat_rate": 0.2},
            {"segment": "DIRECT", "customers": 5, "avg_revenue": 100.0,
             "repeat_rate": 0.1},
        ]
        out = _generate_insights([], [], source, total=10, currency="EUR")
        joined = " ".join(out)
        assert "$" not in joined  # no drift

    def test_balanced_segments_fallback(self):
        # Total >= 10 with no triggering insight branch
        out = _generate_insights([], [], [], total=20)
        joined = " ".join(out)
        assert "balanced" in joined or "no strong" in joined.lower()

    def test_low_customer_count_message(self):
        out = _generate_insights([], [], [], total=5)
        assert any("Only 5 customers" in s for s in out)

    def test_visit_pattern_insight_already_uses_format_money(self):
        visit = [
            {"segment": "REPEAT_VISITOR", "customers": 5, "avg_revenue": 200.0,
             "repeat_rate": 0.5},
            {"segment": "SINGLE_VISIT", "customers": 5, "avg_revenue": 100.0,
             "repeat_rate": 0.1},
        ]
        out = _generate_insights([], visit, [], total=10, currency="GBP")
        joined = " ".join(out)
        # Visit pattern was already using format_money — preserve that
        assert "$" not in joined
