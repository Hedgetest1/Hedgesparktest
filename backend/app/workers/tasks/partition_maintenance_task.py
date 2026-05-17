"""partition_maintenance_task — keep the `events` RANGE partitions
rolling forward so new rows never fall into `events_default`.

Born 2026-05-17 after an independent capillary 10k audit found a
**dated production outage, merchant-count-independent**: the
`bb1_events_partitioning` migration created monthly partitions only
3 months ahead of its own 2026-03-24 apply date (last partition
`events_y2026m06`). The idempotent `create_events_partition(year,
month)` plpgsql helper it shipped has **zero application callers** —
nothing ever creates the next month. From 2026-07-01 onward 100% of
new events land in the catch-all `events_default`, which:
  - grows unbounded (no pruning, no cheap DROP),
  - defeats every per-shop event query's partition pruning,
  - makes retention's time-cutoff DELETE the only reclamation path
    on an ever-growing single table.

This task closes that gap permanently with the EXISTING in-repo
primitive (no schema migration, no new infra — §2 r1 reuse, §2 r10
scale-only-what's-needed): once per 24h ensure the current month +
the next 3 months exist. `create_events_partition` is idempotent
(`IF NOT EXISTS` guard, verified in pg_proc), so re-calling is a
cheap no-op. ~4 function calls/day. Mirrors the night_shift_task /
retention_task is_due()/run() shape exactly.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.database import SessionLocal

# How many months AHEAD to guarantee exist. 3 = a full quarter of
# head-room so even a worker outage of weeks cannot reach the cliff
# before the next successful run. Env-tunable kill/Scale knob (§2 r11).
import os
_MONTHS_AHEAD = int(os.getenv("EVENTS_PARTITION_MONTHS_AHEAD", "3"))
_INTERVAL_S = 86_400  # once per 24h — partitions are monthly; daily is ample
_last_run: float | None = None


def is_due() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def _mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def _months_to_ensure(now: datetime) -> list[tuple[int, int]]:
    """Current month + the next _MONTHS_AHEAD, as (year, month)."""
    out: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _ in range(_MONTHS_AHEAD + 1):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def run() -> int:
    """Ensure the rolling window of events partitions exists. Returns
    the count of (year,month) slots ensured. Best-effort: a failure is
    logged by the caller and retried next cycle (the 3-month head-room
    means a transient failure is never the outage)."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    ensured = 0
    try:
        for (y, m) in _months_to_ensure(now):
            db.execute(
                text("SELECT create_events_partition(:y, :m)"),
                {"y": y, "m": m},
            )
            ensured += 1
        db.commit()
        _mark_done()
        return ensured
    finally:
        db.close()
