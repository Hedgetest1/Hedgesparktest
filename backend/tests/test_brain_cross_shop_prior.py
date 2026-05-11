"""Sprint 3 #3 — soft-prior consultation tests for _decide.

Covers _apply_cross_shop_prior matrix:
  - state.cross_shop_priors is None / [] → unchanged
  - candidate.action_kind already no_action_* → unchanged
  - no matching prior for this (action, metric) → unchanged
  - low-confidence prior (any sign) → unchanged
  - medium/high-confidence positive prior → unchanged
  - medium/high-confidence negative prior → DEMOTED
  - demoted draft preserves original metric / window / baseline + telemetry

Tests are pure-function — no DB needed (MerchantState built synthetically).
"""
from __future__ import annotations

from app.services.merchant_brain import (
    BrainDecisionDraft,
    MerchantState,
    _apply_cross_shop_prior,
    _decide,
)


def _baseline_state(**overrides) -> MerchantState:
    """Build a state that triggers the rule-table 'high RAR + stale' rule
    (recovery_digest). Override fields per test."""
    defaults = dict(
        shop_domain="t.myshopify.com",
        rars_total_eur=5000.0,
        churn_risk_level="low",
        recent_orders_7d=0,
        recent_events_24h=10,
        hours_since_install=100,
        last_action_age_hours=80,
        last_chat_age_hours=None,
        last_brain_decision_age_hours=None,
        has_email_in_queue=False,
        currency="USD",
    )
    defaults.update(overrides)
    return MerchantState(**defaults)


def _baseline_candidate() -> BrainDecisionDraft:
    return BrainDecisionDraft(
        action_kind="recovery_digest",
        action_payload={"rars_focus_eur": 5000},
        rationale="rars=5000, last_action=80h",
        expected_outcome_metric="rars_delta_7d",
        outcome_window_hours=168,
        baseline_value=5000.0,
    )


def _prior(action_kind="recovery_digest",
           metric_kind="rars_delta_7d",
           lift=-2.5, confidence="high",
           n_shops=10, n_decisions=40, p_value=0.02):
    return {
        "action_kind": action_kind,
        "metric_kind": metric_kind,
        "lift_pct_avg": lift,
        "lift_pct_std": 0.3,
        "n_shops": n_shops,
        "n_decisions": n_decisions,
        "p_value": p_value,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# _apply_cross_shop_prior direct
# ---------------------------------------------------------------------------


def test_apply_prior_none_returns_unchanged():
    state = _baseline_state()  # cross_shop_priors defaults to None
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result is candidate  # identity — no new object created


def test_apply_prior_empty_list_returns_unchanged():
    state = _baseline_state()
    state.cross_shop_priors = []
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result is candidate


def test_apply_prior_skips_no_action_candidates():
    """Cooldown / no_signal drafts must not be touched by the prior layer."""
    state = _baseline_state()
    state.cross_shop_priors = [_prior(lift=-5.0, confidence="high")]
    candidate = BrainDecisionDraft(
        action_kind="no_action_cooldown",
        action_payload={},
        rationale="cooldown",
        expected_outcome_metric="cooldown_pending",
        outcome_window_hours=24,
        baseline_value=None,
    )
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "no_action_cooldown"


def test_apply_prior_no_matching_signal_returns_unchanged():
    """Prior exists but for a different (action, metric) → no-op."""
    state = _baseline_state()
    state.cross_shop_priors = [
        _prior(action_kind="retention_outreach_email",
               metric_kind="merchant_re_engaged_7d",
               lift=-5.0, confidence="high"),
    ]
    candidate = _baseline_candidate()  # recovery_digest / rars_delta_7d
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "recovery_digest"


def test_apply_prior_low_confidence_negative_does_not_demote():
    """Signal too weak → keep rule-table choice."""
    state = _baseline_state()
    state.cross_shop_priors = [_prior(lift=-5.0, confidence="low")]
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "recovery_digest"


def test_apply_prior_positive_high_confidence_does_not_demote():
    """Vertical evidence reinforces the candidate → unchanged."""
    state = _baseline_state()
    state.cross_shop_priors = [_prior(lift=+6.8, confidence="high")]
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "recovery_digest"


def test_apply_prior_zero_lift_does_not_demote():
    """lift == 0 is not 'negative' → no demotion (caller intent: only
    DEMOTE when vertical measured the action regresses)."""
    state = _baseline_state()
    state.cross_shop_priors = [_prior(lift=0.0, confidence="high")]
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "recovery_digest"


def test_apply_prior_negative_high_confidence_demotes():
    state = _baseline_state(vertical="apparel")
    state.vertical = "apparel"
    state.cross_shop_priors = [_prior(lift=-2.5, confidence="high")]
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "no_action_demoted_by_vertical_evidence"
    # Telemetry preserved for post-hoc analysis
    assert result.action_payload["original_action_kind"] == "recovery_digest"
    assert result.action_payload["prior_lift_pct"] == -2.5
    assert result.action_payload["prior_confidence"] == "high"
    assert result.action_payload["prior_n_shops"] == 10
    assert result.action_payload["vertical"] == "apparel"
    # Original outcome metric / window preserved (so _measure compares
    # the same yardstick across treatment + demotion arms post-hoc)
    assert result.expected_outcome_metric == "rars_delta_7d"
    assert result.outcome_window_hours == 168
    assert result.baseline_value == 5000.0
    # Rationale chain auditable
    assert "rule-table" in result.rationale
    assert "demoted" in result.rationale


def test_apply_prior_negative_medium_confidence_demotes():
    """Medium confidence + negative → demote (only LOW is skipped)."""
    state = _baseline_state()
    state.cross_shop_priors = [_prior(lift=-1.5, confidence="medium")]
    candidate = _baseline_candidate()
    result = _apply_cross_shop_prior(candidate, state)
    assert result.action_kind == "no_action_demoted_by_vertical_evidence"


# ---------------------------------------------------------------------------
# _decide end-to-end (rule-table + prior chained)
# ---------------------------------------------------------------------------


def test_decide_demotes_recovery_digest_when_vertical_says_negative():
    state = _baseline_state(vertical="apparel")
    state.cross_shop_priors = [_prior(lift=-3.0, confidence="high")]
    decision = _decide(state)
    assert decision.action_kind == "no_action_demoted_by_vertical_evidence"


def test_decide_cooldown_bypasses_prior_consultation():
    """Cooldown is hit BEFORE the rule-table picks recovery_digest →
    prior consultation never runs on cooldown decisions."""
    state = _baseline_state(last_brain_decision_age_hours=1.0)
    state.cross_shop_priors = [_prior(lift=-5.0, confidence="high")]
    decision = _decide(state)
    assert decision.action_kind == "no_action_cooldown"


def test_decide_no_signal_default_not_demoted():
    """Default no_action_no_signal is already a no-dispatch → no-op."""
    state = _baseline_state(
        rars_total_eur=0.0, churn_risk_level="low",
        recent_orders_7d=5, recent_events_24h=200,
        hours_since_install=500, last_action_age_hours=10,
    )
    state.cross_shop_priors = [_prior(lift=-5.0, confidence="high")]
    decision = _decide(state)
    # Either no_action_no_signal OR proactive_nudge — both fine, not demoted
    assert decision.action_kind != "no_action_demoted_by_vertical_evidence"
