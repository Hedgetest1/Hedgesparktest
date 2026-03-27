"""Tests for immutable audit log (audit.py + AuditLog model)."""
from sqlalchemy import text

from app.services.audit import write_audit_log
from tests.conftest import SHOP_A


def test_write_audit_log_persists(db):
    """write_audit_log creates a row with all fields."""
    entry = write_audit_log(
        db,
        actor_type="system",
        actor_name="test_runner",
        action_type="test_action",
        target_type="merchant",
        target_id=SHOP_A,
        shop_domain=SHOP_A,
        before_state={"status": "before"},
        after_state={"status": "after"},
        status="completed",
        approval_mode="autonomous",
        metadata={"test": True},
    )
    assert entry.id is not None
    assert entry.actor_type == "system"
    assert entry.action_type == "test_action"
    assert entry.shop_domain == SHOP_A

    # Verify in DB
    row = db.execute(text(
        "SELECT actor_type, actor_name, action_type, status FROM audit_log WHERE id = :id"
    ), {"id": entry.id}).fetchone()
    assert row is not None
    assert row[0] == "system"
    assert row[2] == "test_action"


def test_audit_log_minimal_fields(db):
    """Audit log works with only required fields."""
    entry = write_audit_log(
        db,
        actor_type="worker",
        actor_name="aggregation_worker",
        action_type="metrics_refresh",
    )
    assert entry.id is not None
    assert entry.target_type is None
    assert entry.shop_domain is None
    assert entry.before_state is None


def test_audit_log_json_serialization(db):
    """Complex state objects serialize to JSON."""
    entry = write_audit_log(
        db,
        actor_type="agent",
        actor_name="signal_qa_agent",
        action_type="signal_validation",
        before_state={"signals": [1, 2, 3], "count": 3},
        after_state={"signals": [1, 2], "count": 2, "removed": 1},
        metadata={"reason": "duplicate detected", "confidence": 0.95},
    )
    assert entry.before_state is not None
    assert '"count": 3' in entry.before_state
    assert '"confidence": 0.95' in entry.metadata_json


def test_multiple_audit_entries(db):
    """Multiple entries create sequential IDs (append-only)."""
    e1 = write_audit_log(db, actor_type="system", actor_name="a", action_type="first")
    e2 = write_audit_log(db, actor_type="system", actor_name="b", action_type="second")
    assert e2.id > e1.id
