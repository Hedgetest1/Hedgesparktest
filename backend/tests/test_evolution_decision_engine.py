"""
Tests for the autonomous decision + rollback engine.

Safety-critical coverage:
  1. Pure decision classifier matches the documented rules.
  2. Rollback FIRES at high confidence NEITHER.
  3. Rollback DOES NOT fire at low confidence.
  4. Rollback is BLOCKED on TIER_2 files.
  5. Rollback is SKIPPED when no applied_commit_sha / target_file.
  6. Rollback is SKIPPED when proposal already has a rollback_candidate_id.
  7. Auto-rollback candidate is routed through the standard bugfix pipeline
     (source_type='auto_rollback'), NOT executed directly.
  8. Per-cycle + per-day caps prevent cascading reversals.
  9. not_applicable → ignored.
 10. BOTH high-confidence → reinforce; BUSINESS_SUCCESS → extend_carefully.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_decision_engine import (
    decide_action,
    propose_rollback,
    run_decision_cycle,
    _MAX_ROLLBACKS_PER_DAY,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_proposal(
    db, *,
    reason="Improve cart conversion",
    target_file="app/services/orchestrator_llm.py",
    applied_commit_sha="deadbeef1234",
    tech_outcome="effective",
    business_outcome="declined",
    confidence_score=0.85,
    rollback_candidate_id=None,
    decision_status=None,
    business_evidence=None,
) -> EvolutionProposal:
    p = EvolutionProposal(
        proposal_type="architecture",
        risk_level="LEVEL_2",
        reason=reason,
        expected_impact="impact",
        auto_applicable=False,
        status="accepted",
        audit_cycle="9999-M99",
        dedup_key=f"monthly_opus:9999-M99:{reason[:40]}",
        target_file=target_file,
        applied_at=_utcnow() - timedelta(days=40),
        applied_commit_sha=applied_commit_sha,
        outcome_status=tech_outcome,
        business_outcome=business_outcome,
        business_measured_at=_utcnow(),
        business_evidence=business_evidence,
        confidence_score=confidence_score,
        rollback_candidate_id=rollback_candidate_id,
        decision_status=decision_status,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# decide_action — pure classifier
# ---------------------------------------------------------------------------

def test_decide_rollback_on_high_conf_neither():
    assert decide_action("ineffective", "declined", 0.85) == "rollback_proposed"
    assert decide_action("effective", "declined", 0.80) == "rollback_proposed"


def test_decide_observe_on_low_confidence_neither():
    # NEITHER but confidence below 0.70 → observe only, NO rollback
    assert decide_action("ineffective", "declined", 0.5) == "observe"
    assert decide_action("ineffective", "declined", 0.69) == "observe"


def test_decide_reinforce_on_high_conf_both():
    assert decide_action("effective", "improved", 0.8) == "reinforce"


def test_decide_extend_on_high_conf_business_success():
    # tech not effective, biz improved → BUSINESS_SUCCESS → extend_carefully
    assert decide_action("inconclusive", "improved", 0.7) == "extend_carefully"
    assert decide_action(None, "improved", 0.65) == "extend_carefully"


def test_decide_observe_low_conf_both():
    assert decide_action("effective", "improved", 0.4) == "observe"


def test_decide_ignored_when_not_applicable():
    # infra proposal with no revenue link → ignored regardless of confidence
    assert decide_action("effective", "not_applicable", 0.99) == "ignored"


def test_decide_observe_on_tech_success_only():
    assert decide_action("effective", "stable", 0.9) == "observe"
    assert decide_action("effective", None, 0.9) == "observe"


def test_decide_observe_on_noise():
    assert decide_action("inconclusive", "inconclusive", 0.0) == "observe"
    assert decide_action(None, None, 0.0) == "observe"


# ---------------------------------------------------------------------------
# propose_rollback — safety gates
# ---------------------------------------------------------------------------

def test_propose_rollback_creates_bugfix_candidate(db):
    p = _make_proposal(db)
    status, candidate_id, reason = propose_rollback(db, p)
    db.flush()
    assert status == "rollback_proposed"
    assert candidate_id is not None
    assert reason == "ok"

    # The rollback candidate exists and is routed through standard pipeline.
    cand = db.query(BugFixCandidate).filter(BugFixCandidate.id == candidate_id).first()
    assert cand is not None
    assert cand.source_type == "auto_rollback"
    assert cand.source_ref == f"evolution_{p.id}"
    assert cand.status == "open"  # flows through standard triage/tier_check
    assert "Auto-Rollback" in cand.title
    assert p.applied_commit_sha in cand.summary
    # Context JSON is present and references the originating proposal.
    import json as _json
    ctx = _json.loads(cand.context_json)
    assert ctx["proposal_id"] == p.id
    assert ctx["reason"] == "auto_rollback_on_measured_decline"
    assert ctx["applied_commit_sha"] == p.applied_commit_sha


def test_propose_rollback_blocks_tier_2_files(db):
    # TIER_2 protected: .env, ecosystem.config.js, app/core/deps.py, etc.
    # No commit blast radius available for this synthetic SHA, so the
    # safety check falls back to target_file and blocks on .env.
    from unittest.mock import patch as _patch
    with _patch(
        "app.services.evolution_decision_engine.extract_commit_files", return_value=[],
    ):
        p = _make_proposal(db, target_file=".env")
        status, candidate_id, reason = propose_rollback(db, p)
    assert status == "rollback_blocked"
    assert candidate_id is None
    assert reason.startswith("blast_radius_contains_tier2")


def test_propose_rollback_skips_without_commit_sha(db):
    p = _make_proposal(db, applied_commit_sha=None)
    status, candidate_id, reason = propose_rollback(db, p)
    assert status == "rollback_skipped"
    assert candidate_id is None
    assert reason == "no_applied_commit_sha"


def test_propose_rollback_skips_without_target_file(db):
    p = _make_proposal(db, target_file=None)
    status, candidate_id, reason = propose_rollback(db, p)
    assert status == "rollback_skipped"
    assert candidate_id is None
    assert reason == "no_target_file"


def test_propose_rollback_refuses_double_rollback(db):
    p = _make_proposal(db, rollback_candidate_id=12345)
    status, candidate_id, reason = propose_rollback(db, p)
    assert status == "rollback_blocked"
    assert candidate_id is None
    assert reason == "already_rolled_back"


def test_propose_rollback_enforces_daily_cap(db):
    p1 = _make_proposal(db, reason="p1")
    p2 = _make_proposal(db, reason="p2")
    with patch(
        "app.services.evolution_decision_engine._daily_rollback_count",
        return_value=_MAX_ROLLBACKS_PER_DAY,
    ):
        status, candidate_id, reason = propose_rollback(db, p1)
        assert status == "rollback_blocked"
        assert reason == "daily_rollback_cap_reached"
        assert candidate_id is None


# ---------------------------------------------------------------------------
# run_decision_cycle — batch orchestration + per-cycle cap
# ---------------------------------------------------------------------------

def test_run_cycle_classifies_each_proposal(db):
    # Prevent side-effect write into Redis counter during test
    with patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count",
        return_value=0,
    ):
        _make_proposal(db, reason="r1", tech_outcome="effective",
                       business_outcome="improved", confidence_score=0.85)
        _make_proposal(db, reason="r2", tech_outcome=None,
                       business_outcome="improved", confidence_score=0.8)
        _make_proposal(db, reason="r3", tech_outcome="effective",
                       business_outcome="not_applicable", confidence_score=0.9)
        _make_proposal(db, reason="r4", tech_outcome="inconclusive",
                       business_outcome="stable", confidence_score=0.1)
        summary = run_decision_cycle(db)

    assert summary["scanned"] >= 4
    assert summary["reinforce"] >= 1
    assert summary["extend_carefully"] >= 1
    assert summary["ignored"] >= 1
    assert summary["observe"] >= 1


def test_run_cycle_caps_rollbacks_per_cycle(db):
    """Only one rollback per cycle even if multiple proposals qualify."""
    with patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count",
        return_value=0,
    ):
        _make_proposal(db, reason="bad1", tech_outcome="ineffective",
                       business_outcome="declined", confidence_score=0.9,
                       target_file="app/services/orchestrator_llm.py",
                       applied_commit_sha="aaa1111111")
        _make_proposal(db, reason="bad2", tech_outcome="ineffective",
                       business_outcome="declined", confidence_score=0.9,
                       target_file="app/services/telegram_agent.py",
                       applied_commit_sha="bbb2222222")
        summary = run_decision_cycle(db)

    # Exactly one rollback proposed this cycle; the other deferred (still NULL).
    assert summary["rollback_proposed"] == 1


def test_run_cycle_writes_decision_status(db):
    p = _make_proposal(
        db, tech_outcome="effective", business_outcome="not_applicable",
        confidence_score=0.95,
    )
    run_decision_cycle(db)
    db.refresh(p)
    assert p.decision_status == "ignored"
    assert p.decision_decided_at is not None


def test_run_cycle_skips_already_decided(db):
    p = _make_proposal(
        db, tech_outcome="effective", business_outcome="improved",
        confidence_score=0.9, decision_status="reinforce",
    )
    summary = run_decision_cycle(db)
    # Our already-decided proposal must not be touched; reinforce count
    # comes only from NEW scans. Prop's status unchanged.
    db.refresh(p)
    assert p.decision_status == "reinforce"
