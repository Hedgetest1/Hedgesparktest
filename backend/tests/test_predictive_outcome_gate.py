"""
Tests for the predictive outcome gate added 2026-04-11.

Before run_auto_apply commits an apply, we estimate the probability that
a candidate will end effective based on historical (affected_domain,
source_type) outcomes. Candidates with < 25% predicted effective rate
(with at least 5 samples of history) are downgraded to manual review
instead of burning apply budget.

The gate is the competitive moat: a fresh deployment has no history and
the gate stays neutral; as your merchant base accumulates outcome data
over months, the gate tightens specifically around YOUR failure modes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    predict_outcome_probability,
    should_skip_apply_by_prediction,
    _PREDICT_OUTCOME_DEFAULT_FLOOR,
    _PREDICT_OUTCOME_MIN_SAMPLES,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_history(db, *, domain: str, source_type: str,
                effective: int, ineffective: int, failed: int):
    """Plant historical BugFixCandidate rows so the predictor has data."""
    base = _now() - timedelta(days=30)
    created = []
    for i in range(effective):
        c = BugFixCandidate(
            source_type=source_type,
            source_ref=f"hist_eff_{domain}_{i}",
            title="historical eff",
            status="applied",
            outcome_status="effective",
            affected_domain=domain,
            created_at=base + timedelta(minutes=i),
            applied_at=base + timedelta(minutes=i),
        )
        db.add(c)
        created.append(c)
    for i in range(ineffective):
        c = BugFixCandidate(
            source_type=source_type,
            source_ref=f"hist_ineff_{domain}_{i}",
            title="historical ineff",
            status="applied",
            outcome_status="ineffective",
            affected_domain=domain,
            created_at=base + timedelta(minutes=100 + i),
            applied_at=base + timedelta(minutes=100 + i),
        )
        db.add(c)
        created.append(c)
    for i in range(failed):
        c = BugFixCandidate(
            source_type=source_type,
            source_ref=f"hist_failed_{domain}_{i}",
            title="historical fail",
            status="apply_failed",
            affected_domain=domain,
            created_at=base + timedelta(minutes=200 + i),
        )
        db.add(c)
        created.append(c)
    db.flush()
    return created


# ---------------------------------------------------------------------------
# predict_outcome_probability — pure read
# ---------------------------------------------------------------------------

def test_predict_neutral_with_insufficient_history(db):
    """Fewer than MIN_SAMPLES history → neutral 0.5."""
    _mk_history(db, domain="newdom_5", source_type="ops_alert",
                effective=1, ineffective=1, failed=0)
    p, n = predict_outcome_probability(
        db, affected_domain="newdom_5", source_type="ops_alert",
    )
    assert p == 0.5
    assert n == 2


def test_predict_effective_domain(db):
    """A domain with strong positive history predicts effective.

    C2 update (2026-04-11): the gate now uses the Bayesian lower 5%
    bound of Beta(wins+1, fails+1), not the naive ratio. At n=9 with
    8 wins and 1 loss the bound sits ~0.62 — more conservative than
    the naive 0.88, intentionally so. The bound rises toward 1 as n
    grows. We assert the bound is above the FLOOR (0.25) and tracks
    the win rate qualitatively.
    """
    _mk_history(db, domain="strong_dom", source_type="ops_alert",
                effective=8, ineffective=1, failed=0)
    p, n = predict_outcome_probability(
        db, affected_domain="strong_dom", source_type="ops_alert",
    )
    assert p > 0.50  # safely above the 0.25 auto-apply floor
    assert p < 0.88  # but conservatively below the naive ratio
    assert n >= _PREDICT_OUTCOME_MIN_SAMPLES


def test_predict_weak_domain(db):
    """A domain that fails 4 of 5 times predicts ineffective."""
    _mk_history(db, domain="weak_dom", source_type="ops_alert",
                effective=1, ineffective=3, failed=2)
    p, n = predict_outcome_probability(
        db, affected_domain="weak_dom", source_type="ops_alert",
    )
    # 1 effective / (1+3+2) = 1/6 = 0.17
    assert p < _PREDICT_OUTCOME_DEFAULT_FLOOR
    assert n >= _PREDICT_OUTCOME_MIN_SAMPLES


def test_predict_scoped_by_source_type(db):
    """evolution history does NOT affect ops_alert predictions."""
    _mk_history(db, domain="mixed_dom", source_type="evolution",
                effective=0, ineffective=10, failed=0)
    p, n = predict_outcome_probability(
        db, affected_domain="mixed_dom", source_type="ops_alert",
    )
    # No ops_alert history for this domain → neutral
    assert p == 0.5


# ---------------------------------------------------------------------------
# should_skip_apply_by_prediction — decision boundary
# ---------------------------------------------------------------------------

def test_skip_decision_passes_with_no_history(db):
    """Fresh domain with no data → caller should NOT skip."""
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref="skip_neutral",
        title="fresh",
        status="patch_proposed",
        affected_domain="brand_new_dom",
        created_at=_now(),
    )
    db.add(c)
    db.flush()
    skip, reason = should_skip_apply_by_prediction(db, c)
    assert skip is False
    assert "insufficient_history" in reason


def test_skip_decision_blocks_weak_domain(db):
    """A strong negative history → caller SHOULD skip."""
    _mk_history(db, domain="doomed_dom", source_type="evolution",
                effective=0, ineffective=6, failed=4)
    c = BugFixCandidate(
        source_type="evolution",
        source_ref="skip_weak",
        title="doomed",
        status="patch_proposed",
        affected_domain="doomed_dom",
        created_at=_now(),
    )
    db.add(c)
    db.flush()
    skip, reason = should_skip_apply_by_prediction(db, c)
    assert skip is True
    assert "predicted_effective_pct" in reason


def test_skip_decision_allows_strong_domain(db):
    """A strong positive history → caller should NOT skip."""
    _mk_history(db, domain="trusted_dom", source_type="evolution",
                effective=9, ineffective=1, failed=0)
    c = BugFixCandidate(
        source_type="evolution",
        source_ref="skip_strong",
        title="trusted",
        status="patch_proposed",
        affected_domain="trusted_dom",
        created_at=_now(),
    )
    db.add(c)
    db.flush()
    skip, _ = should_skip_apply_by_prediction(db, c)
    assert skip is False


def test_prediction_returns_neutral_for_null_inputs(db):
    """Missing affected_domain or source_type → neutral, never errors."""
    p, n = predict_outcome_probability(db, affected_domain=None, source_type=None)
    assert p == 0.5
    assert n == 0
