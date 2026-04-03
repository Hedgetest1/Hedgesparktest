"""Tests for evolution proposal → bugfix candidate conversion."""
import json

import pytest
from sqlalchemy import text

from app.models.evolution_proposal import EvolutionProposal
from app.models.bugfix_candidate import BugFixCandidate
from app.services.evolution_converter import convert_eligible_proposals


@pytest.fixture(autouse=True)
def _clear_existing_proposals(db):
    """Mark all existing open LEVEL_1 proposals as rejected so tests are isolated."""
    db.execute(text(
        "UPDATE evolution_proposals SET status = 'rejected' WHERE status = 'open' AND risk_level = 'LEVEL_1'"
    ))
    db.flush()


def _make_proposal(db, risk="LEVEL_1", auto=True, status="open", reason="Missing test file"):
    p = EvolutionProposal(
        proposal_type="reliability",
        target_file="app/services/test_file.py",
        risk_level=risk,
        reason=reason,
        expected_impact="Improved coverage",
        auto_applicable=auto,
        status=status,
        audit_cycle="2026-W13",
        dedup_key=f"test:{reason[:50]}",
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Eligible conversion
# ---------------------------------------------------------------------------

def test_level1_auto_creates_bugfix(db):
    """LEVEL_1 + auto_applicable + open → creates BugFixCandidate."""
    p = _make_proposal(db)
    summary = convert_eligible_proposals(db)

    assert summary["converted"] >= 1

    candidate = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "evolution",
        BugFixCandidate.source_ref == f"evolution_{p.id}",
    ).first()
    assert candidate is not None
    assert candidate.status == "open"
    assert "[Evolution]" in candidate.title

    # Proposal marked as accepted
    db.refresh(p)
    assert p.status == "accepted"
    assert p.decided_by == "evolution_converter"


def test_candidate_has_traceability(db):
    """Converted candidate's context_json links back to evolution proposal."""
    p = _make_proposal(db)
    convert_eligible_proposals(db)

    candidate = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_ref == f"evolution_{p.id}",
    ).first()
    ctx = json.loads(candidate.context_json)
    assert ctx["evolution_proposal_id"] == p.id
    assert ctx["target_file"] == "app/services/test_file.py"
    assert ctx["audit_cycle"] == "2026-W13"


# ---------------------------------------------------------------------------
# Ineligible proposals
# ---------------------------------------------------------------------------

def test_level2_not_converted(db):
    """LEVEL_2 proposals are never converted."""
    _make_proposal(db, risk="LEVEL_2")
    summary = convert_eligible_proposals(db)
    assert summary["converted"] == 0


def test_level3_not_converted(db):
    """LEVEL_3 proposals are never converted."""
    _make_proposal(db, risk="LEVEL_3")
    summary = convert_eligible_proposals(db)
    assert summary["converted"] == 0


def test_non_auto_applicable_not_converted(db):
    """auto_applicable=False proposals are not converted."""
    _make_proposal(db, auto=False)
    summary = convert_eligible_proposals(db)
    assert summary["converted"] == 0


def test_non_open_not_converted(db):
    """Already-accepted proposals are not re-converted."""
    _make_proposal(db, status="accepted")
    summary = convert_eligible_proposals(db)
    assert summary["converted"] == 0


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def test_duplicate_conversion_blocked(db):
    """Same proposal cannot create two open bugfix candidates."""
    p = _make_proposal(db)
    s1 = convert_eligible_proposals(db)
    assert s1["converted"] >= 1

    # Reset proposal to open (simulate re-scan)
    p.status = "open"
    p.decided_by = None
    db.flush()

    s2 = convert_eligible_proposals(db)
    assert s2["skipped_dedup"] >= 1

    # Only one candidate exists
    count = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_ref == f"evolution_{p.id}",
    ).count()
    assert count == 1


def test_failed_proposals_skipped(db):
    """Proposals with 2+ failed bugfix candidates are skipped."""
    p = _make_proposal(db, reason="Chronically failing fix")
    ref = f"evolution_{p.id}"

    # Create 2 failed candidates
    for i in range(2):
        db.add(BugFixCandidate(
            source_type="evolution", source_ref=ref,
            title=f"Failed attempt {i}", status="apply_failed",
        ))
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["skipped_ineligible"] >= 1


# ---------------------------------------------------------------------------
# Max per cycle
# ---------------------------------------------------------------------------

def test_max_conversions_per_cycle(db):
    """Max 2 conversions per cycle."""
    for i in range(5):
        _make_proposal(db, reason=f"Proposal {i}")

    summary = convert_eligible_proposals(db, max_per_cycle=2)
    assert summary["converted"] == 2


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_conversion_writes_audit_log(db):
    """Conversion creates an audit_log entry."""
    p = _make_proposal(db)
    convert_eligible_proposals(db)

    audit = db.execute(text(
        "SELECT action_type, actor_name FROM audit_log "
        "WHERE action_type = 'evolution_to_bugfix' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "evolution_converter"


# ---------------------------------------------------------------------------
# Pipeline compatibility
# ---------------------------------------------------------------------------

def test_converted_candidate_is_standard_bugfix(db):
    """Converted candidate has all fields needed for bugfix pipeline."""
    p = _make_proposal(db)
    convert_eligible_proposals(db)

    candidate = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_ref == f"evolution_{p.id}",
    ).first()
    assert candidate.source_type == "evolution"
    assert candidate.status == "open"
    assert candidate.title is not None
    assert candidate.summary is not None
    assert candidate.context_json is not None
    # These are set by auto-propose later
    assert candidate.patch_diff is None
    assert candidate.patch_risk_tier is None


# ---------------------------------------------------------------------------
# Execution policy enforcement
# ---------------------------------------------------------------------------

def test_tier2_target_blocked(db):
    """Proposals targeting TIER_2 files are rejected at conversion time."""
    p = _make_proposal(db, reason="Fix token_crypto issue")
    p.target_file = "app/core/token_crypto.py"
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["skipped_ineligible"] >= 1
    assert summary["converted"] == 0


def test_tier2_billing_blocked(db):
    """Proposals targeting billing are rejected at conversion time."""
    p = _make_proposal(db, reason="Fix billing edge case")
    p.target_file = "app/api/billing.py"
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["skipped_ineligible"] >= 1
    assert summary["converted"] == 0


def test_tier2_migration_blocked(db):
    """Proposals targeting migrations are rejected at conversion time."""
    p = _make_proposal(db, reason="Add migration index")
    p.target_file = "migrations/versions/abc_new.py"
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["skipped_ineligible"] >= 1
    assert summary["converted"] == 0


def test_tier0_target_allowed(db):
    """Proposals targeting safe service files are converted normally."""
    p = _make_proposal(db, reason="Fix revenue calc")
    p.target_file = "app/services/revenue_metrics.py"
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["converted"] >= 1


def test_none_target_allowed(db):
    """Proposals without a target_file (e.g., area-level proposals) are not blocked."""
    p = _make_proposal(db, reason="General reliability improvement")
    p.target_file = None
    db.flush()

    summary = convert_eligible_proposals(db)
    assert summary["converted"] >= 1
