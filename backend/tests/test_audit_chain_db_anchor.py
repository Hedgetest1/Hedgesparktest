"""Contract tests for the audit_chain_anchor DB defense.

Born 2026-05-15 (10k-structural sprint TIER_2 fresh approval). Closes
the threat model: attacker with both Redis + DB write access wipes
audit_log + Redis chain-head key and reconstructs a fake chain from
genesis. The DB-side singleton anchor row persists across that attack
and triggers CRITICAL alerts on mismatch / missing-row.

These tests pin:
  1. write_audit_log upserts the DB anchor row on every write.
  2. revision_counter monotonically increments.
  3. verify_audit_log_chain reports `db_anchor_present=True` +
     `head_matches_db_anchor=True` on healthy state.
  4. Wiping audit_log (full table delete) but leaving the anchor row →
     verify reports `head_matches_db_anchor=False` (tampering).
  5. Dropping/deleting the anchor row → `db_anchor_present=False`.
  6. Genesis state (empty audit_log + anchor at genesis hash) → not flagged.
  7. enforce_chain_integrity raises CRITICAL alerts on subclasses.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import audit as audit_module
from app.services.audit import (
    _GENESIS_HASH,
    enforce_chain_integrity,
    get_chain_head_db_anchor,
    verify_audit_log_chain,
    write_audit_log,
)


def _anchor_row(db):
    return db.execute(
        text("SELECT id, chain_head, revision_counter FROM audit_chain_anchor WHERE id = 1")
    ).fetchone()


def _reset_anchor_to_genesis(db) -> None:
    """Drive the anchor back to genesis between tests so each one starts
    clean. Conftest SAVEPOINT covers audit_log rollback, but
    audit_chain_anchor is updated via an INSERT...ON CONFLICT path
    that increments revision_counter — we explicitly reset here."""
    db.execute(
        text(
            "INSERT INTO audit_chain_anchor (id, chain_head, revision_counter, updated_at) "
            "VALUES (1, :gen, 0, now()) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  chain_head = EXCLUDED.chain_head, "
            "  revision_counter = 0, "
            "  updated_at = now()"
        ),
        {"gen": _GENESIS_HASH},
    )
    db.commit()


@pytest.fixture(autouse=True)
def _reset_anchor(db):
    """Reset anchor to genesis BEFORE and AFTER each test in this file."""
    _reset_anchor_to_genesis(db)
    yield
    _reset_anchor_to_genesis(db)


def test_write_audit_log_upserts_db_anchor(db):
    """First audit write updates anchor.chain_head off genesis + revision_counter→1."""
    pre = _anchor_row(db)
    assert pre.chain_head == _GENESIS_HASH
    assert pre.revision_counter == 0

    entry = write_audit_log(
        db,
        actor_type="test",
        actor_name="test_writer",
        action_type="probe",
        target_type="probe",
        target_id="1",
    )
    db.flush()

    post = _anchor_row(db)
    assert post.chain_head != _GENESIS_HASH
    assert post.revision_counter == 1
    # The new anchor equals the row's stored self-hash
    import json
    meta = json.loads(entry.metadata_json)
    assert post.chain_head == meta["_chain"]["self"]


def test_write_audit_log_increments_revision_counter(db):
    """Each subsequent write bumps revision_counter atomically."""
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe1")
    db.flush()
    r1 = _anchor_row(db).revision_counter
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe2")
    db.flush()
    r2 = _anchor_row(db).revision_counter
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe3")
    db.flush()
    r3 = _anchor_row(db).revision_counter
    assert r1 == 1 and r2 == 2 and r3 == 3


def test_verify_audit_log_chain_anchor_present_and_matches(db):
    """Healthy state: db_anchor_present=True, head_matches_db_anchor=True."""
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe")
    db.flush()

    report = verify_audit_log_chain(db)
    assert report["db_anchor_present"] is True
    assert report["head_matches_db_anchor"] is True


def test_genesis_empty_state_does_not_flag_mismatch(db):
    """Empty audit_log + anchor at genesis hash → not tampering, just fresh state."""
    # No audit writes; anchor at genesis from fixture.
    report = verify_audit_log_chain(db)
    assert report["db_anchor_present"] is True
    # head_matches_db_anchor is True only when both align on genesis
    assert report["head_matches_db_anchor"] is True
    assert report["total_rows"] == 0


def test_audit_log_wipe_leaves_anchor_inconsistent(db):
    """Simulate the attack: write rows → wipe audit_log → anchor still holds
    pre-wipe head → verify flags db_anchor_mismatch."""
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe1")
    db.flush()
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe2")
    db.flush()

    pre_wipe_anchor = _anchor_row(db).chain_head
    assert pre_wipe_anchor != _GENESIS_HASH

    # Simulate audit_log wipe (attacker DELETE FROM audit_log)
    from app.models.audit_log import AuditLog
    db.query(AuditLog).delete()
    db.flush()

    report = verify_audit_log_chain(db)
    # audit_log empty but anchor holds the pre-wipe head → mismatch
    assert report["total_rows"] == 0
    assert report["db_anchor_present"] is True
    assert report["head_matches_db_anchor"] is False, (
        f"Tampering signature should fire: anchor={pre_wipe_anchor[:16]} "
        f"vs empty audit_log. Got head_matches_db_anchor="
        f"{report['head_matches_db_anchor']}"
    )


def test_anchor_row_missing_reports_db_anchor_present_false(db):
    """If the anchor row is somehow gone (table dropped or row deleted),
    verify reports db_anchor_present=False — distinct subclass of
    tampering for separate alerting."""
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe")
    db.flush()

    # Simulate row deletion
    db.execute(text("DELETE FROM audit_chain_anchor WHERE id = 1"))
    db.flush()

    report = verify_audit_log_chain(db)
    assert report["db_anchor_present"] is False
    # head_matches_db_anchor stays None when anchor is missing — the
    # absence itself is the alert signal, not a match comparison.
    assert report["head_matches_db_anchor"] is None


def test_get_chain_head_db_anchor_returns_seeded_genesis(db):
    """The migration seeded genesis_hash; reader returns it pre-first-write."""
    assert get_chain_head_db_anchor(db) == _GENESIS_HASH


def test_get_chain_head_db_anchor_returns_latest_after_write(db):
    """After a write, the reader sees the new head."""
    entry = write_audit_log(db, actor_type="t", actor_name="w", action_type="probe")
    db.flush()

    import json
    expected = json.loads(entry.metadata_json)["_chain"]["self"]
    assert get_chain_head_db_anchor(db) == expected


def test_enforce_chain_integrity_alerts_on_db_anchor_mismatch(db):
    """The enforce path emits CRITICAL alert with subclass=db_anchor_mismatch
    when the anchor and computed head disagree."""
    write_audit_log(db, actor_type="t", actor_name="w", action_type="probe1")
    db.flush()

    from app.models.audit_log import AuditLog
    db.query(AuditLog).delete()
    db.flush()

    # Run enforce — expect at least one alert with the new subclass
    captured_alerts: list[dict] = []
    from app.services import alerting

    def _capture(**kwargs):
        captured_alerts.append(kwargs)

    import unittest.mock as _mock
    with _mock.patch.object(alerting, "write_alert", side_effect=_capture):
        result = enforce_chain_integrity(db)

    db_anchor_alerts = [
        a for a in captured_alerts
        if a.get("source", "").startswith("audit_chain_db_anchor:")
    ]
    assert len(db_anchor_alerts) == 1, (
        f"expected 1 db_anchor alert, got {len(db_anchor_alerts)}: "
        f"{[a.get('source') for a in captured_alerts]}"
    )
    assert db_anchor_alerts[0]["severity"] == "critical"
    assert db_anchor_alerts[0]["alert_type"] == "audit_log_tampering"


def test_enforce_chain_integrity_alerts_on_db_anchor_missing(db):
    """When anchor row absent, enforce emits subclass=db_anchor_missing."""
    db.execute(text("DELETE FROM audit_chain_anchor WHERE id = 1"))
    db.flush()

    captured_alerts: list[dict] = []
    from app.services import alerting

    def _capture(**kwargs):
        captured_alerts.append(kwargs)

    import unittest.mock as _mock
    with _mock.patch.object(alerting, "write_alert", side_effect=_capture):
        enforce_chain_integrity(db)

    missing_alerts = [
        a for a in captured_alerts
        if a.get("source") == "audit_chain_db_anchor:missing"
    ]
    assert len(missing_alerts) == 1
    assert missing_alerts[0]["severity"] == "critical"


def test_singleton_check_constraint_blocks_second_row(db):
    """The CheckConstraint id=1 enforces the singleton invariant —
    attempting to insert id=2 must fail at the DB level."""
    from sqlalchemy.exc import IntegrityError

    # Wrap in a savepoint so the check failure doesn't poison the test session
    nested = db.begin_nested()
    try:
        with pytest.raises((IntegrityError, Exception)):
            db.execute(
                text(
                    "INSERT INTO audit_chain_anchor (id, chain_head, revision_counter) "
                    "VALUES (2, :gen, 0)"
                ),
                {"gen": _GENESIS_HASH},
            )
            db.flush()
    finally:
        nested.rollback()
