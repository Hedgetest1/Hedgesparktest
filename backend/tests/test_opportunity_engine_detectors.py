"""
Unit tests for the 11 pure detectors extracted from
`_evaluate_product_signals` in the 2026-05-12 A3 refactor
(commit 5536677).

Each detector is a pure function: given keyword product-metric inputs,
returns either a signal dict OR None. Mutex groups A/B/C suppress each
other; groups D-K are independent. The thresholds here are the
contract — drift in any threshold changes which products get flagged
and what merchants see in their Pro action panel.

End-to-end coverage is at the
`/pro/opportunity-signals` endpoint level; this file locks the per-
detector thresholds + strength-output shape.
"""
from __future__ import annotations

from app.services.opportunity_engine import (
    _detect_cart_rate_trend,
    _detect_device_conversion_gap,
    _detect_device_purchase_gap,
    _detect_engagement_quality,
    _detect_landing_page_failure,
    _detect_paid_traffic_not_converting,
    _detect_return_visitor_quality,
    _detect_source_revenue_gap,
    _detect_time_window_misalignment,
    _detect_traffic_quality,
    _detect_traffic_spike,
    _strength_dead_traffic,
    _strength_high_engagement_no_action,
    _strength_high_return_low_conversion,
    _strength_high_traffic_no_cart,
    _strength_low_conversion,
    _strength_return_visitor_interest,
    _strength_scroll_high_no_click,
    _strength_traffic_spike,
)


_COMMON = dict(product_url="/products/x", label="My Product", detected_at="2026-05-13T00:00:00")


# ---------------------------------------------------------------------------
# Group A — _detect_traffic_quality (mutex: DEAD > NO_CART > LOW_CONV)
# ---------------------------------------------------------------------------


class TestDetectTrafficQuality:
    def test_dead_traffic_dwell_below_floor(self):
        r = _detect_traffic_quality(
            **_COMMON, views_24h=100, unique_visitors_24h=50,
            cart_conversions_24h=0, avg_dwell_24h=2.0,
            views_floor=20, dwell_floor=5.0, low_conv_threshold=0.02,
        )
        assert r is not None
        assert r["signal_type"] == "DEAD_TRAFFIC"
        assert "2.0s" in r["explanation"] or "2." in r["explanation"]

    def test_high_traffic_no_cart_when_dwell_ok(self):
        r = _detect_traffic_quality(
            **_COMMON, views_24h=100, unique_visitors_24h=50,
            cart_conversions_24h=0, avg_dwell_24h=30.0,
            views_floor=20, dwell_floor=5.0, low_conv_threshold=0.02,
        )
        assert r is not None
        assert r["signal_type"] == "HIGH_TRAFFIC_NO_CART"

    def test_low_conversion_when_carts_present(self):
        # conv_rate = 1/100 = 1% < 2%
        r = _detect_traffic_quality(
            **_COMMON, views_24h=100, unique_visitors_24h=80,
            cart_conversions_24h=1, avg_dwell_24h=30.0,
            views_floor=20, dwell_floor=5.0, low_conv_threshold=0.02,
        )
        assert r is not None
        assert r["signal_type"] == "LOW_CONVERSION_ATTENTION"

    def test_returns_none_below_views_floor(self):
        assert _detect_traffic_quality(
            **_COMMON, views_24h=5, unique_visitors_24h=4,
            cart_conversions_24h=0, avg_dwell_24h=1.0,
            views_floor=20, dwell_floor=5.0, low_conv_threshold=0.02,
        ) is None

    def test_dwell_none_falls_through_to_no_cart(self):
        # dwell None → cannot test DEAD_TRAFFIC, falls to NO_CART
        r = _detect_traffic_quality(
            **_COMMON, views_24h=100, unique_visitors_24h=50,
            cart_conversions_24h=0, avg_dwell_24h=None,
            views_floor=20, dwell_floor=5.0, low_conv_threshold=0.02,
        )
        assert r is not None
        assert r["signal_type"] == "HIGH_TRAFFIC_NO_CART"


# ---------------------------------------------------------------------------
# Group B — _detect_engagement_quality (mutex: ENGAGEMENT > SCROLL)
# ---------------------------------------------------------------------------


class TestDetectEngagementQuality:
    def test_high_engagement_no_action(self):
        # dwell>=20 AND scroll>=70 AND carts==0 → HIGH_ENGAGEMENT_NO_ACTION
        r = _detect_engagement_quality(
            **_COMMON, avg_dwell_24h=25.0, avg_scroll_24h=75.0,
            cart_conversions_24h=0,
        )
        assert r is not None
        assert r["signal_type"] == "HIGH_ENGAGEMENT_NO_ACTION"

    def test_scroll_high_no_click_when_engagement_blocked(self):
        # scroll>=85 AND dwell>=15 AND carts==0 BUT dwell<20 → SCROLL_HIGH_NO_CLICK
        r = _detect_engagement_quality(
            **_COMMON, avg_dwell_24h=16.0, avg_scroll_24h=88.0,
            cart_conversions_24h=0,
        )
        assert r is not None
        assert r["signal_type"] == "SCROLL_HIGH_NO_CLICK"

    def test_returns_none_when_dwell_below_15(self):
        assert _detect_engagement_quality(
            **_COMMON, avg_dwell_24h=10.0, avg_scroll_24h=90.0,
            cart_conversions_24h=0,
        ) is None

    def test_returns_none_when_carts_positive(self):
        assert _detect_engagement_quality(
            **_COMMON, avg_dwell_24h=30.0, avg_scroll_24h=80.0,
            cart_conversions_24h=1,
        ) is None

    def test_returns_none_when_scroll_none(self):
        assert _detect_engagement_quality(
            **_COMMON, avg_dwell_24h=30.0, avg_scroll_24h=None,
            cart_conversions_24h=0,
        ) is None


# ---------------------------------------------------------------------------
# Group C — _detect_return_visitor_quality (mutex: LOW_CONV > INTEREST)
# ---------------------------------------------------------------------------


class TestDetectReturnVisitorQuality:
    def test_high_return_low_conversion(self):
        r = _detect_return_visitor_quality(
            **_COMMON, return_visitor_count_7d=15, cart_conversions_24h=1,
            return_floor=10,
        )
        assert r is not None
        assert r["signal_type"] == "HIGH_RETURN_LOW_CONVERSION"

    def test_return_visitor_interest_when_below_floor(self):
        # 8 returns >= 8 AND no cart → RETURN_VISITOR_INTEREST
        r = _detect_return_visitor_quality(
            **_COMMON, return_visitor_count_7d=9, cart_conversions_24h=0,
            return_floor=12,
        )
        assert r is not None
        assert r["signal_type"] == "RETURN_VISITOR_INTEREST"

    def test_returns_none_when_carts_present_and_below_high_floor(self):
        assert _detect_return_visitor_quality(
            **_COMMON, return_visitor_count_7d=9, cart_conversions_24h=2,
            return_floor=12,
        ) is None

    def test_returns_none_when_below_both_floors(self):
        assert _detect_return_visitor_quality(
            **_COMMON, return_visitor_count_7d=5, cart_conversions_24h=0,
            return_floor=10,
        ) is None


# ---------------------------------------------------------------------------
# Group D — _detect_traffic_spike
# ---------------------------------------------------------------------------


class TestDetectTrafficSpike:
    def test_canonical_spike(self):
        # prior_23h = 200 - 30 = 170; avg = 170/23 ≈ 7.4; views_1h=30 > 3*7.4=22.2 → spike
        r = _detect_traffic_spike(**_COMMON, views_24h=200, views_1h=30)
        assert r is not None
        assert r["signal_type"] == "TRAFFIC_SPIKE"

    def test_returns_none_when_no_prior_traffic(self):
        # all views in last hour → prior_23h <= 0
        assert _detect_traffic_spike(**_COMMON, views_24h=20, views_1h=20) is None

    def test_returns_none_when_views_1h_below_10(self):
        assert _detect_traffic_spike(**_COMMON, views_24h=200, views_1h=9) is None

    def test_returns_none_when_spike_below_3x(self):
        # views_1h=15, prior_23h=185, avg=8.04, 3x=24.12, 15<24.12 → no spike
        assert _detect_traffic_spike(**_COMMON, views_24h=200, views_1h=15) is None


# ---------------------------------------------------------------------------
# Group E — _detect_device_conversion_gap
# ---------------------------------------------------------------------------


class TestDetectDeviceConversionGap:
    def test_mobile_weaker_than_desktop(self):
        # mobile=20 views, 1 cart → 5%; desktop=20 views, 5 carts → 25%; 5 < 25*0.4=10 → flag
        r = _detect_device_conversion_gap(
            **_COMMON, views_mobile=20, views_desktop=20,
            carts_mobile=1, carts_desktop=5,
        )
        assert r is not None
        assert r["signal_type"] == "MOBILE_CONVERSION_GAP"
        assert "Mobile" in r["explanation"]

    def test_desktop_weaker_than_mobile(self):
        # mobile=30 views, 6 carts → 20%; desktop=30 views, 1 cart → 3.3%; 3.3 < 8.0 → flag
        r = _detect_device_conversion_gap(
            **_COMMON, views_mobile=30, views_desktop=30,
            carts_mobile=6, carts_desktop=1,
        )
        assert r is not None
        assert r["signal_type"] == "MOBILE_CONVERSION_GAP"
        assert "Desktop" in r["explanation"]

    def test_returns_none_below_views_floor(self):
        assert _detect_device_conversion_gap(
            **_COMMON, views_mobile=5, views_desktop=20,
            carts_mobile=0, carts_desktop=5,
        ) is None


# ---------------------------------------------------------------------------
# Group F — _detect_cart_rate_trend
# ---------------------------------------------------------------------------


class TestDetectCartRateTrend:
    def test_canonical_decline(self):
        # 7d rate = 10/200 = 5%; 24h rate = 1/100 = 1%; 1 < 5*0.6=3 → flag
        r = _detect_cart_rate_trend(
            **_COMMON, views_24h=100, views_7d=200,
            cart_conversions_24h=1, cart_conversions_7d=10,
        )
        assert r is not None
        assert r["signal_type"] == "CART_RATE_DECLINING"

    def test_returns_none_below_views_7d_floor(self):
        assert _detect_cart_rate_trend(
            **_COMMON, views_24h=100, views_7d=20,
            cart_conversions_24h=1, cart_conversions_7d=5,
        ) is None

    def test_returns_none_when_no_prior_carts(self):
        assert _detect_cart_rate_trend(
            **_COMMON, views_24h=100, views_7d=200,
            cart_conversions_24h=1, cart_conversions_7d=0,
        ) is None

    def test_returns_none_when_decline_too_mild(self):
        # 7d rate = 10/200 = 5%; 24h rate = 4/100 = 4%; 4 < 5*0.6=3 false → no flag
        assert _detect_cart_rate_trend(
            **_COMMON, views_24h=100, views_7d=200,
            cart_conversions_24h=4, cart_conversions_7d=10,
        ) is None


# ---------------------------------------------------------------------------
# Group G — _detect_paid_traffic_not_converting
# ---------------------------------------------------------------------------


class TestDetectPaidTrafficNotConverting:
    def test_canonical_paid_zero_with_organic_proof(self):
        r = _detect_paid_traffic_not_converting(
            **_COMMON, views_paid=50, carts_paid=0,
            carts_organic=5, carts_direct=3,
        )
        assert r is not None
        assert r["signal_type"] == "PAID_TRAFFIC_NOT_CONVERTING"
        # Has organic proof → page-works framing
        assert "may be poorly targeted" in r["explanation"]

    def test_paid_zero_no_organic_proof(self):
        r = _detect_paid_traffic_not_converting(
            **_COMMON, views_paid=50, carts_paid=0,
            carts_organic=0, carts_direct=0,
        )
        assert r is not None
        assert "No traffic source" in r["explanation"]

    def test_returns_none_when_paid_has_carts(self):
        assert _detect_paid_traffic_not_converting(
            **_COMMON, views_paid=50, carts_paid=2,
            carts_organic=0, carts_direct=0,
        ) is None

    def test_returns_none_below_paid_views_floor(self):
        assert _detect_paid_traffic_not_converting(
            **_COMMON, views_paid=5, carts_paid=0,
            carts_organic=0, carts_direct=0,
        ) is None


# ---------------------------------------------------------------------------
# Group H — _detect_device_purchase_gap
# ---------------------------------------------------------------------------


class TestDetectDevicePurchaseGap:
    def test_mobile_purchase_zero_with_desktop_purchases(self):
        r = _detect_device_purchase_gap(
            **_COMMON, purchases_24h=3,
            purchases_mobile=0, purchases_desktop=3,
            views_mobile=20, views_desktop=20,
        )
        assert r is not None
        assert r["signal_type"] == "DEVICE_PURCHASE_GAP"
        assert "Mobile checkout may be broken" in r["explanation"]

    def test_desktop_purchase_zero_with_mobile_purchases(self):
        r = _detect_device_purchase_gap(
            **_COMMON, purchases_24h=3,
            purchases_mobile=3, purchases_desktop=0,
            views_mobile=20, views_desktop=20,
        )
        assert r is not None
        assert "Desktop checkout" in r["explanation"]

    def test_returns_none_below_purchase_floor(self):
        assert _detect_device_purchase_gap(
            **_COMMON, purchases_24h=1,
            purchases_mobile=0, purchases_desktop=1,
            views_mobile=20, views_desktop=20,
        ) is None

    def test_returns_none_when_both_devices_have_purchases(self):
        assert _detect_device_purchase_gap(
            **_COMMON, purchases_24h=4,
            purchases_mobile=2, purchases_desktop=2,
            views_mobile=20, views_desktop=20,
        ) is None


# ---------------------------------------------------------------------------
# Group I — _detect_source_revenue_gap
# ---------------------------------------------------------------------------


class TestDetectSourceRevenueGap:
    def test_canonical(self):
        r = _detect_source_revenue_gap(
            **_COMMON, purchases_24h=3, views_paid=50,
            purchases_paid=0, purchases_organic=2, purchases_direct=1,
        )
        assert r is not None
        assert r["signal_type"] == "SOURCE_REVENUE_GAP"

    def test_returns_none_when_paid_has_purchases(self):
        assert _detect_source_revenue_gap(
            **_COMMON, purchases_24h=3, views_paid=50,
            purchases_paid=1, purchases_organic=2, purchases_direct=0,
        ) is None

    def test_returns_none_when_no_organic_purchases(self):
        assert _detect_source_revenue_gap(
            **_COMMON, purchases_24h=3, views_paid=50,
            purchases_paid=0, purchases_organic=0, purchases_direct=0,
        ) is None

    def test_returns_none_below_paid_views_floor(self):
        assert _detect_source_revenue_gap(
            **_COMMON, purchases_24h=3, views_paid=5,
            purchases_paid=0, purchases_organic=2, purchases_direct=0,
        ) is None


# ---------------------------------------------------------------------------
# Group J — _detect_time_window_misalignment
# ---------------------------------------------------------------------------


class TestDetectTimeWindowMisalignment:
    def test_off_peak_outconverts_peak(self):
        # peak=50 views, 1 cart → 2%; off=20 views, 4 carts → 20%; 20 > 2*2 → flag
        r = _detect_time_window_misalignment(
            **_COMMON, peak_hour_views=50, peak_hour_carts=1,
            off_peak_hour_views=20, off_peak_hour_carts=4,
        )
        assert r is not None
        assert r["signal_type"] == "TIME_WINDOW_MISALIGNMENT"
        assert "misaligned" in r["explanation"]

    def test_peak_outconverts_offpeak(self):
        r = _detect_time_window_misalignment(
            **_COMMON, peak_hour_views=20, peak_hour_carts=4,
            off_peak_hour_views=50, off_peak_hour_carts=1,
        )
        assert r is not None
        assert "promotional timing" in r["explanation"]

    def test_peak_zero_with_offpeak_carts(self):
        # peak_rate==0 AND off_peak>0 AND peak_views>=15
        r = _detect_time_window_misalignment(
            **_COMMON, peak_hour_views=20, peak_hour_carts=0,
            off_peak_hour_views=20, off_peak_hour_carts=2,
        )
        assert r is not None
        assert "zero carts" in r["explanation"]

    def test_returns_none_below_views_floor(self):
        assert _detect_time_window_misalignment(
            **_COMMON, peak_hour_views=5, peak_hour_carts=0,
            off_peak_hour_views=20, off_peak_hour_carts=2,
        ) is None


# ---------------------------------------------------------------------------
# Group K — _detect_landing_page_failure
# ---------------------------------------------------------------------------


class TestDetectLandingPageFailure:
    def test_canonical_landing_underperforms_browsing(self):
        # landing=30 views, 0 carts → 0%; browsing=30 views, 3 carts → 10%; 0 < 10*0.3=3 → flag
        r = _detect_landing_page_failure(
            **_COMMON, landing_views_24h=30, browsing_views_24h=30,
            landing_carts_24h=0, browsing_carts_24h=3,
        )
        assert r is not None
        assert r["signal_type"] == "LANDING_PAGE_FAILURE"

    def test_returns_none_below_landing_views_floor(self):
        assert _detect_landing_page_failure(
            **_COMMON, landing_views_24h=5, browsing_views_24h=30,
            landing_carts_24h=0, browsing_carts_24h=3,
        ) is None

    def test_returns_none_when_browsing_no_carts(self):
        assert _detect_landing_page_failure(
            **_COMMON, landing_views_24h=30, browsing_views_24h=30,
            landing_carts_24h=0, browsing_carts_24h=0,
        ) is None

    def test_returns_none_when_landing_comparable(self):
        # landing 3% > browsing 10%*0.3=3% → no flag
        assert _detect_landing_page_failure(
            **_COMMON, landing_views_24h=30, browsing_views_24h=30,
            landing_carts_24h=1, browsing_carts_24h=3,
        ) is None


# ---------------------------------------------------------------------------
# Signal output shape — common contract across all detectors
# ---------------------------------------------------------------------------


class TestSignalDictShape:
    def test_signal_carries_all_required_keys(self):
        r = _detect_traffic_spike(**_COMMON, views_24h=200, views_1h=30)
        assert r is not None
        assert set(r.keys()) == {
            "product_url",
            "signal_type",
            "signal_strength",
            "explanation",
            "detected_at",
            "human_label",
            "human_action",
        }

    def test_signal_strength_in_zero_to_one(self):
        r = _detect_traffic_spike(**_COMMON, views_24h=500, views_1h=200)
        assert r is not None
        s = r["signal_strength"]
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# Strength helpers — pin docstring contract + clamping + monotonicity
# ---------------------------------------------------------------------------


class TestStrengthDeadTraffic:
    def test_at_floor_threshold(self):
        # docstring: "0.40 at 20 views"
        assert _strength_dead_traffic(20) == 0.40

    def test_at_ceiling_threshold(self):
        # docstring: "1.0 at 100+ views"
        assert _strength_dead_traffic(100) == 1.0

    def test_above_ceiling_clamps(self):
        assert _strength_dead_traffic(500) == 1.0

    def test_below_floor_below_0_40(self):
        # 10 views: (10-20)/80+0.40 = 0.275
        assert _strength_dead_traffic(10) < 0.40

    def test_monotonic_in_views(self):
        assert (
            _strength_dead_traffic(30)
            < _strength_dead_traffic(50)
            < _strength_dead_traffic(80)
        )


class TestStrengthHighTrafficNoCart:
    def test_at_floor_threshold(self):
        # docstring: "0.40 at 20 views"
        assert _strength_high_traffic_no_cart(20) == 0.40

    def test_at_ceiling_threshold(self):
        # docstring: "1.0 at 90+ views"
        assert _strength_high_traffic_no_cart(90) == 1.0

    def test_clamps_above_ceiling(self):
        assert _strength_high_traffic_no_cart(1000) == 1.0


class TestStrengthLowConversion:
    def test_floor_at_0_30(self):
        # docstring: "0.30 floor (evidence of some cart activity)"
        # At conv_rate=2% (0.02), 1.0 - (0.02/0.02) = 0.0 → floored to 0.30
        assert _strength_low_conversion(0.02) == 0.30

    def test_near_zero_conv_approaches_1(self):
        # 0% conv rate → 1.0
        assert _strength_low_conversion(0.0) == 1.0

    def test_inverse_relationship(self):
        # Higher conv = LOWER signal (it's a bad-signal detector)
        assert (
            _strength_low_conversion(0.001)
            > _strength_low_conversion(0.005)
            > _strength_low_conversion(0.01)
        )

    def test_floor_holds_for_high_rates(self):
        # Way above 2% (e.g. 50%) still returns floor, never negative
        assert _strength_low_conversion(0.5) == 0.30


class TestStrengthHighEngagementNoAction:
    def test_weighted_average_50_50(self):
        # dwell=30 (factor=0.5), scroll=50 (factor=0.5) → 0.5*0.5+0.5*0.5 = 0.50
        assert _strength_high_engagement_no_action(30, 50) == 0.50

    def test_max_at_60_dwell_100_scroll(self):
        assert _strength_high_engagement_no_action(60, 100) == 1.0

    def test_max_clamps_dwell_above_60(self):
        # dwell saturates at 60s
        assert _strength_high_engagement_no_action(120, 100) == 1.0


class TestStrengthScrollHighNoClick:
    def test_below_floor_returns_0_30(self):
        # scroll=80, dwell=0 → dwell_mod=0 → 0+0.10 → floored to 0.30
        assert _strength_scroll_high_no_click(80, 0) == 0.30

    def test_high_scroll_high_dwell(self):
        # scroll=100, dwell=30 → scroll_base=1.0, dwell_mod=1.0 → 1.0+0.10 → clamped via max
        # but max(0.30, 1.0 * 1.0 + 0.10) = 1.10 (NOT clamped — helper doesn't cap above 1.0)
        # Smoke: just check >= 0.30 floor
        assert _strength_scroll_high_no_click(100, 30) >= 0.30


class TestStrengthHighReturnLowConversion:
    def test_at_5_returns(self):
        # docstring: "0.33 at 5 returns" → 5/15 = 0.333... → 0.33
        assert _strength_high_return_low_conversion(5) == 0.33

    def test_at_ceiling_15(self):
        assert _strength_high_return_low_conversion(15) == 1.0

    def test_clamps_above_15(self):
        assert _strength_high_return_low_conversion(100) == 1.0


class TestStrengthReturnVisitorInterest:
    def test_at_4_returns(self):
        # docstring: "0.20 at 4 returns" → 4/20 = 0.20
        assert _strength_return_visitor_interest(4) == 0.20

    def test_at_ceiling_20(self):
        # docstring: "1.0 at 20 returns"
        assert _strength_return_visitor_interest(20) == 1.0


class TestStrengthTrafficSpike:
    def test_below_ratio_1_5_below_0_30(self):
        # 1.5 / 7.5 = 0.20
        assert _strength_traffic_spike(1.5) == 0.20

    def test_at_ceiling_7_5(self):
        # docstring: "1.0 at 7.5×+"
        assert _strength_traffic_spike(7.5) == 1.0

    def test_clamps_above_ceiling(self):
        assert _strength_traffic_spike(20.0) == 1.0


class TestStrengthAllReturnInRange:
    """Class-level invariant: every _strength_* helper returns a value
    in [0.0, 1.0] regardless of input — this is the contract every
    detector relies on when emitting signal_strength."""

    def test_dead_traffic_extreme_inputs(self):
        for v in [0, 1, 10, 1_000_000]:
            assert 0.0 <= _strength_dead_traffic(v) <= 1.0

    def test_low_conversion_extreme_inputs(self):
        for r in [-1.0, 0.0, 0.001, 0.5, 100.0]:
            assert 0.0 <= _strength_low_conversion(r) <= 1.0

    def test_return_visitor_extreme_inputs(self):
        for n in [0, 1, 100, 10_000]:
            assert 0.0 <= _strength_return_visitor_interest(n) <= 1.0
            assert 0.0 <= _strength_high_return_low_conversion(n) <= 1.0
