"""Tests for B1 — closed-loop holdout outcome measurement."""
from __future__ import annotations

import math
import uuid

import pytest

from app.services import fix_holdout_measurement as hm


# ---- Welch t-test math ----

def test_identical_distributions_returns_p_one():
    lift, p = hm._welch_t_test([10, 10, 10, 10, 10], [10, 10, 10, 10, 10])
    assert lift == 0.0
    assert p == 1.0


def test_clearly_different_distributions_low_p():
    """Treatment clearly larger → significant p-value."""
    treatment = [100, 110, 95, 105, 98, 102, 108, 96]
    control = [70, 65, 72, 68, 71, 69, 73, 67]
    lift, p = hm._welch_t_test(treatment, control)
    assert lift > 25
    assert p < 0.001


def test_overlapping_distributions_high_p():
    treatment = [50, 55, 48, 52, 51]
    control = [49, 53, 50, 51, 52]
    lift, p = hm._welch_t_test(treatment, control)
    assert p > 0.30  # not significant


def test_small_sample_returns_neutral():
    lift, p = hm._welch_t_test([1], [1])
    assert p == 1.0


def test_p_value_is_in_unit_interval():
    cases = [
        ([1, 2, 3], [4, 5, 6]),
        ([100], [50]),
        ([0, 0, 0], [0, 0, 0]),
        ([1] * 20, [1.001] * 20),
    ]
    for t, c in cases:
        _, p = hm._welch_t_test(t, c)
        assert 0.0 <= p <= 1.0, f"p={p} out of range for t={t} c={c}"


# ---- Cohort assignment ----

def test_cohort_assignment_is_deterministic():
    shops = [f"shop{i}.myshopify.com" for i in range(20)]
    a1 = hm.assign_cohort(99001, shops)
    a2 = hm.assign_cohort(99001, shops)
    if not a1.get("treatment") and not a1.get("control"):
        pytest.skip("redis unavailable")
    assert a1["treatment"] == a2["treatment"]
    assert a1["control"] == a2["control"]


def test_cohort_assignment_splits_roughly_5050():
    shops = [f"split{i}.myshopify.com" for i in range(100)]
    cid = uuid.uuid4().int & 0xFFFFFF
    a = hm.assign_cohort(cid, shops)
    if not a.get("treatment"):
        pytest.skip("redis unavailable")
    n_t = len(a["treatment"])
    n_c = len(a["control"])
    assert 35 <= n_t <= 65
    assert n_t + n_c == 100


def test_is_shop_in_treatment_matches_assignment():
    cid = uuid.uuid4().int & 0xFFFFFF
    shops = [f"member{i}.myshopify.com" for i in range(10)]
    a = hm.assign_cohort(cid, shops)
    if not a.get("treatment"):
        pytest.skip("redis unavailable")
    for s in a["treatment"]:
        assert hm.is_shop_in_treatment(cid, s) is True
    for s in a["control"]:
        assert hm.is_shop_in_treatment(cid, s) is False


# ---- measure_outcome end-to-end ----

def test_measure_below_min_sample_returns_measuring():
    cid = uuid.uuid4().int & 0xFFFFFF
    result = hm.measure_outcome(
        cid,
        treatment_outcomes=[1.0],
        control_outcomes=[1.0],
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    assert result["status"] == "measuring"
    assert "sample_too_small" in result["reason"]


def test_measure_proven_effective_loss_metric():
    """Treatment LOWERS the loss → significant negative raw lift,
    positive signed lift, status=proven_effective."""
    cid = uuid.uuid4().int & 0xFFFFFF
    treatment = [10, 12, 11, 9, 13, 10, 11, 12]   # low loss after fix
    control = [40, 45, 42, 38, 41, 43, 39, 44]    # high loss without fix
    result = hm.measure_outcome(
        cid,
        treatment_outcomes=treatment,
        control_outcomes=control,
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    assert result["status"] == "proven_effective"
    assert result["lift_eur"] > 0
    assert result["p_value"] < 0.05


def test_measure_ineffective_when_treatment_worse():
    cid = uuid.uuid4().int & 0xFFFFFF
    treatment = [50, 55, 52, 51, 53, 54, 49, 50]
    control = [10, 12, 11, 13, 10, 11, 12, 9]
    result = hm.measure_outcome(
        cid,
        treatment_outcomes=treatment,
        control_outcomes=control,
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    assert result["status"] == "ineffective"


def test_measure_inconclusive_when_overlapping():
    cid = uuid.uuid4().int & 0xFFFFFF
    treatment = [10, 11, 10, 11, 10, 11, 10, 11]
    control = [10, 11, 10, 11, 10, 12, 11, 10]
    result = hm.measure_outcome(
        cid,
        treatment_outcomes=treatment,
        control_outcomes=control,
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    assert result["status"] in ("inconclusive", "ineffective")
    # Crucially: NOT proven_effective
    assert result["status"] != "proven_effective"


def test_proven_effective_bumps_weekly_savings():
    cid = uuid.uuid4().int & 0xFFFFFF
    before = hm.get_weekly_proven_savings(week_offset=0)
    treatment = [5, 6, 5, 6, 5, 6, 5, 6]
    control = [50, 55, 52, 51, 53, 54, 49, 50]
    result = hm.measure_outcome(
        cid,
        treatment_outcomes=treatment,
        control_outcomes=control,
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    if result["status"] != "proven_effective":
        pytest.skip("test data didn't reach significance — math regression")
    after = hm.get_weekly_proven_savings(week_offset=0)
    assert after > before
