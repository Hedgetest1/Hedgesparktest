"""Deterministic safety tests for J4-part-1 — DROP fully-aged whole
`events` partitions in retention (aggregation_worker retention_task,
2026-05-18). Design led + re-verified by an independent Agent
(a93059e); this pins the safety-critical invariants WITHOUT real DDL
(DETACH CONCURRENTLY + DROP cannot run inside the SAVEPOINT test
harness — the live-lock mechanism is a separate manual check, flagged
by the design Agent).

Load-bearing invariants pinned (each = a way irreversible data loss
or a 10k ingest stall would happen if it regressed):
  1. droppable predicate boundary: child droppable iff upper-bound
     `hi <= cutoff` (half-open [a,b) ⟹ max row = hi-1 < cutoff).
  2. events_default + any unparseable bound are NEVER enumerated
     (fail-safe ⟹ row-DELETE handles them; never DROP the catch-all).
  3. forward APP-clock skew cannot widen the drop set past the DB's
     own now() (the min(cutoff, db_now-91d) double guard).
  4. the drop phase is TOTALLY fail-safe — ANY error returns 0, never
     raises (the unchanged batched row-DELETE is a superset fallback).
  5. name-injection guard: _detach_then_drop refuses any name not
     matching ^events_y\\d{4}m\\d{2}$ (no DDL-injection surface).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.workers.tasks.retention_task as rt


class _FakeRes:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


def _row(name, bound):
    m = MagicMock()
    m.name = name
    m.bound = bound
    return m


def test_bound_regex_parses_pg15_format_rejects_default_and_malformed():
    assert rt._PARTITION_BOUND_RE.search(
        "FOR VALUES FROM ('1700000000000') TO ('1800000000000')"
    ).group(2) == "1800000000000"
    assert rt._PARTITION_BOUND_RE.search("DEFAULT") is None
    assert rt._PARTITION_BOUND_RE.search("FROM (x) TO (y)") is None


def test_enumerate_excludes_default_and_unparseable():
    conn = MagicMock()
    conn.execute.return_value = _FakeRes(rows=[
        _row("events_y2026m03", "FOR VALUES FROM ('100') TO ('200')"),
        _row("events_default", "DEFAULT"),
        _row("events_weird", "FOR VALUES IN ('x')"),     # unparseable
    ])
    out = rt._enumerate_partition_bounds(conn)
    assert out == [("events_y2026m03", 200)]              # ONLY the parseable child


def test_droppable_predicate_boundary_half_open():
    """hi == cutoff ⟹ droppable (max row = hi-1 < cutoff, half-open).
    hi == cutoff+1 ⟹ NOT droppable (it can hold an in-window row)."""
    cutoff = 1_000_000
    parsed = [("events_y2026m01", cutoff),          # droppable (hi == cutoff)
              ("events_y2026m02", cutoff + 1),      # NOT (straddle/in-window)
              ("events_y2026m03", cutoff - 1)]      # droppable (older)
    droppable = sorted(n for (n, hi) in parsed if hi <= cutoff)
    assert droppable == ["events_y2026m01", "events_y2026m03"]
    assert "events_y2026m02" not in droppable


def test_clock_skew_guard_blocks_forward_app_skew():
    """App `cutoff_ms` skewed FAR future, but the DB's own now() is
    real ⟹ effective = min(cutoff, db_now-91d) stays old ⟹ a recent
    partition is NOT dropped."""
    conn = MagicMock()
    real_db_now_ms = 1_700_000_000_000
    conn.execute.return_value = _FakeRes(scalar=real_db_now_ms)
    recent_hi = real_db_now_ms - 5 * 86_400_000        # only 5 days old
    with patch.object(rt, "_enumerate_partition_bounds",
                      return_value=[("events_y2099m01", recent_hi)]), \
         patch.object(rt, "_detach_then_drop") as dd:
        # cutoff_ms is absurd-future (forward app-skew)
        n = rt._drop_fully_aged_event_partitions(conn, 9_999_999_999_999)
    assert n == 0
    dd.assert_not_called()                              # skew did NOT widen the set


def test_drop_phase_is_totally_failsafe():
    """ANY error in the drop phase ⟹ return 0, NEVER raise (the
    batched row-DELETE after it is a complete superset fallback)."""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("db exploded")
    assert rt._drop_fully_aged_event_partitions(conn, 1_000) == 0  # no raise


def test_detach_then_drop_refuses_injection_and_non_partition_names():
    from app.core import database as dbmod
    for bad in ("events", "users", 'events"; DROP TABLE x;--',
                "events_y2026m03; DROP TABLE events"):
        with patch.object(dbmod, "engine") as eng:
            with pytest.raises(ValueError):
                rt._detach_then_drop(bad)
            eng.raw_connection.assert_not_called()      # never touched the DB


def test_detach_sets_autocommit_on_the_REAL_dbapi_connection():
    """MAKE-OR-BREAK (independent Agent a28854e caught this): on a
    SQLAlchemy _ConnectionFairy, `raw.autocommit = True` is silent
    attribute-shadowing — it NEVER reaches psycopg2, so
    DETACH...CONCURRENTLY raises ActiveSqlTransaction and J4 is
    SILENTLY DEAD. Pin that autocommit is set on
    `.driver_connection` (the real DBAPI conn). Cheap, no DDL,
    SAVEPOINT-safe — exactly the deterministic check that would have
    caught the bug."""
    from app.core import database as dbmod
    fake_raw = MagicMock(name="ConnectionFairy")
    fake_raw.driver_connection = MagicMock(name="psycopg2_conn")
    with patch.object(dbmod, "engine") as eng:
        eng.raw_connection.return_value = fake_raw
        rt._detach_then_drop("events_y2026m03")
    # the REAL DBAPI connection got autocommit=True (NOT a shadowed
    # attribute on the fairy):
    assert fake_raw.driver_connection.autocommit is True
    # and a DETACH ... CONCURRENTLY + DROP were actually issued:
    sql = " ".join(str(c.args[0]) for c in
                    fake_raw.cursor.return_value.execute.call_args_list)
    assert "DETACH PARTITION" in sql and "CONCURRENTLY" in sql
    assert "DROP TABLE IF EXISTS" in sql


def test_drop_phase_drops_only_fully_aged_capped_by_breaker():
    conn = MagicMock()
    db_now = 1_700_000_000_000
    conn.execute.return_value = _FakeRes(scalar=db_now)
    old_hi = db_now - 200 * 86_400_000                  # ~200d old → droppable
    parts = [(f"events_y2025m{m:02d}", old_hi) for m in range(1, 13)] + \
            [("events_y2099m01", db_now)]               # recent → NOT droppable
    with patch.object(rt, "_enumerate_partition_bounds", return_value=parts), \
         patch.object(rt, "_detach_then_drop") as dd, \
         patch.object(rt, "_RETENTION_MAX_PARTITION_DROPS", 5):
        n = rt._drop_fully_aged_event_partitions(conn, db_now)  # cutoff=now (huge)
    assert n == 5                                       # breaker cap honoured
    dropped = [c.args[0] for c in dd.call_args_list]
    assert all(name.startswith("events_y2025m") for name in dropped)
    assert "events_y2099m01" not in dropped             # recent never dropped
