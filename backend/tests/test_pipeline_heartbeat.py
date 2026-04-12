"""Tests for A1 — synthetic pipeline heartbeat.

Locks the contract: every fire creates a synthetic alert, runs the
real triage, expects a candidate, cleans up, and writes a heartbeat
outcome alert. Killswitch + cooldown enforced.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.database import SessionLocal
from app.services import pipeline_heartbeat as hb
from app.models.bugfix_candidate import BugFixCandidate
from app.models.ops_alert import OpsAlert


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _clear_cooldown():
    rc = hb._redis()
    if rc is not None:
        try:
            rc.delete(hb._REDIS_LAST_RUN_KEY)
        except Exception:
            pass


def test_kill_switch_blocks(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_PAUSED", "1")
    db = MagicMock()
    result = hb.run_heartbeat(db)
    assert result["status"] == "paused"


def test_cooldown_blocks_consecutive_runs(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_PAUSED", raising=False)
    _clear_cooldown()
    with patch.object(hb, "_is_on_cooldown", return_value=True):
        result = hb.run_heartbeat(MagicMock())
    assert result["status"] == "cooldown"


def test_full_happy_path_against_real_db(db):
    """End-to-end against the real DB. The candidate must be created
    and then cleaned up — leave the database exactly as we found it."""
    _clear_cooldown()

    candidates_before = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "ops_alert_generic",
        BugFixCandidate.status != "discarded",
    ).count()

    result = hb.run_heartbeat(db)

    assert result["status"] == "ok", f"unexpected: {result}"
    assert result["candidate_id"] is not None
    assert "alert_write" in result["phases"]
    assert "triage_run" in result["phases"]
    assert "candidate_lookup" in result["phases"]
    assert "cleanup" in result["phases"]
    assert result["total_s"] > 0
    assert result["total_s"] < 30  # not slow

    # The candidate must be marked discarded so the LLM never sees it
    cand = db.query(BugFixCandidate).filter_by(id=result["candidate_id"]).first()
    assert cand is not None
    assert cand.status == "discarded"
    assert "heartbeat_synthetic_cleanup" in (cand.failure_reason or "")

    # Net new active candidates: zero
    candidates_after = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "ops_alert_generic",
        BugFixCandidate.status != "discarded",
    ).count()
    assert candidates_after == candidates_before

    # The synthetic ops_alerts must be resolved
    synthetic_open = db.query(OpsAlert).filter(
        OpsAlert.alert_type == hb._HEARTBEAT_ALERT_TYPE,
        OpsAlert.source.like(f"{hb._HEARTBEAT_SOURCE_PREFIX}:{result['run_id']}"),
        OpsAlert.resolved == False,  # noqa: E712
    ).count()
    assert synthetic_open == 0

    # An outcome alert must have been written
    outcome = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "heartbeat_ok",
    ).order_by(OpsAlert.created_at.desc()).first()
    assert outcome is not None
    assert "ok" in (outcome.summary or "").lower()


def test_triage_failure_records_failed_outcome(db, monkeypatch):
    """If run_bug_triage explodes, the heartbeat must clean up and
    write a heartbeat_failed alert."""
    _clear_cooldown()
    monkeypatch.delenv("HEARTBEAT_PAUSED", raising=False)

    def _boom(*a, **k):
        raise RuntimeError("synthetic triage explosion")

    with patch("app.services.bugfix_pipeline.run_bug_triage", side_effect=_boom):
        result = hb.run_heartbeat(db)

    assert result["status"] == "triage_run_failed"
    assert "synthetic triage explosion" in result["error"]

    failed = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "heartbeat_failed",
    ).order_by(OpsAlert.created_at.desc()).first()
    assert failed is not None


def test_candidate_not_created_records_failure(db, monkeypatch):
    """If triage runs but doesn't produce the synthetic candidate
    (regression in Rule 7), the heartbeat must surface it."""
    _clear_cooldown()
    monkeypatch.delenv("HEARTBEAT_PAUSED", raising=False)

    fake_summary = {"created": 0, "scanned": 0, "deduped": 0, "suppressed": 0}
    with patch("app.services.bugfix_pipeline.run_bug_triage", return_value=fake_summary):
        result = hb.run_heartbeat(db)

    assert result["status"] == "candidate_not_created"

    failed = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "heartbeat_failed",
    ).order_by(OpsAlert.created_at.desc()).first()
    assert failed is not None
