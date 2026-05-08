"""Tests for app/core/stats.py — Welch's t-test + p-value helpers.

Extracted 2026-05-08 from app/services/fix_holdout_measurement.py.
The original tests in tests/test_fix_holdout_measurement.py were
deleted along with the parent module; these pin the contract for the
2 helpers that survived the cleanup.
"""
from __future__ import annotations

from app.core.stats import welch_t_test, two_sided_t_pvalue


def test_welch_identical_distributions_no_signal():
    """When treatment == control, lift is 0 and p_value is 1.0."""
    lift, p = welch_t_test([10, 10, 10, 10, 10], [10, 10, 10, 10, 10])
    assert lift == 0.0
    assert p == 1.0


def test_welch_clear_lift_returns_significant_p():
    """Strong signal (control 0s, treatment 10s, n=8 each) must produce
    a small p-value."""
    lift, p = welch_t_test([10] * 8, [0] * 8)
    assert lift == 10.0
    assert p < 0.001


def test_welch_small_sample_returns_neutral():
    """Sample size <2 in either arm → neutral (no signal claim)."""
    lift, p = welch_t_test([10], [5])
    assert lift == 0.0
    assert p == 1.0


def test_two_sided_t_pvalue_zero_t_returns_one():
    """t=0 means treatment_mean == control_mean → p=1.0 (no signal)."""
    assert two_sided_t_pvalue(0.0, 10.0) == 1.0


def test_two_sided_t_pvalue_negative_df_returns_one():
    """Defensive: df ≤ 0 → p=1.0 (no signal claim)."""
    assert two_sided_t_pvalue(2.0, 0.0) == 1.0


def test_two_sided_t_pvalue_high_t_close_to_zero():
    """Very high |t| with reasonable df → p approaches 0."""
    p = two_sided_t_pvalue(5.0, 30.0)
    assert p < 0.001
    assert p >= 0.0


def test_welch_lift_direction_treatment_minus_control():
    """Confirm: lift = treatment_mean - control_mean (treatment up = positive)."""
    lift, _ = welch_t_test([20, 20, 20], [10, 10, 10])
    assert lift == 10.0
    lift, _ = welch_t_test([10, 10, 10], [20, 20, 20])
    assert lift == -10.0
