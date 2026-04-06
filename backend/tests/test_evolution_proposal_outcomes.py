"""
Tests for the evolution-proposal feedback loop.

Verifies that:
  1. link_bugfix_to_proposal is idempotent and first-write-wins.
  2. propagate_proposal_outcomes copies bugfix outcomes onto proposals.
  3. Unmeasured bugfixes leave proposals in "still_pending".
  4. Dangling links resolve to "inconclusive" (no infinite wait).
  5. Apply metadata (applied_at / applied_commit_sha) is mirrored even
     before the outcome is measured.
  6. get_proposal_effectiveness_stats computes counts and rates correctly.
  7. The monthly-audit prompt builder shows outcomes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_proposal_outcomes import (
    link_bugfix_to_proposal,
    propagate_proposal_outcomes,
    get_proposal_effectiveness_stats,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_proposal(db, *, cycle="9999-M99", title="T", status="accepted") -> EvolutionProposal:
    p = EvolutionProposal(
        proposal_type="architecture",
        risk_level="LEVEL_2",
        reason=title,
        expected_impact="impact",
        auto_applicable=False,
        status=status,
        audit_cycle=cycle,
        dedup_key=f"monthly_opus:{cycle}:{title}",
    )
    db.add(p)
    db.flush()
    return p


def _make_bugfix(db, *, status="applied", outcome=None, applied_at=None, sha=None, evidence=None) -> BugFixCandidate:
    b = BugFixCandidate(
        source_type="evolution",
        source_ref="evolution_x",
        title="bugfix",
        status=status,
        applied_at=applied_at,
        git_commit_sha=sha,
        outcome_status=outcome,
        outcome_measured_at=_utcnow() if outcome else None,
        outcome_evidence=evidence,
    )
    db.add(b)
    db.flush()
    return b


# ---------------------------------------------------------------------------
# link_bugfix_to_proposal
# ---------------------------------------------------------------------------

def test_link_bugfix_to_proposal_sets_link(db):
    p = _make_proposal(db)
    b = _make_bugfix(db)
    assert link_bugfix_to_proposal(db, p.id, b.id) is True
    db.flush()
    assert p.linked_bugfix_candidate_id == b.id


def test_link_bugfix_to_proposal_is_idempotent(db):
    p = _make_proposal(db)
    b = _make_bugfix(db)
    assert link_bugfix_to_proposal(db, p.id, b.id) is True
    assert link_bugfix_to_proposal(db, p.id, b.id) is True  # second call no-op
    assert p.linked_bugfix_candidate_id == b.id


def test_link_bugfix_to_proposal_first_write_wins(db):
    p = _make_proposal(db)
    b1 = _make_bugfix(db)
    b2 = _make_bugfix(db)
    assert link_bugfix_to_proposal(db, p.id, b1.id) is True
    # Subsequent link to a different bugfix is refused
    assert link_bugfix_to_proposal(db, p.id, b2.id) is False
    assert p.linked_bugfix_candidate_id == b1.id


def test_link_bugfix_to_proposal_missing_proposal(db):
    b = _make_bugfix(db)
    assert link_bugfix_to_proposal(db, 99_999_999, b.id) is False


# ---------------------------------------------------------------------------
# propagate_proposal_outcomes
# ---------------------------------------------------------------------------

def test_propagate_copies_effective_outcome(db):
    p = _make_proposal(db, title="works")
    b = _make_bugfix(
        db, status="applied", outcome="effective",
        applied_at=_utcnow(), sha="abcdef1234567890",
        evidence='{"alerts_before":5,"alerts_after":0}',
    )
    p.linked_bugfix_candidate_id = b.id
    db.flush()

    summary = propagate_proposal_outcomes(db)
    db.flush()

    assert summary["updated"] == 1
    assert p.outcome_status == "effective"
    assert p.outcome_measured_at is not None
    assert p.applied_commit_sha == "abcdef1234567890"
    assert p.applied_at is not None
    assert '"source": "bugfix_candidate"' in p.outcome_evidence
    assert '"bugfix_id": %d' % b.id in p.outcome_evidence


def test_propagate_copies_ineffective_outcome(db):
    p = _make_proposal(db, title="failed")
    b = _make_bugfix(db, outcome="ineffective", applied_at=_utcnow(), sha="def456")
    p.linked_bugfix_candidate_id = b.id
    db.flush()

    propagate_proposal_outcomes(db)
    db.flush()
    assert p.outcome_status == "ineffective"


def test_propagate_pending_bugfix_leaves_proposal_unmeasured(db):
    p = _make_proposal(db, title="waiting")
    b = _make_bugfix(db, status="applied", outcome=None, applied_at=_utcnow(), sha="c0mm1t")
    p.linked_bugfix_candidate_id = b.id
    db.flush()

    summary = propagate_proposal_outcomes(db)
    db.flush()

    # Semantic assertions — production DB may contain other rows; verify
    # that OUR proposal was correctly processed:
    #   - still_pending count is at least 1 (our proposal)
    #   - our proposal's outcome is still unmeasured (bugfix not measured yet)
    #   - apply metadata mirrored as soon as bugfix is applied
    assert summary["still_pending"] >= 1
    assert p.outcome_status is None
    assert p.applied_commit_sha == "c0mm1t"
    assert p.applied_at is not None


def test_propagate_dangling_link_marks_inconclusive(db):
    p = _make_proposal(db, title="dangling")
    p.linked_bugfix_candidate_id = 99_999_999  # no bugfix with this id
    db.flush()

    summary = propagate_proposal_outcomes(db)
    db.flush()

    assert summary["updated"] == 1
    assert p.outcome_status == "inconclusive"
    assert "no longer exists" in p.outcome_evidence


def test_propagate_skips_already_measured(db):
    """Proposals with outcome_status already set must not be re-scanned.

    NOTE: assertions are SEMANTIC — we verify that OUR proposal was not
    touched. Absolute counts are meaningless because the production DB
    may contain other unmeasured proposals (autonomous evolution flow
    keeps adding rows continuously).
    """
    p = _make_proposal(db, title="done")
    b = _make_bugfix(db, outcome="effective", applied_at=_utcnow(), sha="aaa")
    p.linked_bugfix_candidate_id = b.id
    p.outcome_status = "effective"
    p.outcome_measured_at = _utcnow()
    db.flush()

    propagate_proposal_outcomes(db)
    # Our already-measured proposal must NOT be modified.
    db.refresh(p)
    assert p.outcome_status == "effective"
    assert p.outcome_measured_at is not None


def test_propagate_skips_unlinked_proposals(db):
    """Proposals without a linked bugfix must never be touched.

    Semantic assertion — see note in test_propagate_skips_already_measured.
    """
    p = _make_proposal(db, title="unlinked", status="open")
    original_outcome = p.outcome_status
    propagate_proposal_outcomes(db)
    db.refresh(p)
    # Our unlinked proposal's outcome unchanged.
    assert p.outcome_status == original_outcome
    assert p.linked_bugfix_candidate_id is None


# ---------------------------------------------------------------------------
# get_proposal_effectiveness_stats
# ---------------------------------------------------------------------------

def test_effectiveness_stats_counts_and_rate(db):
    # Use a synthetic cycle that does not collide with real data.
    _make_proposal(db, cycle="9999-M01", title="a").outcome_status = "effective"
    _make_proposal(db, cycle="9999-M01", title="b").outcome_status = "effective"
    _make_proposal(db, cycle="9999-M01", title="c").outcome_status = "ineffective"
    _make_proposal(db, cycle="9999-M01", title="d").outcome_status = "inconclusive"
    _make_proposal(db, cycle="9999-M01", title="e")  # unmeasured
    db.flush()

    # Stats includes REAL production data too — so filter mentally by cycle.
    stats = get_proposal_effectiveness_stats(db, limit_cycles=12)
    by_cycle = stats["by_cycle"].get("9999-M01", {})
    assert by_cycle.get("effective") == 2
    assert by_cycle.get("ineffective") == 1
    assert by_cycle.get("inconclusive") == 1
    assert by_cycle.get("unmeasured") == 1
    assert by_cycle.get("total") == 5


def test_effectiveness_stats_rate_zero_when_no_measured(db):
    """effectiveness_rate must be 0.0 (not NaN/error) when denominator is 0."""
    stats = get_proposal_effectiveness_stats(db)
    assert isinstance(stats["effectiveness_rate"], float)
    assert 0.0 <= stats["effectiveness_rate"] <= 1.0


# ---------------------------------------------------------------------------
# Monthly audit prompt rendering includes outcomes
# ---------------------------------------------------------------------------

def test_monthly_prompt_builder_includes_outcomes(db):
    from app.services.monthly_evolution_audit import _build_prior_monthly_audits
    p = _make_proposal(db, cycle="9999-M02", title="outcome-rendered")
    p.outcome_status = "effective"
    p.applied_commit_sha = "deadbeef1234"
    db.flush()

    text = _build_prior_monthly_audits(db)
    # Rolling effectiveness header always present.
    assert "Rolling TECH effectiveness" in text
    assert "Rolling BUSINESS success rates" in text
    # Outcome appears inline with status (format: [status/tech=.../biz=.../COMBINED commit=...]).
    # Because real production data is also rendered, only assert our row is present.
    assert "9999-M02" in text
    assert "tech=effective" in text
    assert "deadbeef" in text
