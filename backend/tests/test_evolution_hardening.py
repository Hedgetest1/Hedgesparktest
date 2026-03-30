"""Tests for evolution backlog hardening — dedup awareness, revalidation, visibility."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.evolution_proposal import (
    EvolutionProposal, ENGINE_DEDUP_STATUSES, GC_STATUSES,
)
from app.services.evolution_engine import run_evolution_audit


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ago(days=0):
    return _now() - timedelta(days=days)


@pytest.fixture(autouse=True)
def _isolate(db):
    """Neutralize existing proposals that would block dedup in engine tests."""
    db.execute(text(
        "UPDATE evolution_proposals SET status = 'expired'"
        " WHERE status IN ('open', 'accepted', 'needs_revalidation')"
    ))
    db.flush()


def _make_proposal(db, *, dedup_key, status="open", target_file="app/services/audit.py",
                   proposal_type="reliability", reason="Test", gc_reason=None):
    p = EvolutionProposal(
        proposal_type=proposal_type,
        target_file=target_file,
        risk_level="LEVEL_2",
        reason=reason,
        expected_impact="Test",
        auto_applicable=False,
        status=status,
        audit_cycle="2026-W13",
        dedup_key=dedup_key,
        created_at=_now(),
        gc_reason=gc_reason,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Phase 1: Dedup awareness — needs_revalidation blocks recreation
# ---------------------------------------------------------------------------

def test_needs_revalidation_blocks_recreation(db):
    """Engine must NOT recreate a proposal whose dedup_key is in needs_revalidation."""
    # Manually create a needs_revalidation proposal with a known dedup_key
    # that one of the scanners would produce
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="needs_revalidation",
        target_file="app/services/evolution_gc.py",
        gc_reason="Target file changed",
    )

    summary = run_evolution_audit(db)

    # The scanner would find this missing test again, but dedup should block it
    recreated = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert recreated == 0, "needs_revalidation proposal was recreated — dedup failed"


def test_open_blocks_recreation(db):
    """Engine must NOT recreate a proposal whose dedup_key is already open."""
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="open",
    )

    summary = run_evolution_audit(db)

    count = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert count == 1, "Open proposal was duplicated"


def test_accepted_blocks_recreation(db):
    """Engine must NOT recreate a proposal whose dedup_key is accepted."""
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="accepted",
    )

    summary = run_evolution_audit(db)

    recreated = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert recreated == 0


def test_obsolete_allows_recreation(db):
    """Engine CAN recreate a proposal whose dedup_key was marked obsolete."""
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="obsolete",
        gc_reason="Duplicate",
    )

    summary = run_evolution_audit(db)

    # Scanner should find the same issue and create a new open proposal
    recreated = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert recreated == 1, "Obsolete proposal should allow recreation"


def test_rejected_allows_recreation(db):
    """Engine CAN recreate a proposal whose dedup_key was rejected."""
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="rejected",
    )

    summary = run_evolution_audit(db)

    recreated = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert recreated == 1, "Rejected proposal should allow recreation"


def test_resolved_indirectly_allows_recreation(db):
    """Engine CAN recreate a proposal marked resolved_indirectly."""
    _make_proposal(
        db,
        dedup_key="missing_test:action_executor.py",
        status="resolved_indirectly",
        gc_reason="Merged fix covered it",
    )

    summary = run_evolution_audit(db)

    recreated = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key == "missing_test:action_executor.py",
        EvolutionProposal.status == "open",
    ).count()
    assert recreated == 1, "resolved_indirectly proposal should allow recreation"


# ---------------------------------------------------------------------------
# Phase 1: ENGINE_DEDUP_STATUSES constant is correct
# ---------------------------------------------------------------------------

def test_engine_dedup_statuses_is_correct():
    """ENGINE_DEDUP_STATUSES must contain exactly open, accepted, needs_revalidation."""
    assert ENGINE_DEDUP_STATUSES == {"open", "accepted", "needs_revalidation"}


# ---------------------------------------------------------------------------
# Phase 2: Revalidation flow
# ---------------------------------------------------------------------------

def test_revalidation_returns_to_open(db, client):
    """POST /ops/evolution/{id}/revalidate moves a GC'd proposal back to open."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(
        db,
        dedup_key="reval:test",
        status="needs_revalidation",
        gc_reason="Target changed",
    )
    p.gc_updated_at = _now()
    p.decided_by = "evolution_gc"
    db.flush()

    resp = client.post(
        f"/ops/evolution/{p.id}/revalidate",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["revalidated_from"] == "needs_revalidation"

    db.refresh(p)
    assert p.status == "open"
    assert p.gc_reason is None
    assert p.gc_updated_at is None
    assert p.decided_by == "operator_revalidate"


def test_revalidation_writes_audit_log(db, client):
    """Revalidation writes an audit_log entry."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="reval_audit:test", status="obsolete",
                       gc_reason="Duplicate")

    resp = client.post(
        f"/ops/evolution/{p.id}/revalidate",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    audit = db.execute(text(
        "SELECT action_type, actor_name FROM audit_log "
        "WHERE action_type = 'evolution_revalidate' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "operator"


def test_revalidation_rejects_non_gc_status(db, client):
    """Cannot revalidate a proposal that isn't in a GC status."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="reval_bad:test", status="open")

    resp = client.post(
        f"/ops/evolution/{p.id}/revalidate",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Phase 4: active_for_engine visibility
# ---------------------------------------------------------------------------

def test_active_for_engine_open(db, client):
    """Open proposals show active_for_engine=True."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    _make_proposal(db, dedup_key="vis:open", status="open")

    resp = client.get("/ops/evolution?status=open", headers={"X-API-Key": key})
    assert resp.status_code == 200
    items = [r for r in resp.json() if r["status"] == "open"]
    assert len(items) >= 1
    assert items[0]["active_for_engine"] is True


def test_active_for_engine_needs_revalidation(db, client):
    """needs_revalidation proposals show active_for_engine=True (blocks recreation)."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="vis:nr", status="needs_revalidation",
                       gc_reason="Target changed")

    resp = client.get("/ops/evolution?status=needs_revalidation", headers={"X-API-Key": key})
    assert resp.status_code == 200
    items = resp.json()
    match = [r for r in items if r["id"] == p.id]
    assert len(match) == 1
    assert match[0]["active_for_engine"] is True
    assert match[0]["gc_reason"] == "Target changed"


def test_active_for_engine_obsolete(db, client):
    """Obsolete proposals show active_for_engine=False (can be recreated)."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="vis:obs", status="obsolete",
                       gc_reason="Duplicate")

    resp = client.get("/ops/evolution?status=obsolete", headers={"X-API-Key": key})
    assert resp.status_code == 200
    items = resp.json()
    match = [r for r in items if r["id"] == p.id]
    assert len(match) == 1
    assert match[0]["active_for_engine"] is False


def test_active_for_engine_rejected(db, client):
    """Rejected proposals show active_for_engine=False."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="vis:rej", status="rejected")

    resp = client.get("/ops/evolution?status=rejected", headers={"X-API-Key": key})
    assert resp.status_code == 200
    items = resp.json()
    match = [r for r in items if r["id"] == p.id]
    assert len(match) == 1
    assert match[0]["active_for_engine"] is False


# ---------------------------------------------------------------------------
# Phase 4: API response includes decided_by / decided_at
# ---------------------------------------------------------------------------

def test_api_includes_decided_fields(db, client):
    """GET /ops/evolution response includes decided_by and decided_at."""
    import os
    key = os.environ.get("DASHBOARD_API_KEY", "test-key")
    p = _make_proposal(db, dedup_key="vis:decided", status="open")

    resp = client.get("/ops/evolution?status=open", headers={"X-API-Key": key})
    assert resp.status_code == 200
    items = resp.json()
    match = [r for r in items if r["id"] == p.id]
    assert len(match) == 1
    assert "decided_by" in match[0]
    assert "decided_at" in match[0]
