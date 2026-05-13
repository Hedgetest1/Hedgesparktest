"""
Unit tests for the pure helpers extracted from
`compute_post_execution_deltas` in the 2026-05-12 A3 refactor
(commit 932870c).

End-to-end coverage exists in `test_execution_engine_deltas.py`; this
file is the structural unit gate for the 3 pure helpers carrying the
counterfactual math (rate / lift / 20-field metrics composer) and the
`_compute_confidence` ladder that produces the merchant-visible
strong/moderate/low label.
"""
from __future__ import annotations

import pytest

from app.services.execution_engine import (
    _compute_confidence,
    _compute_opp_metrics,
    _lift,
    _rate,
)


# ---------------------------------------------------------------------------
# _rate — pure rounded-rate calculation
# ---------------------------------------------------------------------------


class TestRate:
    def test_canonical(self):
        assert _rate(1, 4) == 0.25

    def test_rounds_to_4_places(self):
        # 1/3 = 0.3333333... → 0.3333
        assert _rate(1, 3) == 0.3333

    def test_zero_denominator_returns_none(self):
        assert _rate(5, 0) is None

    def test_negative_denominator_returns_none(self):
        # `den > 0` guard catches both 0 and negative
        assert _rate(5, -1) is None

    def test_zero_numerator(self):
        assert _rate(0, 10) == 0.0

    def test_numerator_above_denominator(self):
        # rates above 1.0 are allowed (used for impact deltas elsewhere)
        assert _rate(15, 10) == 1.5


# ---------------------------------------------------------------------------
# _lift — counterfactual diff with None propagation
# ---------------------------------------------------------------------------


class TestLift:
    def test_positive_lift(self):
        assert _lift(0.10, 0.05) == 0.05

    def test_negative_lift(self):
        assert _lift(0.05, 0.10) == -0.05

    def test_rounds_to_4_places(self):
        assert _lift(0.123456, 0.0) == 0.1235

    def test_first_none_returns_none(self):
        assert _lift(None, 0.05) is None

    def test_second_none_returns_none(self):
        assert _lift(0.10, None) is None

    def test_both_none_returns_none(self):
        assert _lift(None, None) is None

    def test_zero_diff(self):
        assert _lift(0.05, 0.05) == 0.0


# ---------------------------------------------------------------------------
# _compute_opp_metrics — 20-field counterfactual composer
# ---------------------------------------------------------------------------


def _opp(eid="exec_1", executed_at="2026-05-12", bl_ret=0.10, bl_view=0.20, bl_purchase=0.05):
    """Tuple-shaped Row in the position layout the helper consumes."""
    return (eid, executed_at, bl_ret, bl_view, bl_purchase)


def _group(*, n=0, ret=0, viewed=0, purchased=0):
    return {"n": n, "ret": ret, "viewed": viewed, "purchased": purchased}


class TestComputeOppMetricsShortCircuits:
    def test_returns_none_when_total_post_zero(self):
        assert (
            _compute_opp_metrics(_opp(), _group(n=0), _group(n=0), leakage=0.0)
            is None
        )

    def test_proceeds_with_exposed_only(self):
        result = _compute_opp_metrics(
            _opp(), _group(n=10, viewed=5), _group(n=0), leakage=0.0
        )
        assert result is not None
        assert result["exp_n"] == 10
        assert result["hld_n"] == 0

    def test_proceeds_with_holdout_only(self):
        result = _compute_opp_metrics(
            _opp(), _group(n=0), _group(n=10, viewed=3), leakage=0.0
        )
        assert result is not None
        assert result["exp_n"] == 0
        assert result["hld_n"] == 10


class TestComputeOppMetricsShape:
    def test_all_20_fields_present(self):
        result = _compute_opp_metrics(
            _opp(),
            _group(n=20, ret=4, viewed=10, purchased=2),
            _group(n=10, ret=1, viewed=3, purchased=0),
            leakage=0.1,
        )
        assert result is not None
        assert set(result.keys()) == {
            "eid",
            "post_ret", "post_view", "post_purchase",
            "total_post",
            "d_ret", "d_view", "d_purchase",
            "exp_n", "hld_n",
            "rr_exp", "vr_exp", "pr_exp",
            "rr_hld", "vr_hld", "pr_hld",
            "lift_ret", "lift_view", "lift_purchase",
            "conf",
        }

    def test_eid_is_preserved(self):
        result = _compute_opp_metrics(
            _opp(eid="exec_abc"),
            _group(n=5, viewed=2),
            _group(n=0),
            leakage=0.0,
        )
        assert result is not None
        assert result["eid"] == "exec_abc"

    def test_total_post_is_exp_plus_hld(self):
        result = _compute_opp_metrics(
            _opp(), _group(n=15), _group(n=8), leakage=0.0
        )
        assert result is not None
        assert result["total_post"] == 23


class TestComputeOppMetricsRates:
    def test_exposed_rates(self):
        result = _compute_opp_metrics(
            _opp(),
            _group(n=20, ret=4, viewed=10, purchased=2),
            _group(n=10),
            leakage=0.0,
        )
        assert result is not None
        assert result["rr_exp"] == 0.2  # 4/20
        assert result["vr_exp"] == 0.5  # 10/20
        assert result["pr_exp"] == 0.1  # 2/20

    def test_holdout_rates(self):
        result = _compute_opp_metrics(
            _opp(),
            _group(n=20),
            _group(n=10, ret=2, viewed=3, purchased=1),
            leakage=0.0,
        )
        assert result is not None
        assert result["rr_hld"] == 0.2  # 2/10
        assert result["vr_hld"] == 0.3  # 3/10
        assert result["pr_hld"] == 0.1  # 1/10

    def test_lift_is_exp_minus_hld(self):
        # exp: ret=4/20=0.2, viewed=10/20=0.5, purchased=2/20=0.1
        # hld: ret=1/10=0.1, viewed=3/10=0.3, purchased=0/10=0.0
        # lift_ret = 0.2-0.1 = 0.1; lift_view = 0.5-0.3 = 0.2; lift_purchase = 0.1-0.0 = 0.1
        result = _compute_opp_metrics(
            _opp(),
            _group(n=20, ret=4, viewed=10, purchased=2),
            _group(n=10, ret=1, viewed=3, purchased=0),
            leakage=0.0,
        )
        assert result is not None
        assert result["lift_ret"] == 0.1
        assert result["lift_view"] == 0.2
        assert result["lift_purchase"] == 0.1


class TestComputeOppMetricsBaselineDeltas:
    def test_delta_against_baseline(self):
        # post_ret across exp+hld: (4+1)/30 = 0.1667
        # baseline = 0.10 → d_ret = 0.1667 - 0.10 = 0.0667
        result = _compute_opp_metrics(
            _opp(bl_ret=0.10, bl_view=0.20, bl_purchase=0.05),
            _group(n=20, ret=4, viewed=10, purchased=2),
            _group(n=10, ret=1, viewed=3, purchased=0),
            leakage=0.0,
        )
        assert result is not None
        assert result["d_ret"] == pytest.approx(0.0667, abs=0.0001)
        # post_view = (10+3)/30 = 0.4333; d_view = 0.4333 - 0.20 = 0.2333
        assert result["d_view"] == pytest.approx(0.2333, abs=0.0001)
        # post_purchase = (2+0)/30 = 0.0667; d_purchase = 0.0667 - 0.05 = 0.0167
        assert result["d_purchase"] == pytest.approx(0.0167, abs=0.0001)

    def test_delta_none_when_baseline_none(self):
        opp = (
            "eid",
            "2026-05-12",
            None,  # bl_return = NULL
            0.20,
            0.05,
        )
        result = _compute_opp_metrics(
            opp,
            _group(n=10, ret=2, viewed=4, purchased=1),
            _group(n=5, ret=1, viewed=1, purchased=0),
            leakage=0.0,
        )
        assert result is not None
        assert result["d_ret"] is None
        assert result["d_view"] is not None
        assert result["d_purchase"] is not None


# ---------------------------------------------------------------------------
# _compute_confidence — strong/moderate/low ladder
# ---------------------------------------------------------------------------


class TestComputeConfidence:
    def test_below_min_exposed_returns_low(self):
        assert (
            _compute_confidence(
                exposed_n=4, holdout_n=5,
                lift_view=0.05, lift_purchase=0.05,
                has_baseline=True, leakage_rate=0.0,
            )
            == "low"
        )

    def test_leakage_above_30_returns_low(self):
        assert (
            _compute_confidence(
                exposed_n=30, holdout_n=10,
                lift_view=0.10, lift_purchase=0.10,
                has_baseline=True, leakage_rate=0.31,
            )
            == "low"
        )

    def test_strong_via_purchase_lift(self):
        # purchase_lift >= 0.02, leakage <= 0.2, exp >= 20, hld >= 5
        assert (
            _compute_confidence(
                exposed_n=20, holdout_n=5,
                lift_view=0.0, lift_purchase=0.02,
                has_baseline=True, leakage_rate=0.0,
            )
            == "strong"
        )

    def test_strong_via_view_lift(self):
        # view_lift >= 0.03, leakage <= 0.2, exp >= 20, hld >= 5
        assert (
            _compute_confidence(
                exposed_n=20, holdout_n=5,
                lift_view=0.03, lift_purchase=0.0,
                has_baseline=True, leakage_rate=0.20,
            )
            == "strong"
        )

    def test_strong_blocked_by_leakage_above_20(self):
        # 0.21 leakage falls out of strong (cap 0.2) but qualifies for moderate
        result = _compute_confidence(
            exposed_n=20, holdout_n=5,
            lift_view=0.03, lift_purchase=0.02,
            has_baseline=True, leakage_rate=0.21,
        )
        assert result == "moderate"

    def test_moderate_with_lower_thresholds(self):
        # exp >= 10, hld >= 3, any positive lift, leakage <= 0.3
        assert (
            _compute_confidence(
                exposed_n=10, holdout_n=3,
                lift_view=0.001, lift_purchase=0.0,
                has_baseline=True, leakage_rate=0.3,
            )
            == "moderate"
        )

    def test_low_when_no_positive_lift(self):
        assert (
            _compute_confidence(
                exposed_n=50, holdout_n=20,
                lift_view=-0.01, lift_purchase=-0.01,
                has_baseline=True, leakage_rate=0.0,
            )
            == "low"
        )

    def test_low_when_lifts_are_none(self):
        assert (
            _compute_confidence(
                exposed_n=50, holdout_n=20,
                lift_view=None, lift_purchase=None,
                has_baseline=False, leakage_rate=0.0,
            )
            == "low"
        )

    def test_moderate_blocked_when_holdout_too_small(self):
        # holdout_n=2 below moderate threshold (3); falls to low
        result = _compute_confidence(
            exposed_n=10, holdout_n=2,
            lift_view=0.05, lift_purchase=0.05,
            has_baseline=True, leakage_rate=0.0,
        )
        assert result == "low"

    def test_exact_threshold_boundary_strong(self):
        # exact boundary purchase_lift==0.02 → strong
        assert (
            _compute_confidence(
                exposed_n=20, holdout_n=5,
                lift_view=0.0, lift_purchase=0.02,
                has_baseline=True, leakage_rate=0.20,
            )
            == "strong"
        )

    def test_just_below_strong_purchase_threshold(self):
        # purchase_lift==0.0199 < 0.02 → falls to moderate
        result = _compute_confidence(
            exposed_n=20, holdout_n=5,
            lift_view=0.0, lift_purchase=0.0199,
            has_baseline=True, leakage_rate=0.0,
        )
        assert result == "moderate"
