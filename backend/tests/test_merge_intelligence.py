"""Tests for merge recommendation + post-merge outcome tracking."""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.autofix_promotion import AutoFixPromotion
from app.models.bugfix_candidate import BugFixCandidate
from app.models.merge_outcome import MergeOutcome
from app.models.ops_alert import OpsAlert
from app.services.merge_intelligence import (
    compute_merge_recommendation,
    create_merge_outcome,
    evaluate_merge_outcomes,
    get_merge_outcome_summary,
    _MIN_EVAL_DELAY_MINUTES,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_full_setup(db, ci_passed=True, tier=0, applied=True, has_pr=True):
    """Create candidate + promotion with all fields for recommendation testing."""
    c = BugFixCandidate(
        source_type="manual", source_ref="merge_test",
        title="Merge test", status="applied" if applied else "open",
        patch_risk_tier=tier,
        applied_at=_now() - timedelta(hours=1) if applied else None,
    )
    db.add(c)
    db.flush()

    p = AutoFixPromotion(
        bugfix_candidate_id=c.id,
        git_commit_sha="merge_sha_test",
        status="pushed",
        branch_name="autofix/merge-test",
        pushed_at=_now(),
        remote_ci_status="passed" if ci_passed else "failed",
        pr_url="https://github.com/pr/1" if has_pr else None,
        pr_number=1 if has_pr else None,
    )
    db.add(p)
    db.flush()
    return c, p


# ---------------------------------------------------------------------------
# Merge recommendation
# ---------------------------------------------------------------------------

def test_recommend_merge_all_gates_pass(db):
    """All gates pass → recommend=True."""
    c, p = _make_full_setup(db)
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is True
    assert "all_gates_passed" in rec.reasons


def test_recommend_false_no_pr(db):
    """No PR → recommend=False."""
    c, p = _make_full_setup(db, has_pr=False)
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is False
    assert any("no_pr" in r for r in rec.reasons)


def test_recommend_false_ci_failed(db):
    """CI not passed → recommend=False."""
    c, p = _make_full_setup(db, ci_passed=False)
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is False
    assert any("ci_not_passed" in r for r in rec.reasons)


def test_recommend_false_tier_not_0(db):
    """Patch tier != 0 → recommend=False."""
    c, p = _make_full_setup(db, tier=1)
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is False
    assert any("tier" in r for r in rec.reasons)


def test_recommend_false_not_applied(db):
    """Candidate not applied → recommend=False."""
    c, p = _make_full_setup(db, applied=False)
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is False
    assert any("not_applied" in r for r in rec.reasons)


def test_recommend_false_critical_alert(db):
    """Critical alert after apply → recommend=False."""
    c, p = _make_full_setup(db)
    db.add(OpsAlert(
        severity="critical", source="test", alert_type="test_alert",
        summary="bad", created_at=_now(),  # after apply
    ))
    db.flush()
    rec = compute_merge_recommendation(db, p.id)
    assert rec.recommend is False
    assert any("critical" in r for r in rec.reasons)


# ---------------------------------------------------------------------------
# Merge outcome creation
# ---------------------------------------------------------------------------

def test_merge_creates_outcome(db):
    """Merged promotion → MergeOutcome(pending) created."""
    c, p = _make_full_setup(db)
    p.status = "merged"
    p.merged_at = _now()
    db.flush()

    outcome = create_merge_outcome(db, p.id)
    assert outcome is not None
    assert outcome.evaluation_status == "pending"
    assert outcome.promotion_id == p.id


def test_merge_outcome_dedup(db):
    """Duplicate creation returns existing."""
    c, p = _make_full_setup(db)
    p.status = "merged"
    p.merged_at = _now()
    db.flush()

    o1 = create_merge_outcome(db, p.id)
    o2 = create_merge_outcome(db, p.id)
    assert o1.id == o2.id


# ---------------------------------------------------------------------------
# Merge outcome evaluation
# ---------------------------------------------------------------------------

def test_evaluate_healthy(db):
    """No regressions → healthy."""
    c, p = _make_full_setup(db)
    outcome = MergeOutcome(
        promotion_id=p.id, bugfix_candidate_id=c.id,
        created_at=_now() - timedelta(minutes=_MIN_EVAL_DELAY_MINUTES + 5),
        evaluation_status="pending",
    )
    db.add(outcome)
    db.flush()

    summary = evaluate_merge_outcomes(db)
    assert summary["healthy"] >= 1
    db.refresh(outcome)
    assert outcome.evaluation_status == "healthy"


def test_evaluate_regressed_same_bug(db):
    """Same bug reappears → regressed."""
    c, p = _make_full_setup(db)
    outcome = MergeOutcome(
        promotion_id=p.id, bugfix_candidate_id=c.id,
        created_at=_now() - timedelta(minutes=_MIN_EVAL_DELAY_MINUTES + 5),
        evaluation_status="pending",
    )
    db.add(outcome)
    db.flush()

    # Same source bug reappears
    db.add(BugFixCandidate(
        source_type=c.source_type, source_ref=c.source_ref,
        title="Same bug again", status="open",
        created_at=_now(),  # after merge outcome
    ))
    db.flush()

    summary = evaluate_merge_outcomes(db)
    assert summary["regressed"] >= 1
    db.refresh(outcome)
    assert outcome.evaluation_status == "regressed"


def test_evaluate_too_recent_skipped(db):
    """Outcome < 15 min old → not evaluated."""
    c, p = _make_full_setup(db)
    outcome = MergeOutcome(
        promotion_id=p.id, bugfix_candidate_id=c.id,
        created_at=_now(),  # just now
        evaluation_status="pending",
    )
    db.add(outcome)
    db.flush()

    summary = evaluate_merge_outcomes(db)
    assert summary["evaluated"] == 0


# ---------------------------------------------------------------------------
# API exposure
# ---------------------------------------------------------------------------

def test_detail_includes_recommendation(client, db):
    """GET detail includes merge_recommendation."""
    c, p = _make_full_setup(db)
    db.commit()
    resp = client.get(f"/ops/promotions/{p.id}", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "merge_recommendation" in data
    rec = data["merge_recommendation"]
    assert "recommend" in rec
    assert "reasons" in rec


def test_detail_includes_merge_outcome(client, db):
    """GET detail includes merge_outcome when available."""
    c, p = _make_full_setup(db)
    p.status = "merged"
    p.merged_at = _now()
    db.flush()
    create_merge_outcome(db, p.id)
    db.commit()

    resp = client.get(f"/ops/promotions/{p.id}", headers=_op_headers())
    data = resp.json()
    assert "merge_outcome" in data
    assert data["merge_outcome"]["evaluation_status"] == "pending"


# ---------------------------------------------------------------------------
# Context summary
# ---------------------------------------------------------------------------

def test_merge_summary_in_context(db, merchant_a):
    """Orchestrator context includes merge outcomes section."""
    from app.services.orchestrator_context import build_orchestrator_context
    context = build_orchestrator_context(db)
    assert "## Merge Outcomes" in context


# ---------------------------------------------------------------------------
# Slack resilience
# ---------------------------------------------------------------------------

def test_regression_alert_does_not_break_evaluation(db):
    """Slack failure during regression alert does not crash evaluation."""
    from unittest.mock import patch
    c, p = _make_full_setup(db)
    outcome = MergeOutcome(
        promotion_id=p.id, bugfix_candidate_id=c.id,
        created_at=_now() - timedelta(minutes=_MIN_EVAL_DELAY_MINUTES + 5),
        evaluation_status="pending",
    )
    db.add(outcome)
    db.add(BugFixCandidate(
        source_type=c.source_type, source_ref=c.source_ref,
        title="Reappeared", status="open", created_at=_now(),
    ))
    db.flush()

    with patch("app.services.merge_intelligence._notify_regression", side_effect=Exception("slack down")):
        summary = evaluate_merge_outcomes(db)

    assert summary["regressed"] >= 1
