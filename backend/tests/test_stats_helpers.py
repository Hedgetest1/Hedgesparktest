"""Tests for app/core/stats.py — Welch's t-test + p-value helpers.

Extracted 2026-05-08 from app/services/fix_holdout_measurement.py.
The original tests in tests/test_fix_holdout_measurement.py were
deleted along with the parent module; these pin the contract for the
2 helpers that survived the cleanup.
"""
from __future__ import annotations

import pytest

from app.core.stats import vertical_blend, welch_t_test, two_sided_t_pvalue


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


# ── vertical_blend (Sprint 2 #4 vertical-tuned priors) ──

def test_vertical_blend_cold_start_uses_prior_heavily():
    """Cold-start shop (n_observed=10) blended toward vertical prior at n_prior=200."""
    out = vertical_blend(observed=0.06, prior=0.032, n_observed=10, n_prior=200)
    # posterior = (10*0.06 + 200*0.032) / 210 = (0.6 + 6.4) / 210 ≈ 0.0333
    assert abs(out - 0.0333) < 0.001
    # Closer to prior (0.032) than to observed (0.06)
    assert abs(out - 0.032) < abs(out - 0.06)


def test_vertical_blend_high_volume_shop_dominates_prior():
    """High-volume shop (n_observed=10000) → posterior ≈ observed."""
    out = vertical_blend(observed=0.06, prior=0.032, n_observed=10000, n_prior=200)
    # posterior = (10000*0.06 + 200*0.032) / 10200 ≈ 0.0594
    assert abs(out - 0.0594) < 0.0005
    # Posterior is now closer to observed than to prior
    assert abs(out - 0.06) < abs(out - 0.032)


def test_vertical_blend_observed_none_returns_prior():
    """Defensive: observed=None → return prior unchanged (anti-cold-start)."""
    assert vertical_blend(observed=None, prior=0.032, n_observed=0, n_prior=200) == 0.032


def test_vertical_blend_prior_none_returns_observed():
    """Defensive: prior=None → return observed unchanged."""
    assert vertical_blend(observed=0.041, prior=None, n_observed=500, n_prior=200) == 0.041


def test_vertical_blend_both_none_returns_none():
    """Defensive: both None → None (caller decides default)."""
    assert vertical_blend(observed=None, prior=None, n_observed=0, n_prior=200) is None


def test_vertical_blend_zero_n_observed_returns_prior():
    """n_observed=0 with n_prior=200 → posterior = prior (no shop signal yet)."""
    assert vertical_blend(observed=0.06, prior=0.032, n_observed=0, n_prior=200) == 0.032


def test_vertical_blend_zero_n_prior_returns_observed():
    """n_prior=0 → posterior = observed (no smoothing applied)."""
    assert vertical_blend(observed=0.06, prior=0.032, n_observed=100, n_prior=0) == 0.06


def test_vertical_blend_deterministic_same_input_same_output():
    """Pure function: same input → identical output across calls."""
    out_a = vertical_blend(observed=0.06, prior=0.032, n_observed=42, n_prior=200)
    out_b = vertical_blend(observed=0.06, prior=0.032, n_observed=42, n_prior=200)
    assert out_a == out_b


def test_vertical_blend_negative_n_raises():
    """Defensive: negative sample sizes are programmer error."""
    with pytest.raises(ValueError):
        vertical_blend(observed=0.06, prior=0.032, n_observed=-1, n_prior=200)
    with pytest.raises(ValueError):
        vertical_blend(observed=0.06, prior=0.032, n_observed=10, n_prior=-1)


def test_vertical_blend_zero_denom_returns_prior():
    """Both n_observed=0 and n_prior=0 → degenerate; fall back to prior."""
    assert vertical_blend(observed=0.06, prior=0.032, n_observed=0, n_prior=0) == 0.032
