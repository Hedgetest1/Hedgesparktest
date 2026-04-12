"""Tests for C2 — Bayesian predictive gate.

Locks the math: Beta lower 5% bound is conservative, monotonically
tightens with sample size, never goes outside [0, 1], punishes
unmeasured failures, and is more strict than the naive `wins/total`
ratio for small N.
"""
from __future__ import annotations

import pytest

from app.services.bugfix_pipeline import _beta_lower_bound_5pct


# ---- Edge cases ----

def test_zero_evidence_returns_zero():
    assert _beta_lower_bound_5pct(0, 0) == 0.0


def test_all_wins_low_n_is_strict():
    """5 wins, 0 losses → naive 1.0, Bayesian must be substantially lower."""
    bound = _beta_lower_bound_5pct(5, 0)
    assert 0.4 < bound < 0.85


def test_all_wins_high_n_is_close_to_one():
    """100 wins, 0 losses → Bayesian bound rises but never reaches 1."""
    bound = _beta_lower_bound_5pct(100, 0)
    assert bound > 0.94
    assert bound < 1.0


def test_zero_wins_gives_strict_low_bound():
    """0 wins, 5 losses → very low bound (we punish unmeasured failures)."""
    bound = _beta_lower_bound_5pct(0, 5)
    assert bound < 0.20


def test_balanced_outcomes_around_50pct():
    """50 wins, 50 losses → bound near 0.42 (slightly below center)."""
    bound = _beta_lower_bound_5pct(50, 50)
    assert 0.40 < bound < 0.46


def test_bound_monotonically_tightens_with_n():
    """Same win rate, increasing N → bound rises."""
    b10 = _beta_lower_bound_5pct(8, 2)   # 80% with n=10
    b50 = _beta_lower_bound_5pct(40, 10)  # 80% with n=50
    b500 = _beta_lower_bound_5pct(400, 100)  # 80% with n=500
    assert b10 < b50 < b500
    assert b500 > 0.76  # Approaches the true 0.80
    assert b10 < 0.65   # Penalized at low n


def test_bound_in_unit_interval_for_extreme_inputs():
    cases = [(0, 0), (0, 1000), (1000, 0), (1, 1), (1, 0), (0, 1)]
    for w, l in cases:
        bound = _beta_lower_bound_5pct(w, l)
        assert 0.0 <= bound <= 1.0, f"out of range for w={w} l={l}: {bound}"


# ---- Integration with predict_outcome_probability ----

def test_predict_returns_neutral_prior_when_no_history(monkeypatch):
    from unittest.mock import MagicMock
    from app.services.bugfix_pipeline import predict_outcome_probability

    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0, 0, 0)
    p, n = predict_outcome_probability(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert p == 0.5
    assert n == 0


def test_predict_uses_bayesian_when_history_present(monkeypatch):
    from unittest.mock import MagicMock
    from app.services.bugfix_pipeline import predict_outcome_probability

    db = MagicMock()
    # 8 effective, 10 measured, 0 hard fails, 10 total → wins=8 losses=2
    db.execute.return_value.fetchone.return_value = (8, 10, 0, 10)
    p, n = predict_outcome_probability(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert n == 10
    # Naive ratio would be 0.80; Bayesian lower bound at n=10 is ~0.55
    assert p < 0.80
    assert p > 0.40


def test_predict_punishes_hard_failures(monkeypatch):
    from unittest.mock import MagicMock
    from app.services.bugfix_pipeline import predict_outcome_probability

    db = MagicMock()
    # 5 effective, 5 measured, 5 hard fails → wins=5 losses=5
    db.execute.return_value.fetchone.return_value = (5, 5, 5, 10)
    p, n = predict_outcome_probability(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert n == 10
    # 50% rate at n=10 → bound around 0.30
    assert p < 0.45
