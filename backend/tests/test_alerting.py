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


def test_step2_savepoint_happy_path_with_simulated_flush_failure(db):
    """Behavioral smoke for the §21 Stage-2 fix (2026-05-20): when
    Step-2 delivery flush fails, write_alert still returns a usable
    alert object and the next write_alert call still produces a row.

    **HONESTY NOTE (anti-flattery correction post-empirical proof
    2026-05-20)**: this test is NOT mutation-sensitive for the
    savepoint_scope wrap, despite earlier docstring claims. An
    empirical proof (monkey-patch savepoint_scope to no-op + run
    this test) PASSED, meaning the test cannot distinguish
    "savepoint_scope present" from "savepoint_scope stripped". The
    pytest test fixture wraps every test in its OWN outer savepoint
    (via conftest's SAVEPOINT-per-test pattern), which absorbs the
    Step-2 flush failure regardless of whether alerting.py wraps
    Step 2 internally. Earlier §22.7 ship-gate Agent verification
    used code analysis and missed this — empirical verification
    (this docstring's correction) caught the false claim.

    The MUTATION-SENSITIVE assertion lives in
    `test_alerting_step2_uses_savepoint_scope_structural` (below).
    This test is kept as a happy-path smoke — it does verify that
    write_alert returns an alert object even when Step-2 fails, and
    that the subsequent call doesn't crash on imports/setup.
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


def test_alerting_step2_uses_savepoint_scope_structural():
    """STRUCTURAL mutation-sensitive contract for the §21 Stage-2 fix
    (commit 855390a): `write_alert` MUST wrap Step 2 in
    `with savepoint_scope(db):`. Stripping it (refactor removes the
    wrap) breaks this assertion directly.

    Born 2026-05-20 after empirical mutation proof revealed the
    behavioral counterpart
    (test_step2_savepoint_happy_path_with_simulated_flush_failure)
    was VACUOUS: the pytest test fixture absorbs Step-2 failures in
    its outer savepoint regardless of whether alerting.py wraps
    internally. Behavioral tests cannot catch the mutation in this
    environment; a structural assertion is the load-bearing pin.

    Production-equivalent correctness is verified at the
    savepoint_scope PRIMITIVE level (app/core/database.py contract
    tests, commit 42fc791). write_alert's correctness = composition
    of (this structural assertion) + (savepoint_scope primitive
    contract). Together they catch any future refactor that:
      - removes `with savepoint_scope(db):` from write_alert, OR
      - breaks savepoint_scope's swallow-recovery guarantee.

    Caught the failure mode the §22.7 Agent missed: code analysis
    cannot see the test-fixture-savepoint interference. Empirical
    proof catches it. Ship correction same-session (anti-flattery
    discipline).
    """
    import inspect
    import app.services.alerting as alerting_mod

    src = inspect.getsource(alerting_mod.write_alert)
    assert "with savepoint_scope(db):" in src, (
        "§21 Stage-2 structural fix (855390a) was stripped: "
        "write_alert no longer wraps Step 2 in savepoint_scope. "
        "Restore the wrap or document why the class can be reopened."
    )


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
