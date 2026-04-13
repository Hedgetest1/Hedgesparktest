"""
retention_task.py — Data retention + expired-signal cleanup.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Owns:

    cleanup_expired_signals(conn)       — runs EVERY cycle
    should_run_event_retention()        — 24h gate
    run_event_retention(conn, now_ms)   — delete events > RETENTION_DAYS
    run_nudge_event_retention(conn)     — delete nudge_events > 60d
    run_worker_log_retention(conn)      — delete worker_log > 30d
    mark_retention_done()               — advance internal dwell timer

The internal `_last_retention_run` state used to live in the worker
module. It now lives here — the orchestrator calls `should_run_event_retention`
and `mark_retention_done` rather than manipulating the flag directly.
This is a behavior-preserving refactor: the 24h window is still enforced
per-process, it just lives in one place now.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

_log = logging.getLogger("worker.aggregation.retention")

RETENTION_DAYS = 90
NUDGE_EVENT_RETENTION_DAYS = 60
WORKER_LOG_RETENTION_DAYS = 30

_RETENTION_INTERVAL_S = 86_400  # once per 24h

# Module-level state — owned here, no longer in aggregation_worker.
_last_retention_run: float | None = None


def cleanup_expired_signals(conn) -> int:
    """
    Delete opportunity_signals rows whose hard expiry has passed.
    Runs every cycle; the expires_at index makes it a fast range delete.
    """
    result = conn.execute(
        text("DELETE FROM opportunity_signals WHERE expires_at < now()")
    )
    return result.rowcount


def should_run_event_retention() -> bool:
    if _last_retention_run is None:
        return True
    return (time.monotonic() - _last_retention_run) >= _RETENTION_INTERVAL_S


def mark_retention_done() -> None:
    """Advance dwell timer. Call only after a successful commit."""
    global _last_retention_run
    _last_retention_run = time.monotonic()


def get_distinct_shops(conn) -> list[str]:
    result = conn.execute(
        text("SELECT DISTINCT shop_domain FROM events WHERE shop_domain IS NOT NULL")
    )
    return [row.shop_domain for row in result.fetchall()]


def run_event_retention(conn, now_ms: int) -> int:
    """
    Delete events older than RETENTION_DAYS, one shop at a time.

    Each per-shop DELETE uses the events(shop_domain, timestamp DESC)
    index efficiently. Returns total rows deleted across all shops.
    """
    cutoff_ms = now_ms - (RETENTION_DAYS * 24 * 3_600 * 1_000)
    shops = get_distinct_shops(conn)
    total_deleted = 0
    for shop in shops:
        result = conn.execute(
            text("""
                DELETE FROM events
                WHERE shop_domain = :shop
                  AND timestamp   < :cutoff_ms
            """),
            {"shop": shop, "cutoff_ms": cutoff_ms},
        )
        total_deleted += result.rowcount
    return total_deleted


def run_nudge_event_retention(conn) -> int:
    """Delete nudge_events older than NUDGE_EVENT_RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=NUDGE_EVENT_RETENTION_DAYS)
    result = conn.execute(
        text("DELETE FROM nudge_events WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return result.rowcount


def run_worker_log_retention(conn) -> int:
    """
    Delete worker_log entries older than WORKER_LOG_RETENTION_DAYS.

    NB: column is `started_at` (not `created_at`) — the original
    aggregation_worker.py had a typo that meant this retention job had
    been silently failing for months, deleting nothing and filling the
    error log. Fixed 2026-04-13 as part of the post-refactor bug sweep.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WORKER_LOG_RETENTION_DAYS)
    result = conn.execute(
        text("DELETE FROM worker_log WHERE started_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return result.rowcount
