"""
stats.py — pure-Python statistical helpers used by holdout measurement.

Extracted 2026-05-08 from app/services/fix_holdout_measurement.py during
the Stage 2-E supersession cleanup. The wider fix_holdout_measurement
module (assign_cohort, measure_outcome, get_weekly_proven_savings)
targeted the deleted bugfix_pipeline; only these 2 t-test helpers
remained in active use by report_holdout_lift.

Zero external dependencies. Deterministic. ~30 lines per primitive.
"""
from __future__ import annotations

import math


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch_t_test(treatment: list[float], control: list[float]) -> tuple[float, float]:
    """Return (lift, p_value).

    Lift is treatment_mean - control_mean (positive = treatment improved
    the metric direction). The caller is responsible for choosing a
    metric where bigger=better.

    p_value uses Welch's t-test with the survival function of a
    Student's t computed via the regularized incomplete beta function
    approximation. ~30 lines of pure Python, accurate to ~0.01 in the
    range we care about.
    """
    n_t = len(treatment)
    n_c = len(control)
    if n_t < 2 or n_c < 2:
        return 0.0, 1.0

    m_t = _mean(treatment)
    m_c = _mean(control)
    v_t = _variance(treatment)
    v_c = _variance(control)
    lift = m_t - m_c

    if v_t == 0 and v_c == 0:
        # Identical distributions → no signal
        return lift, 1.0 if lift == 0 else 0.0

    se = math.sqrt(v_t / n_t + v_c / n_c)
    if se == 0:
        return lift, 1.0
    t = lift / se

    # Welch-Satterthwaite degrees of freedom
    num = (v_t / n_t + v_c / n_c) ** 2
    denom = (v_t / n_t) ** 2 / (n_t - 1) + (v_c / n_c) ** 2 / (n_c - 1)
    df = num / denom if denom > 0 else max(1, n_t + n_c - 2)

    p_value = two_sided_t_pvalue(abs(t), df)
    return lift, p_value


def two_sided_t_pvalue(t_abs: float, df: float) -> float:
    """Two-sided p-value of Student's t given |t| and degrees of freedom.

    Uses the regularized incomplete beta function via the continued
    fraction expansion. Pure Python, deterministic, ~ 0.005 accuracy
    for df > 1.
    """
    if t_abs == 0 or df <= 0:
        return 1.0
    x = df / (df + t_abs * t_abs)
    a = df / 2.0
    b = 0.5
    return _regularized_incomplete_beta(x, a, b)


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a, b). Used for the t p-value.

    Numerical Recipes algorithm. Stable for the parameter ranges we
    encounter (df ≥ 1, x ∈ (0, 1)).
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1 - x)
    )
    if x < (a + 1) / (a + b + 2):
        return bt * _beta_continued_fraction(x, a, b) / a
    return 1.0 - bt * _beta_continued_fraction(1 - x, b, a) / b


def _beta_continued_fraction(x: float, a: float, b: float, max_iter: int = 200) -> float:
    eps = 3e-7
    qab = a + b
    qap = a + 1
    qam = a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h
