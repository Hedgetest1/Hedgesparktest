"""Contract: get_lazy_db (the WRITE-side sibling of get_lazy_read_db)
checks out a primary pooled connection ONLY on first real DB use — and
its rollback()/commit() are GUARDED no-ops when no connection was ever
taken, so the write_no_rollback error path on the Redis-only /track
branch stays 0-conn.

Born 2026-05-17 (jewel J3 follow-on, honest-residual #6). `/track` is
the highest-volume path; post J3-part-2 its dominant non-purchase
branch is Redis-only (known-shop cache hit → enqueue → return), but the
pre-existing eager Depends(get_db) pinned a primary PgBouncer
connection for the whole request — the c≈64 conn-pin cliff class on
the busiest write path. These tests pin the invariants that catch a
regression:
  1. holder never accessed → SessionLocal NEVER constructed (0 conns).
  2. first attribute access → constructed exactly once, delegated.
  3. teardown → real session closed iff it was opened.
  4. exception mid-use → still closed (no leak).
  5. `with db:` dunder delegated (defensive — guards future call sites).
  6. close_if_opened never-opened → no construction.
  7. rollback() never-opened → NO checkout (the write_no_rollback
     error-path 0-conn property — the pre-mortem failure mode).
  8. commit() never-opened → NO checkout (guard symmetry).
  9. rollback()/commit() AFTER open → delegated to the real session.
 10. audit_track_lazy_db preventer is NON-VACUOUS: it flags the exact
     pre-fix Depends(get_db) shape and passes the fixed tree.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import app.core.database as dbmod

_BACKEND = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_tld", _BACKEND / "scripts" / "audit_track_lazy_db.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_never_accessed_opens_zero_connections():
    with patch.object(dbmod, "SessionLocal") as SL:
        gen = dbmod.get_lazy_db()
        next(gen)  # handler returns the buffered branch WITHOUT db:
        try:
            next(gen)  # teardown (finally → close_if_opened)
        except StopIteration:
            pass
        SL.assert_not_called()  # the whole point: 0 pooled conns


def test_first_attribute_access_opens_once_and_delegates():
    fake = MagicMock(name="SessionLocal()")
    fake.query.return_value = "RESULT"
    with patch.object(dbmod, "SessionLocal", return_value=fake) as SL:
        gen = dbmod.get_lazy_db()
        holder = next(gen)
        assert holder.query("X") == "RESULT"      # triggers _ensure
        assert holder.execute("Y") is fake.execute.return_value
        try:
            next(gen)
        except StopIteration:
            pass
        SL.assert_called_once()                    # opened exactly once
        fake.close.assert_called_once()            # closed at teardown


def test_exception_during_use_still_closes():
    fake = MagicMock(name="SessionLocal()")
    with patch.object(dbmod, "SessionLocal", return_value=fake):
        gen = dbmod.get_lazy_db()
        holder = next(gen)
        holder.query("X")           # opened
        try:
            gen.throw(RuntimeError("boom"))
        except RuntimeError:
            pass
        fake.close.assert_called_once()  # finally closed despite raise


def test_with_statement_is_delegated():
    fake = MagicMock(name="SessionLocal()")
    fake.__enter__.return_value = "ENTERED"
    with patch.object(dbmod, "SessionLocal", return_value=fake):
        holder = dbmod._LazyDbSession()
        with holder as h:
            assert h == "ENTERED"
        fake.__enter__.assert_called_once()
        fake.__exit__.assert_called_once()


def test_close_if_opened_is_noop_when_never_opened():
    with patch.object(dbmod, "SessionLocal") as SL:
        holder = dbmod._LazyDbSession()
        holder.close_if_opened()      # must not construct/raise
        SL.assert_not_called()


def test_rollback_never_opened_does_not_check_out_a_connection():
    """The pre-mortem invariant: track_event's write_no_rollback
    defense does `except Exception: db.rollback()`. If the failure was
    on the Redis-only branch before any DB use, rollback() MUST be a
    no-op that does NOT construct a session — otherwise the error path
    pins a conn purely to roll back a txn that never began."""
    with patch.object(dbmod, "SessionLocal") as SL:
        holder = dbmod._LazyDbSession()
        holder.rollback()            # nothing taken ⟹ nothing to undo
        SL.assert_not_called()


def test_commit_never_opened_does_not_check_out_a_connection():
    with patch.object(dbmod, "SessionLocal") as SL:
        holder = dbmod._LazyDbSession()
        holder.commit()
        SL.assert_not_called()


def test_rollback_and_commit_after_open_delegate_to_real_session():
    fake = MagicMock(name="SessionLocal()")
    with patch.object(dbmod, "SessionLocal", return_value=fake):
        holder = dbmod._LazyDbSession()
        holder.query("X")            # opens the real session
        holder.rollback()
        holder.commit()
        fake.rollback.assert_called_once()
        fake.commit.assert_called_once()


def test_audit_track_lazy_db_is_non_vacuous(tmp_path):
    """Prove the preventer flags the exact pre-fix shape and passes the
    fixed shape — a vacuous audit (always-green) is theater."""
    audit = _load_audit()

    pre_fix = (
        "from fastapi import Depends\n"
        "from app.core.database import get_db\n"
        "@router.post('/track')\n"
        "def track_event(request, payload, db=Depends(get_db)):\n"
        "    return {}\n"
        "@router.post('/track/batch')\n"
        "def track_event_batch(payload, db=Depends(get_lazy_db)):\n"
        "    return {}\n"
    )
    fixed = pre_fix.replace("db=Depends(get_db)", "db=Depends(get_lazy_db)")

    f = tmp_path / "track.py"
    with patch.object(audit, "TARGET", f):
        f.write_text(pre_fix)
        assert audit.main() == 1          # MUST flag the eager shape
        f.write_text(fixed)
        assert audit.main() == 0          # GREEN on the lazy-wired tree

    # And the real tree must be GREEN (the live contract holds).
    assert audit.main() == 0
