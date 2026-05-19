"""Contract tests for app.core.database.savepoint_scope — the SAVEPOINT
half of the write_no_rollback class close (born 2026-05-19).

Pins the three load-bearing invariants the 6 batch-loop sibling fixes
(intelligence_worker / action_learning / prediction_log /
uninstall_erasure / nudge_compose_task / action_proof) depend on:

  1. A failing iteration rolls back ONLY its own SAVEPOINT and the
     original exception propagates to the caller's existing handler.
  2. Prior successful iterations are PRESERVED (a bare rollback would
     discard them — the exact bug, e.g. GDPR Art.17 erasure requests).
  3. After a failing iteration the session is NOT poisoned — the next
     iteration's DB work succeeds (no InFailedSqlTransaction cascade).

Real Postgres dialect via the conftest SAVEPOINT-wrapped `db` fixture
(nested savepoints are supported); a TEMP TABLE keeps it model-free
and zero production leak.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.database import savepoint_scope


@pytest.fixture()
def _tmp(db):
    db.execute(text("CREATE TEMP TABLE _sp_test (id int PRIMARY KEY) ON COMMIT DROP"))
    db.flush()
    return db


def test_failing_iteration_isolated_good_preserved_session_clean(_tmp):
    db = _tmp
    seen_error = []
    # 'bad' raises inside the savepoint (duplicate then a forced error).
    for i in (1, 2, "BAD", 4):
        try:
            with savepoint_scope(db):
                if i == "BAD":
                    db.execute(text("INSERT INTO _sp_test (id) VALUES (1)"))  # PK dup → IntegrityError
                else:
                    db.execute(text("INSERT INTO _sp_test (id) VALUES (:v)"), {"v": i})
        except Exception as exc:  # the caller's existing handler
            seen_error.append(type(exc).__name__)
            continue

    # (1) the failing iteration raised into the handler
    assert len(seen_error) == 1, "the BAD iteration must surface to the handler"
    # (2) prior good rows preserved + (3) the iteration AFTER the failure
    #     succeeded → session was NOT left poisoned
    rows = {r[0] for r in db.execute(text("SELECT id FROM _sp_test ORDER BY id")).fetchall()}
    assert rows == {1, 2, 4}, f"expected good rows preserved + post-fail row inserted, got {rows}"
    # session fully usable afterwards
    assert db.execute(text("SELECT 1")).scalar() == 1


def test_success_path_persists_and_does_not_full_commit(_tmp):
    db = _tmp
    for v in (10, 11, 12):
        with savepoint_scope(db):
            db.execute(text("INSERT INTO _sp_test (id) VALUES (:v)"), {"v": v})
    rows = {r[0] for r in db.execute(text("SELECT id FROM _sp_test")).fetchall()}
    assert rows == {10, 11, 12}
    # savepoint_scope must NOT have issued a full session.commit()
    # (that would dissolve the conftest test SAVEPOINT isolation). The
    # fixture teardown rolls everything back; nothing leaks. Implicitly
    # asserted by the suite's hermeticity audit — here we assert the
    # rows are visible WITHIN the txn (released savepoint, not committed).
    assert db.in_transaction()


def test_exception_type_is_preserved(_tmp):
    db = _tmp

    class _Sentinel(RuntimeError):
        pass

    with pytest.raises(_Sentinel):
        with savepoint_scope(db):
            raise _Sentinel("boom")
    # session still usable after a non-DB exception inside the scope
    assert db.execute(text("SELECT 1")).scalar() == 1


def test_inner_full_commit_fails_loud_not_silent(_tmp):
    """THE seal for the d15ada0 #1 regression: a body that issues a
    full session.commit() (e.g. a helper like update_product_
    opportunity) dissolves the SAVEPOINT. Pre-seal this raised a
    cryptic ResourceClosedError on EVERY iteration → silent
    rows_written=0 / empty shops_seen / Klaviyo-push stopped. The
    primitive must now fail LOUD + ACTIONABLE on first execution so a
    misclassified site (savepoint where rollback_quiet was correct) is
    impossible to ship silently."""
    db = _tmp
    with pytest.raises(RuntimeError, match="SAVEPOINT was.*dissolved|MUST use rollback_quiet"):
        with savepoint_scope(db):
            db.execute(text("INSERT INTO _sp_test (id) VALUES (99)"))
            db.commit()  # the illegal inner full commit (helper-commits class)


def test_swallowed_db_error_recovered_at_primitive(_tmp):
    """PRIMITIVE-LEVEL structural close of the write_no_rollback
    SWALLOW variant (Finding 1; born 2026-05-19e, §22.7 plan-verified
    a42ea12813b7c0b7b). A body that catches+SWALLOWS a DB error
    WITHOUT rolling back leaves the txn aborted but the SAVEPOINT
    intact (is_active stays True → the commit-dissolution guard does
    NOT fire). Pre-2026-05-19e this cascaded (RELEASE on aborted txn →
    deassociated SavepointTransaction → outer txn permanently
    poisoned). The primitive must now DETECT the aborted txn before
    RELEASE, ROLLBACK TO SAVEPOINT (valid+recovering while still
    associated), and raise loudly — so the outer session is usable for
    the next iteration regardless of caller discipline. This makes the
    swallow variant impossible for ALL present+future savepoint_scope
    sites (the per-site audit.py fix only covered audit.py)."""
    db = _tmp

    with pytest.raises(RuntimeError, match="swallowed a DB error"):
        with savepoint_scope(db):
            try:
                # Poison the txn (UndefinedTable) and SWALLOW it with
                # NO rollback — the exact best-effort-helper bug shape.
                db.execute(text("SELECT * FROM __sp_no_such_table__"))
            except Exception:
                pass  # swallow, no rollback (the bug)
            # body returns "normally" → savepoint_scope.__exit__ must
            # detect the aborted txn and recover, not RELEASE-cascade.

    # The whole point: the outer session is RECOVERED, not poisoned —
    # the next iteration's work succeeds (pre-fix this raised
    # InFailedSqlTransaction forever).
    assert db.execute(text("SELECT 1")).scalar() == 1
    db.execute(text("INSERT INTO _sp_test (id) VALUES (777)"))
    assert {r[0] for r in db.execute(
        text("SELECT id FROM _sp_test")).fetchall()} == {777}
