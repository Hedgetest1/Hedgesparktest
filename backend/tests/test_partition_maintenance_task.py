"""Contract tests — events partition roll-forward (born 2026-05-17,
defuses the dated `events_default` cliff: partitions had stopped at
2026-06, the in-DB create_events_partition had zero app callers)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.core.database import SessionLocal
from app.workers.tasks import partition_maintenance_task as P


def test_months_to_ensure_current_plus_ahead_with_year_wrap():
    """Current month + _MONTHS_AHEAD, wrapping the year at December —
    the bug class that caused the original cliff (a horizon that does
    not roll past the year boundary)."""
    got = P._months_to_ensure(datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert got[0] == (2026, 11)                       # current
    assert (2026, 12) in got and (2027, 1) in got     # wraps the year
    assert len(got) == P._MONTHS_AHEAD + 1
    # Strictly monotonic calendar sequence (no gap, no dup → no cliff).
    seq = [y * 12 + (m - 1) for (y, m) in got]
    assert seq == list(range(seq[0], seq[0] + len(seq)))


def test_is_due_gates_then_resets(monkeypatch):
    P._last_run = None
    assert P.is_due() is True                         # cold → due
    P._mark_done()
    assert P.is_due() is False                        # within interval
    # Far enough in the past → due again.
    monkeypatch.setattr(P, "_last_run", P._last_run - P._INTERVAL_S - 1)
    assert P.is_due() is True


def test_run_is_idempotent_and_covers_the_rolling_window():
    """run() ensures current+N months and is safe to re-call (the
    in-DB create_events_partition has an IF NOT EXISTS guard). After a
    run there must be NO future month within the horizon missing — the
    structural anti-cliff invariant."""
    P._last_run = None
    n1 = P.run()
    assert n1 == P._MONTHS_AHEAD + 1
    n2 = P.run()                                      # idempotent re-call
    assert n2 == P._MONTHS_AHEAD + 1                  # no error, same count

    now = datetime.now(timezone.utc)
    want = {f"events_y{y}m{m:02d}" for (y, m) in P._months_to_ensure(now)}
    db = SessionLocal()
    try:
        have = {
            r[0] for r in db.execute(text(
                "SELECT inhrelid::regclass::text FROM pg_inherits "
                "WHERE inhparent='events'::regclass"
            )).fetchall()
        }
    finally:
        db.close()
    missing = want - have
    assert not missing, (
        f"rolling-window partitions missing after run() → the dated "
        f"events_default cliff is NOT defused: {sorted(missing)}"
    )
