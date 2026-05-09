"""Sprint 2 #4 — vertical-tuned prior tests for sip_engine.

Anti-cold-start: a shop with low data points should still get a sensible
cart_rate estimate by leaning on the vertical's industry baseline,
weighted via deterministic Bayesian shrinkage at n_prior=200. As the
shop accumulates events, the prior fades and shop signal dominates.

Tests cover the helper `_compute_vertical_prior` directly — pure
function over (vertical, observed_cart_rate, data_points, confidence).
The helper is leading-underscore-prefixed but stable enough to test
directly (same convention as `_compute_trust` and
`_autonomy_level_from_trust` already tested in test_agent_worker_smoke).
"""
from __future__ import annotations

from app.services.sip_engine import _compute_vertical_prior


def test_no_vertical_returns_none():
    """Caller passed no vertical → helper returns None (backward compat)."""
    out = _compute_vertical_prior(
        vertical=None,
        observed_cart_rate=0.04,
        data_points=500,
        confidence="medium",
    )
    assert out is None


def test_known_vertical_returns_block_with_baselines():
    """Beauty shop → block exposes vertical_prompt_pack baselines verbatim."""
    out = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=0.04,
        data_points=300,
        confidence="low",
    )
    assert out is not None
    assert out["vertical"] == "beauty"
    assert out["vertical_display"] == "Beauty & Cosmetics"
    assert out["cvr_baseline_pct"] == 3.2
    assert out["aov_baseline_eur"] == 42.0
    assert out["n_prior_strength"] == 200


def test_low_confidence_marks_applied_true():
    """Low-data shop → prior materially shifts the estimate, applied=True."""
    out = _compute_vertical_prior(
        vertical="electronics",
        observed_cart_rate=0.06,
        data_points=80,
        confidence="low",
    )
    assert out is not None
    assert out["applied"] is True


def test_high_confidence_marks_applied_false():
    """High-data shop → shop signal dominates, applied=False (telemetry-only)."""
    out = _compute_vertical_prior(
        vertical="electronics",
        observed_cart_rate=0.06,
        data_points=10000,
        confidence="high",
    )
    assert out is not None
    assert out["applied"] is False


def test_blended_cart_rate_low_volume_pulled_toward_prior():
    """Cold-start beauty shop (200 events, observed 6%): n_obs == n_prior →
    50/50 blend ≈ (0.06 + 0.032) / 2 = 0.046."""
    out = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=0.06,
        data_points=200,
        confidence="low",
    )
    assert out is not None
    blended = out["blended_cart_rate"]
    # (200*0.06 + 200*0.032) / 400 = 0.046
    assert abs(blended - 0.046) < 0.001


def test_blended_cart_rate_high_volume_dominates_prior():
    """Hot beauty shop (5000 events, observed 6%) → blended ≈ 0.0589
    (shop signal weight 25× the prior weight)."""
    out = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=0.06,
        data_points=5000,
        confidence="high",
    )
    assert out is not None
    blended = out["blended_cart_rate"]
    # (5000*0.06 + 200*0.032) / 5200 ≈ 0.05892
    assert abs(blended - 0.0589) < 0.0005


def test_data_points_capped_at_5000_for_blend():
    """data_points 50000 vs 5000 produce identical blend output —
    n_observed is capped at 5000 to keep the prior present at vanishing
    weight (anti-overconfidence in pure shop signal)."""
    out_capped = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=0.06,
        data_points=50000,
        confidence="high",
    )
    out_at_cap = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=0.06,
        data_points=5000,
        confidence="high",
    )
    assert out_capped["blended_cart_rate"] == out_at_cap["blended_cart_rate"]
    assert out_capped["n_observed"] == 5000


def test_unknown_vertical_falls_back_to_other_profile():
    """Unknown vertical string → vertical_prompt_pack.get_profile returns
    the 'other' profile (cvr 2.3%, aov 60€). No exception raised."""
    out = _compute_vertical_prior(
        vertical="quantum_computing",
        observed_cart_rate=0.04,
        data_points=100,
        confidence="low",
    )
    assert out is not None
    assert out["cvr_baseline_pct"] == 2.3
    assert out["aov_baseline_eur"] == 60.0


def test_observed_none_returns_prior_unchanged():
    """No shop signal yet → blended = prior unchanged (anti-cold-start
    upper bound: pure prior estimate)."""
    out = _compute_vertical_prior(
        vertical="beauty",
        observed_cart_rate=None,
        data_points=0,
        confidence="low",
    )
    assert out is not None
    # beauty prior = 3.2% = 0.032
    assert abs(out["blended_cart_rate"] - 0.032) < 0.0001


def test_deterministic_same_input_same_output():
    """Pure deterministic — repeat call produces identical output dict."""
    args = dict(
        vertical="beauty",
        observed_cart_rate=0.04,
        data_points=500,
        confidence="medium",
    )
    out_a = _compute_vertical_prior(**args)
    out_b = _compute_vertical_prior(**args)
    assert out_a == out_b


def test_compute_sip_signature_accepts_vertical_kwarg():
    """compute_sip surface contract: must accept vertical kwarg without
    raising. Smoke check on signature, not on full DB-driven path."""
    import inspect
    from app.services.sip_engine import compute_sip
    sig = inspect.signature(compute_sip)
    assert "vertical" in sig.parameters
    assert sig.parameters["vertical"].default is None
