"""
Unit tests for the pure helpers extracted from `compute_product_autopsy`
in the 2026-05-12 A3 refactor (commit 135ab98).

Locks the revenue decomposition formula R = Views × CVR × AOV and the
summarize/headline contract. End-to-end coverage exists at the
`/pro/revenue-autopsy` endpoint level; this file is the structural-unit
gate that catches silent numerical drift inside the 4 pure helpers.
"""
from __future__ import annotations

import pytest

from app.services.revenue_autopsy import (
    _build_headline,
    _compute_one_autopsy,
    _humanize_url,
    _summarize_autopsies,
)


# ---------------------------------------------------------------------------
# _humanize_url
# ---------------------------------------------------------------------------


class TestHumanizeUrl:
    def test_canonical_slug(self):
        assert (
            _humanize_url("/products/premium-leather-wallet")
            == "Premium Leather Wallet"
        )

    def test_underscore_separator(self):
        assert _humanize_url("/products/cool_thing") == "Cool Thing"

    def test_trailing_slash_stripped(self):
        assert _humanize_url("/products/abc/") == "Abc"

    def test_empty_returns_input(self):
        assert _humanize_url("") == ""

    def test_no_slug_segment(self):
        # rsplit returns the full string when no slash — title-case it
        assert _humanize_url("hello-world") == "Hello World"


# ---------------------------------------------------------------------------
# _compute_one_autopsy — the revenue decomposition formula
# ---------------------------------------------------------------------------


def _t(*, vr=0, vp=0, ur=None, up=None):
    """Build traffic dict; uniques default to views (one visitor per view)."""
    return {
        "views_recent": vr,
        "views_prior": vp,
        "uniques_recent": ur if ur is not None else vr,
        "uniques_prior": up if up is not None else vp,
    }


def _r(*, orec=0, opri=0, rec=0.0, pri=0.0):
    return {
        "orders_recent": orec,
        "orders_prior": opri,
        "revenue_recent": rec,
        "revenue_prior": pri,
    }


class TestComputeOneAutopsyShortCircuits:
    def test_returns_none_when_no_traffic_no_orders(self):
        assert _compute_one_autopsy("/products/x", _t(), _r()) is None

    def test_returns_none_when_traffic_below_threshold(self):
        # total_views=4 (<5) AND total_orders=1 (<2) → discarded
        result = _compute_one_autopsy(
            "/products/x", _t(vr=2, vp=2), _r(orec=1, opri=0)
        )
        assert result is None

    def test_keeps_when_orders_above_threshold(self):
        # total_views=4 BUT total_orders=3 ≥ 2 → kept
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=2, vp=2),
            _r(orec=2, opri=1, rec=100.0, pri=50.0),
        )
        assert result is not None

    def test_keeps_when_views_above_threshold(self):
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=50, vp=50),
            _r(orec=1, opri=0, rec=50.0, pri=0.0),
        )
        assert result is not None

    def test_returns_none_on_negligible_change(self):
        # rev_delta < 1 AND |traffic_change_pct| < 10 → discarded
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=105, vp=100),
            _r(orec=5, opri=5, rec=100.5, pri=100.0),
        )
        assert result is None


class TestComputeOneAutopsyDecomposition:
    """
    Pin the revenue decomposition formula:
        R = Views × CVR × AOV
        dR ≈ (dV × CVR₀ × AOV₀) + (V₀ × dCVR × AOV₀) + (V₀ × CVR₀ × dAOV)
    """

    def test_traffic_dominant_growing(self):
        # Views doubled (100→200), CVR and AOV held constant at 5% × €20
        # rev_prior = 100*0.05*20 = 100; rev_recent = 200*0.05*20 = 200
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=200, vp=100),
            _r(orec=10, opri=5, rec=200.0, pri=100.0),
        )
        assert result is not None
        assert result["direction"] == "growing"
        assert result["primary_cause"] == "traffic"
        assert result["traffic"]["change_pct"] == 100.0
        # traffic_impact = (200-100) * 0.05 * 20 = 100; matches rev_delta
        assert result["traffic"]["impact_eur"] == pytest.approx(100.0, abs=0.01)
        assert result["conversion"]["impact_eur"] == pytest.approx(0.0, abs=0.01)
        assert result["value"]["impact_eur"] == pytest.approx(0.0, abs=0.01)

    def test_conversion_dominant_declining(self):
        # Views held constant, CVR collapsed (5% → 1%), AOV constant at €20
        # rev_prior = 100*0.05*20 = 100; rev_recent = 100*0.01*20 = 20
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=100, vp=100),
            _r(orec=1, opri=5, rec=20.0, pri=100.0),
        )
        assert result is not None
        assert result["direction"] == "declining"
        assert result["primary_cause"] == "conversion"
        assert result["traffic"]["change_pct"] == 0.0
        # conversion_impact = 100 * (-0.04) * 20 = -80
        assert result["conversion"]["impact_eur"] == pytest.approx(-80.0, abs=0.01)

    def test_value_dominant_aov_shift(self):
        # Views constant, CVR constant (5%), AOV doubled (€20 → €40)
        # rev_prior = 100*0.05*20 = 100; rev_recent = 100*0.05*40 = 200
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=100, vp=100),
            _r(orec=5, opri=5, rec=200.0, pri=100.0),
        )
        assert result is not None
        assert result["direction"] == "growing"
        assert result["primary_cause"] == "value"
        # value_impact = rev_delta - traffic_impact - conversion_impact = 100 - 0 - 0
        assert result["value"]["impact_eur"] == pytest.approx(100.0, abs=0.01)

    def test_decomposition_sum_matches_revenue_delta(self):
        # Mixed: more traffic AND higher CVR AND higher AOV
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=150, vp=100),
            _r(orec=12, opri=5, rec=360.0, pri=100.0),
        )
        assert result is not None
        rev_delta = result["revenue_delta_eur"]
        sum_impacts = (
            result["traffic"]["impact_eur"]
            + result["conversion"]["impact_eur"]
            + result["value"]["impact_eur"]
        )
        # Sum of the 3 component impacts equals total revenue delta by
        # construction: value_impact = rev_delta - traffic - conversion.
        assert sum_impacts == pytest.approx(rev_delta, abs=0.5)


class TestComputeOneAutopsyOutputShape:
    def test_response_carries_all_top_level_keys(self):
        result = _compute_one_autopsy(
            "/products/leather-wallet",
            _t(vr=200, vp=100),
            _r(orec=10, opri=5, rec=200.0, pri=100.0),
        )
        assert result is not None
        assert set(result.keys()) == {
            "product_url",
            "product_name",
            "revenue_recent_7d",
            "revenue_prior_7d",
            "revenue_delta_eur",
            "direction",
            "primary_cause",
            "narrative",
            "traffic",
            "conversion",
            "value",
        }
        assert result["product_name"] == "Leather Wallet"
        assert result["direction"] == "growing"
        assert result["primary_cause"] in {"traffic", "conversion", "value"}

    def test_narrative_traffic_branch(self):
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=200, vp=100),
            _r(orec=10, opri=5, rec=200.0, pri=100.0),
        )
        assert result is not None
        assert "visitors" in result["narrative"]
        assert "+100%" in result["narrative"]

    def test_narrative_conversion_branch(self):
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=100, vp=100),
            _r(orec=1, opri=5, rec=20.0, pri=100.0),
        )
        assert result is not None
        assert "Conversion rate" in result["narrative"]
        assert "dropped" in result["narrative"]

    def test_narrative_value_branch(self):
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=100, vp=100),
            _r(orec=5, opri=5, rec=200.0, pri=100.0),
        )
        assert result is not None
        assert "Average order value" in result["narrative"]


class TestComputeOneAutopsyEdgeCases:
    def test_zero_prior_views_does_not_divide_by_zero(self):
        # Brand-new product: 0 views prior, 50 views recent
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=50, vp=0),
            _r(orec=2, opri=0, rec=40.0, pri=0.0),
        )
        # Either short-circuits via threshold or returns sane dict; must not
        # raise ZeroDivisionError.
        if result is not None:
            assert result["direction"] == "growing"

    def test_zero_orders_prior_does_not_divide_by_zero(self):
        result = _compute_one_autopsy(
            "/products/x",
            _t(vr=100, vp=100),
            _r(orec=5, opri=0, rec=100.0, pri=0.0),
        )
        # Cannot raise; aov_prior == 0 protected via guard
        if result is not None:
            assert result["value"]["aov_prior"] == 0


# ---------------------------------------------------------------------------
# _summarize_autopsies
# ---------------------------------------------------------------------------


def _autopsy(direction: str, delta: float, cause: str = "traffic") -> dict:
    return {
        "direction": direction,
        "revenue_delta_eur": delta,
        "primary_cause": cause,
    }


class TestSummarizeAutopsies:
    def test_splits_declining_and_growing(self):
        autopsies = [
            _autopsy("declining", -50.0),
            _autopsy("growing", 30.0),
            _autopsy("declining", -20.0),
        ]
        declining, growing, total_loss, total_gain, top_cause = _summarize_autopsies(
            autopsies
        )
        assert len(declining) == 2
        assert len(growing) == 1
        assert total_loss == pytest.approx(70.0)
        assert total_gain == pytest.approx(30.0)

    def test_total_loss_uses_absolute_values(self):
        autopsies = [_autopsy("declining", -100.0), _autopsy("declining", -50.0)]
        _, _, total_loss, _, _ = _summarize_autopsies(autopsies)
        assert total_loss == pytest.approx(150.0)

    def test_top_cause_is_most_common_decliner_cause(self):
        autopsies = [
            _autopsy("declining", -10.0, "traffic"),
            _autopsy("declining", -20.0, "traffic"),
            _autopsy("declining", -5.0, "conversion"),
            _autopsy("growing", 100.0, "value"),  # growing causes ignored
        ]
        _, _, _, _, top_cause = _summarize_autopsies(autopsies)
        assert top_cause == "traffic"

    def test_top_cause_is_none_when_no_decliners(self):
        autopsies = [_autopsy("growing", 50.0)]
        _, _, _, _, top_cause = _summarize_autopsies(autopsies)
        assert top_cause == "none"

    def test_empty_returns_zeros(self):
        declining, growing, total_loss, total_gain, top_cause = _summarize_autopsies([])
        assert declining == []
        assert growing == []
        assert total_loss == 0.0
        assert total_gain == 0.0
        assert top_cause == "none"


# ---------------------------------------------------------------------------
# _build_headline
# ---------------------------------------------------------------------------


def _identity_fmt(value, currency):
    """Test-only money formatter: returns "<int><iso>" deterministically."""
    return f"{value:.0f}{currency}"


class TestBuildHeadline:
    def test_declining_branch(self):
        declining = [_autopsy("declining", -50.0)]
        headline = _build_headline(declining, [], 50.0, 0.0, "traffic", "USD", _identity_fmt)
        assert headline == "1 products declining (−50USD/week). Main cause: traffic."

    def test_growing_branch(self):
        growing = [_autopsy("growing", 30.0), _autopsy("growing", 50.0)]
        headline = _build_headline([], growing, 0.0, 80.0, "none", "EUR", _identity_fmt)
        assert headline == "All 2 tracked products are growing (+80EUR/week)."

    def test_empty_branch(self):
        assert (
            _build_headline([], [], 0.0, 0.0, "none", "USD", _identity_fmt)
            == "Insufficient data for revenue autopsy this period."
        )

    def test_declining_uses_top_cause_string(self):
        declining = [_autopsy("declining", -10.0)]
        headline = _build_headline(declining, [], 10.0, 0.0, "conversion", "GBP", _identity_fmt)
        assert "Main cause: conversion." in headline
