"""Tests for evolution proposal garbage collector."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text

from app.models.evolution_proposal import EvolutionProposal
from app.models.bugfix_candidate import BugFixCandidate
from app.models.autofix_promotion import AutoFixPromotion
from app.services.evolution_gc import run_evolution_gc, should_run_gc, mark_gc_run


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ago(days=0, hours=0):
    return _now() - timedelta(days=days, hours=hours)


# Mock subprocess.run to return no commits by default (safe baseline)
_NO_COMMITS = MagicMock(returncode=0, stdout="")


@pytest.fixture(autouse=True)
def _isolate(db):
    """Mark all existing open proposals as rejected so tests are isolated."""
    db.execute(text(
        "UPDATE evolution_proposals SET status = 'rejected' WHERE status = 'open'"
    ))
    db.flush()


@pytest.fixture(autouse=True)
def _mock_git():
    """Mock subprocess.run globally — no git calls in tests by default."""
    with patch("app.services.evolution_gc.subprocess") as mock_sp:
        mock_sp.run.return_value = _NO_COMMITS
        mock_sp.TimeoutExpired = TimeoutError
        yield mock_sp


def _make_proposal(db, *, target_file="app/services/audit.py", dedup_key=None,
                   reason="Test proposal", created_at=None, status="open"):
    p = EvolutionProposal(
        proposal_type="reliability",
        target_file=target_file,
        risk_level="LEVEL_2",
        reason=reason,
        expected_impact="Test impact",
        auto_applicable=False,
        status=status,
        audit_cycle="2026-W13",
        dedup_key=dedup_key,
        created_at=created_at or _now(),
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Rule 1: Duplicate collapse
# ---------------------------------------------------------------------------

def test_duplicate_proposals_collapsed(db):
    """Multiple open proposals with same dedup_key → keep newest, mark older obsolete."""
    old = _make_proposal(db, dedup_key="dup:foo", created_at=_ago(days=10))
    mid = _make_proposal(db, dedup_key="dup:foo", created_at=_ago(days=5))
    new = _make_proposal(db, dedup_key="dup:foo", created_at=_ago(days=1))

    summary = run_evolution_gc(db)

    db.refresh(old)
    db.refresh(mid)
    db.refresh(new)

    assert old.status == "obsolete"
    assert mid.status == "obsolete"
    assert new.status == "open"  # newest kept
    assert summary["obsolete"] >= 2


def test_single_proposal_not_collapsed(db):
    """Single proposal with a dedup_key is not touched."""
    p = _make_proposal(db, dedup_key="unique:bar")
    summary = run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "open"


def test_different_dedup_keys_independent(db):
    """Proposals with different dedup_keys are not collapsed together."""
    a = _make_proposal(db, dedup_key="key_a")
    b = _make_proposal(db, dedup_key="key_b")

    run_evolution_gc(db)

    db.refresh(a)
    db.refresh(b)
    assert a.status == "open"
    assert b.status == "open"


# ---------------------------------------------------------------------------
# Rule 2: Merged fix coverage → resolved_indirectly
# ---------------------------------------------------------------------------

def test_merged_fix_marks_resolved_indirectly(db):
    """Proposal touching a file that had a merged bugfix → resolved_indirectly."""
    p = _make_proposal(db, target_file="app/services/bar.py", created_at=_ago(days=5))

    # Create a merged bugfix + promotion that touched the same file
    candidate = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_1",
        title="Fix bar.py", status="applied",
        patch_files=json.dumps(["app/services/bar.py"]),
    )
    db.add(candidate)
    db.flush()

    promo = AutoFixPromotion(
        bugfix_candidate_id=candidate.id,
        git_commit_sha="abc123",
        status="merged",
        merged_at=_ago(days=2),  # merged AFTER proposal creation
    )
    db.add(promo)
    db.flush()

    summary = run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "resolved_indirectly"
    assert "bar.py" in p.gc_reason
    assert summary["resolved_indirectly"] >= 1


def test_merge_before_proposal_not_resolved(db):
    """Merged fix from BEFORE the proposal was created does not mark it resolved."""
    p = _make_proposal(db, target_file="app/services/evolution_engine.py", created_at=_ago(days=2))

    candidate = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_2",
        title="Old fix", status="applied",
        patch_files=json.dumps(["app/services/baz.py"]),
    )
    db.add(candidate)
    db.flush()

    promo = AutoFixPromotion(
        bugfix_candidate_id=candidate.id,
        git_commit_sha="def456",
        status="merged",
        merged_at=_ago(days=10),  # merged BEFORE proposal creation
    )
    db.add(promo)
    db.flush()

    run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "open"


# ---------------------------------------------------------------------------
# Rule 3: Target file changes → needs_revalidation
# ---------------------------------------------------------------------------

def test_target_file_changed_marks_needs_revalidation(db, _mock_git):
    """Proposal for file with recent commits → needs_revalidation."""
    p = _make_proposal(db, target_file="app/services/evolution_gc.py", created_at=_ago(days=5))

    # Override mock for this test: return commits
    _mock_git.run.return_value = MagicMock(returncode=0, stdout="abc1234 some commit\ndef5678 another\n")

    summary = run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "needs_revalidation"
    assert "2 commit(s)" in p.gc_reason
    assert summary["needs_revalidation"] >= 1


def test_target_file_deleted_marks_needs_revalidation(db):
    """Proposal for file that no longer exists → needs_revalidation."""
    p = _make_proposal(db, target_file="app/services/nonexistent_service_xyz.py", created_at=_ago(days=5))

    summary = run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "needs_revalidation"
    assert "no longer exists" in p.gc_reason


# ---------------------------------------------------------------------------
# Rule 4: Accepted siblings
# ---------------------------------------------------------------------------

def test_accepted_sibling_marks_obsolete(db):
    """Open proposal with same dedup_key as an accepted proposal → obsolete."""
    _make_proposal(db, dedup_key="sib:test", status="accepted")
    open_p = _make_proposal(db, dedup_key="sib:test")

    summary = run_evolution_gc(db)

    db.refresh(open_p)
    assert open_p.status == "obsolete"
    assert "already accepted" in open_p.gc_reason


# ---------------------------------------------------------------------------
# Rule 5: Stale proposals
# ---------------------------------------------------------------------------

def test_stale_proposal_marks_needs_revalidation(db):
    """Open proposal older than max_age_days → needs_revalidation."""
    p = _make_proposal(db, created_at=_ago(days=100))

    summary = run_evolution_gc(db, max_age_days=90)

    db.refresh(p)
    assert p.status == "needs_revalidation"
    assert "days old" in p.gc_reason


def test_recent_proposal_not_stale(db):
    """Open proposal within max_age_days stays open."""
    p = _make_proposal(db, created_at=_ago(days=30))

    run_evolution_gc(db, max_age_days=90)

    db.refresh(p)
    assert p.status == "open"


# ---------------------------------------------------------------------------
# No-change scenario
# ---------------------------------------------------------------------------

def test_open_proposal_no_changes_stays_open(db):
    """Open proposal with no relevant changes remains open."""
    p = _make_proposal(db, target_file="app/services/evolution_gc.py",
                       dedup_key="unique:gc_test", created_at=_ago(days=5))

    summary = run_evolution_gc(db, max_age_days=90)

    db.refresh(p)
    assert p.status == "open"
    assert p.gc_reason is None
    assert summary["obsolete"] == 0
    assert summary["resolved_indirectly"] == 0
    assert summary["needs_revalidation"] == 0


# ---------------------------------------------------------------------------
# No hard deletes
# ---------------------------------------------------------------------------

def test_no_hard_deletes(db):
    """GC never deletes proposals — all remain in the database."""
    proposals = []
    for i in range(5):
        proposals.append(_make_proposal(db, dedup_key=f"hd:{i % 2}", created_at=_ago(days=10 - i)))

    before_count = db.query(EvolutionProposal).count()

    run_evolution_gc(db)

    after_count = db.query(EvolutionProposal).count()
    assert after_count == before_count  # no deletions


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_written_on_transition(db):
    """Every GC status transition writes an audit_log entry."""
    _make_proposal(db, dedup_key="audit:test", created_at=_ago(days=10))
    _make_proposal(db, dedup_key="audit:test", created_at=_ago(days=1))

    run_evolution_gc(db)

    audit = db.execute(text(
        "SELECT action_type, actor_name, target_type FROM audit_log "
        "WHERE action_type = 'evolution_gc_transition' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "evolution_gc"
    assert audit[2] == "evolution_proposal"


def test_gc_reason_and_timestamp_set(db):
    """Transitioned proposals have gc_reason and gc_updated_at populated."""
    old = _make_proposal(db, dedup_key="meta:test", created_at=_ago(days=10))
    _make_proposal(db, dedup_key="meta:test", created_at=_ago(days=1))

    run_evolution_gc(db)

    db.refresh(old)
    assert old.gc_reason is not None
    assert old.gc_updated_at is not None
    assert old.decided_by == "evolution_gc"


# ---------------------------------------------------------------------------
# Worker cooldown
# ---------------------------------------------------------------------------

def test_worker_cooldown_respected():
    """should_run_gc returns False within cooldown period."""
    import app.services.evolution_gc as gc_mod

    # Reset state
    gc_mod._last_gc_run = None
    assert gc_mod.should_run_gc() is True

    gc_mod.mark_gc_run()
    assert gc_mod.should_run_gc() is False

    # Reset for other tests
    gc_mod._last_gc_run = None


# ---------------------------------------------------------------------------
# Rule priority / no double-transition
# ---------------------------------------------------------------------------

def test_proposal_transitioned_once(db, _mock_git):
    """A proposal is only transitioned by the first matching rule, not multiple."""
    # This proposal matches rule 1 (duplicate) AND would match rule 3 (file changes)
    old = _make_proposal(db, dedup_key="prio:test", created_at=_ago(days=10),
                         target_file="app/services/evolution_gc.py")
    new = _make_proposal(db, dedup_key="prio:test", created_at=_ago(days=1),
                         target_file="app/services/evolution_gc.py")

    # Return commits so rule 3 would fire for the survivor
    _mock_git.run.return_value = MagicMock(returncode=0, stdout="abc123 commit\n")

    run_evolution_gc(db)

    db.refresh(old)
    # Should be obsolete from rule 1 (duplicate), not needs_revalidation from rule 3
    assert old.status == "obsolete"
    assert "Duplicate" in old.gc_reason

    db.refresh(new)
    # Newest duplicate stays open but gets file-change revalidation
    assert new.status == "needs_revalidation"


# ---------------------------------------------------------------------------
# Proposals with no target_file
# ---------------------------------------------------------------------------

def test_proposal_without_target_file_skipped_by_file_rules(db):
    """Proposals with no target_file are not affected by file-based rules."""
    p = _make_proposal(db, target_file=None, dedup_key="no_file:test", created_at=_ago(days=5))

    run_evolution_gc(db, max_age_days=90)

    db.refresh(p)
    assert p.status == "open"


# ---------------------------------------------------------------------------
# Non-open proposals untouched
# ---------------------------------------------------------------------------

def test_rejected_proposal_not_touched(db):
    """GC only processes open proposals — rejected ones are untouched."""
    p = _make_proposal(db, status="rejected", dedup_key="rej:test")

    run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "rejected"
    assert p.gc_reason is None


# ---------------------------------------------------------------------------
# Target file with line number suffix
# ---------------------------------------------------------------------------

def test_target_file_with_line_number(db):
    """Target file like 'app/services/foo.py:42' is normalized to 'app/services/foo.py'."""
    p = _make_proposal(db, target_file="app/services/bar.py:42", created_at=_ago(days=5))

    # Create a merged fix for bar.py (without line number)
    candidate = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_ln",
        title="Fix bar.py", status="applied",
        patch_files=json.dumps(["app/services/bar.py"]),
    )
    db.add(candidate)
    db.flush()

    promo = AutoFixPromotion(
        bugfix_candidate_id=candidate.id,
        git_commit_sha="ln1234",
        status="merged",
        merged_at=_ago(days=2),
    )
    db.add(promo)
    db.flush()

    run_evolution_gc(db)

    db.refresh(p)
    assert p.status == "resolved_indirectly"
