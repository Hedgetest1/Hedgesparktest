"""Tests for operational alerting (alerting.py + OpsAlert model)."""
from unittest.mock import patch

from sqlalchemy import text

from app.services.alerting import write_alert, get_unresolved_alerts, resolve_alert
from tests.conftest import SHOP_A


def test_write_alert_persists(db):
    """write_alert creates a row with all fields."""
    alert = write_alert(
        db,
        severity="warning",
        source="test_runner",
        alert_type="test_alert",
        summary="Test alert summary",
        shop_domain=SHOP_A,
        detail={"key": "value"},
    )
    assert alert.id is not None
    assert alert.severity == "warning"
    assert alert.resolved is False

    row = db.execute(text(
        "SELECT severity, alert_type, resolved FROM ops_alerts WHERE id = :id"
    ), {"id": alert.id}).fetchone()
    assert row is not None
    assert row[0] == "warning"


def test_step2_savepoint_isolates_flush_failure_from_alert_row(db):
    """Pin the §21 Stage-2 fix (2026-05-20): a Step-2 FLUSH failure
    MUST NOT poison the caller's session NOR lose the alert row.

    Before the fix, alerting.py had a nested flush at line :338 inside
    Step 2's except handler. If the outer flush at :334 failed, the
    session was poisoned (PendingRollbackError); the next caller op
    (emit() at :356 OR the test_caller's next DB op) raised
    InFailedSqlTransaction.

    The fix wraps Step 2 (delivery_status update + flush) in
    `with savepoint_scope(db):`. A flush failure inside Step 2 rolls
    back the savepoint (Step 2's mutation reverted) while the alert
    row from Step 1 (db.add(alert); db.flush()) remains durable.

    **Mutation-sensitive**: this test fires a controlled flush failure
    on the SECOND flush call (Step 2's flush, INSIDE the savepoint
    body). If the savepoint_scope wrap is stripped, the failure
    cascades to the caller's session and the follow_up write_alert
    raises InFailedSqlTransaction. The §22.7 ship-gate Agent
    (2026-05-20) flagged the earlier version of this test as vacuous
    because it patched delivery to raise BEFORE any flush — both
    with-savepoint and stripped-savepoint passed. THIS version
    actually fails the flush.
    """
    # Patch db.flush with a counting wrapper that fails on the 2nd
    # invocation (Step 2's flush) AND succeeds otherwise. Step 1's
    # flush (1st call) and the follow_up's flushes succeed normally.
    original_flush = db.flush
    flush_calls = {"n": 0}

    def counted_flush(*args, **kwargs):
        flush_calls["n"] += 1
        if flush_calls["n"] == 2:
            # Simulate a constraint violation / DB-side failure on
            # Step 2's flush. The exact exception type is irrelevant —
            # what matters is the session becomes poisoned unless the
            # savepoint isolates it.
            raise RuntimeError("simulated step-2 flush failure")
        return original_flush(*args, **kwargs)

    with patch.object(db, "flush", side_effect=counted_flush), \
         patch(
             "app.core.alert_delivery.deliver_alert_externally",
             return_value=True,
         ):
        alert = write_alert(
            db,
            severity="warning",
            source="test_step2_iso",
            alert_type="step2_savepoint_contract",
            summary="Step 2 flush failure must not poison the session",
            shop_domain=SHOP_A,
            detail={"sim": "flush_boom"},
        )

    # Step 1 invariant — alert row IS durable (the docstring's "always
    # persist" guarantee). The 1st flush (Step 1) succeeded; Step 2's
    # flush failed inside the savepoint which rolled back only Step 2's
    # in-memory delivery_status mutation.
    assert alert.id is not None
    row = db.execute(
        text(
            "SELECT alert_type, resolved FROM ops_alerts WHERE id = :id"
        ),
        {"id": alert.id},
    ).fetchone()
    assert row is not None
    assert row[0] == "step2_savepoint_contract"

    # Step 2 invariant — the caller's session is NOT poisoned. The
    # following write would raise InFailedSqlTransaction if Step 2's
    # flush failure had not been savepoint-isolated. This is the
    # mutation-sensitive part: strip `with savepoint_scope(db):` from
    # alerting.py and this assertion fires.
    follow_up = write_alert(
        db,
        severity="info",
        source="test_step2_iso",
        alert_type="step2_savepoint_followup",
        summary="If this row exists, the session was clean post-Step-2-failure",
    )
    assert follow_up.id is not None


def test_get_unresolved_alerts(db):
    """get_unresolved_alerts returns only unresolved alerts."""
    write_alert(db, severity="info", source="t", alert_type="a", summary="open")
    write_alert(db, severity="critical", source="t", alert_type="b", summary="also open")
    db.flush()

    alerts = get_unresolved_alerts(db)
    assert len(alerts) >= 2
    assert all(not a.resolved for a in alerts)


def test_get_unresolved_filtered_by_severity(db):
    """Severity filter works."""
    write_alert(db, severity="info", source="t", alert_type="a", summary="info")
    write_alert(db, severity="critical", source="t", alert_type="b", summary="crit")
    db.flush()

    crits = get_unresolved_alerts(db, severity="critical")
    assert all(a.severity == "critical" for a in crits)


def test_resolve_alert_marks_resolved(db):
    """resolve_alert sets resolved=True and resolved_at."""
    alert = write_alert(db, severity="warning", source="t", alert_type="a", summary="fix me")
    db.flush()
    assert alert.resolved is False

    resolve_alert(db, alert.id)
    db.flush()

    row = db.execute(text(
        "SELECT resolved, resolved_at FROM ops_alerts WHERE id = :id"
    ), {"id": alert.id}).fetchone()
    assert row[0] is True
    assert row[1] is not None
