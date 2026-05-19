"""Contract tests for the invariant_monitor poisoned-session structural
fix (born 2026-05-19, from the Sentry deep-DA).

Locks the eventually-consistent safety-net invariant: a caught DB
failure in ANY check or alert-write must NOT leave the session
poisoned for the rest of the cycle. The pre-fix form swallowed 6
invariant alert-writes on 2026-05-11 (InFailedSqlTransaction cascade).

These pin the two structural primitives so a refactor cannot silently
regress them. Pure-unit (fake session) — no DB needed, deterministic.
"""
from __future__ import annotations

from app.services.invariant_monitor import _rollback_quiet, _safe_check


class _FakeSession:
    """Models a SQLAlchemy session that gets poisoned by a failed
    statement and is only usable again after rollback()."""

    def __init__(self, *, rollback_raises: bool = False):
        self.poisoned = False
        self.rollback_calls = 0
        self._rollback_raises = rollback_raises

    def query(self, *a, **k):
        if self.poisoned:
            raise RuntimeError(
                "(psycopg2.errors.InFailedSqlTransaction) current "
                "transaction is aborted, commands ignored until end of "
                "transaction block"
            )
        return self

    def rollback(self):
        self.rollback_calls += 1
        if self._rollback_raises:
            raise RuntimeError("connection already closed")
        self.poisoned = False


class TestRollbackQuiet:
    def test_un_poisons_session(self):
        db = _FakeSession()
        db.poisoned = True
        _rollback_quiet(db)
        assert db.rollback_calls == 1
        assert db.poisoned is False

    def test_swallows_rollback_failure(self):
        """If rollback() itself raises (dead connection) the helper must
        NOT propagate — the worker's finally db.close() + next cycle is
        clean anyway. Re-raising would mask the original error the
        handler exists to record."""
        db = _FakeSession(rollback_raises=True)
        db.poisoned = True
        _rollback_quiet(db)  # must not raise
        assert db.rollback_calls == 1


class TestSafeCheck:
    def test_failing_check_rolls_back_and_does_not_propagate(self):
        db = _FakeSession()
        summary: dict = {}

        def _bad_check(_db, _summary):
            _db.poisoned = True
            raise RuntimeError(
                "(psycopg2.errors.OperationalError) server closed the "
                "connection unexpectedly"
            )

        _safe_check(_bad_check, db, summary)  # must not raise
        assert db.rollback_calls == 1
        assert db.poisoned is False  # un-poisoned for the next check

    def test_passing_check_runs_clean_no_rollback(self):
        db = _FakeSession()
        summary = {"checked": 0}

        def _ok_check(_db, _summary):
            _summary["checked"] += 1

        _safe_check(_ok_check, db, summary)
        assert summary["checked"] == 1
        assert db.rollback_calls == 0

    def test_poison_does_not_cascade_across_checks(self):
        """The exact 2026-05-11 mechanism: a poisoned session must not
        make the NEXT check fail. With _safe_check, check-1's failure is
        rolled back so check-2 runs clean."""
        db = _FakeSession()
        ran: list[str] = []

        def _check1(_db, _s):
            _db.poisoned = True
            raise RuntimeError("InFailedSqlTransaction: aborted")

        def _check2(_db, _s):
            # Would raise if the session were still poisoned.
            _db.query()
            ran.append("check2")

        for fn in (_check1, _check2):
            _safe_check(fn, db, {})

        assert ran == ["check2"]  # check-2 ran clean despite check-1 poison
        assert db.rollback_calls == 1
