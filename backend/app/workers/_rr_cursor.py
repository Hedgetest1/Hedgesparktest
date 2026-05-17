"""Shared round-robin cursor for time-budget-bounded per-shop worker loops.

Born 2026-05-17. `aggregation_worker.store_metrics` had a 240s time-budget
`break` over an in-memory shop *set* with NO cursor: at 10k merchants the
budget exhausted after the first N shops in iteration order and the loop
re-started from the *same* head every cycle, so the iteration-order tail
was **systematically never reached** — store_metrics/SIP/execution never
refreshed for it AND `prewarm_lite_dashboard` never ran for it, so those
merchants had no sticky last-known-good, which is exactly what the
2026-05-16f 4th-tier cold-build admission fix sheds to. That made the
"the worker prewarms every active merchant every cycle" claim (the
load-bearing premise under the 41%-cliff fix's realistic-state proof)
**false at 10k**.

This is the proven `segment_monitor_worker` / `agent_worker` cursor
pattern, consolidated into one helper so the next per-shop worker loop
reuses it instead of copy-pasting a 4th time, and so the structural
preventer has a single canonical shape to assert.

Contract:
  - Redis-backed integer cursor, 24h TTL (survives PM2 restarts within a
    cycle; self-heals if the worker is down a whole day).
  - Redis down → **degrade-open**: cursor 0 (process from head), emit a
    silent-return telemetry breadcrumb, never raise. Failing closed
    would *starve* the loop — the opposite of the bug we are fixing.
  - `rr_slice` returns the deterministic wrap-around slice for this cycle
    (caller MUST pass a deterministically-ordered list — sort it).
  - `next_cursor` advances by **actual-processed count**, not by the
    slice window. A mid-batch time-budget break therefore resumes
    exactly where it stopped — strict fairness even under *sustained*
    budget pressure. This is intentionally stronger than the
    segment_monitor advance-by-window (which skips the un-processed tail
    of a broken batch): on these hot paths the budget break is the
    COMMON case at 10k, not a rare safety net, so skip-the-tail would
    reintroduce the very starvation this helper exists to remove.
    Wrap-partial cycles can re-process a bounded (≤ slice) prefix; every
    advanced op (store_metrics upsert, opportunity upsert, prewarm) is
    idempotent, so the only cost is bounded redundant work, never a
    skipped shop.
"""
from __future__ import annotations

import logging

log = logging.getLogger("rr_cursor")

_TTL_SEC = 86400  # 24h — a day-down worker self-heals to a fresh start


def load_cursor(key: str) -> int:
    """Current cursor position. 0 on miss / redis-down (process from
    head — degrade-open; failing closed would starve the loop)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return(f"rr_cursor.load.redis_down:{key}")
            return 0
        v = rc.get(key)
        return int(v) if v else 0
    except Exception as exc:
        log.warning("rr_cursor: load_cursor(%s) failed: %s", key, exc)
        return 0


def save_cursor(key: str, pos: int) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return(f"rr_cursor.save.redis_down:{key}")
            return
        rc.set(key, str(int(pos)), ex=_TTL_SEC)
    except Exception as exc:
        log.warning("rr_cursor: save_cursor(%s) failed: %s", key, exc)


def rr_slice(items: list, cursor: int, max_per_cycle: int) -> list:
    """Deterministic wrap-around slice starting at ``cursor % len``.

    ``items`` MUST already be deterministically ordered by the caller
    (sorted) so the cursor is stable across cycles. At ``<= max_per_cycle``
    items the whole list is returned (the cursor is then irrelevant; the
    loop covers everyone every cycle as before — zero behaviour change
    below scale)."""
    n = len(items)
    if n == 0:
        return []
    if n <= max_per_cycle:
        return list(items)
    start = cursor % n
    end = start + max_per_cycle
    if end <= n:
        return items[start:end]
    return items[start:] + items[: end - n]


def next_cursor(cursor: int, processed: int, total: int) -> int:
    """Advance by ACTUAL-PROCESSED count, wrapped to ``total``.

    ``processed == 0`` → no advance (a cycle that did nothing must not
    skip ground). ``total <= 0`` → 0 (defensive; empty population)."""
    if total <= 0:
        return 0
    return (cursor + max(0, processed)) % total
