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
# Keep terminal-status bugfix candidates only briefly — discarded/
# apply_failed are write-once analytical breadcrumbs, not load-bearing.
# Active candidates (open/analyzed/patch_proposed/applied/superseded)
# are NEVER pruned by this task — only terminal failure/discarded rows.
BUGFIX_CANDIDATE_RETENTION_DAYS = 30
# Reviewer assessments are write-once audit trail of every reviewer
# decision (propose/apply/promote). Useful for trend analysis but
# not load-bearing past 90d. Keeps the table bounded for the audit
# growth check.
REVIEWER_ASSESSMENT_RETENTION_DAYS = 90
# Sentry incident table is pipeline-driven; resolved incidents older
# than 60d are analytical breadcrumbs not load-bearing. Active
# incidents (resolved=False) are NEVER pruned.
SENTRY_INCIDENT_RETENTION_DAYS = 60

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
    """Return all shops to run retention for.

    Historical implementation did `SELECT DISTINCT shop_domain FROM events`
    which requires a full index-scan on events. At 10k merchants × 100M
    events this becomes a multi-second scan every 24h. The merchants table
    is the authoritative list of shops and is 4+ orders of magnitude smaller,
    so we query it directly. Uninstalled shops still have their events
    retention-cleaned because we keep the row (with uninstalled_at set)
    rather than deleting it.
    """
    result = conn.execute(
        text("SELECT shop_domain FROM merchants WHERE shop_domain IS NOT NULL")
    )
    return [row.shop_domain for row in result.fetchall()]


def run_event_retention(conn, now_ms: int) -> int:
    """
    Delete events older than RETENTION_DAYS in a single batch DELETE.

    Previously looped over N shops issuing N separate DELETEs (N+1 pattern).
    At 10k merchants this serialised retention across thousands of round-trips.
    A single DELETE with a plain timestamp filter hits the same index
    (timestamp DESC) and removes the N+1 entirely. Returns total rows deleted.
    """
    cutoff_ms = now_ms - (RETENTION_DAYS * 24 * 3_600 * 1_000)
    result = conn.execute(
        text("DELETE FROM events WHERE timestamp < :cutoff_ms"),
        {"cutoff_ms": cutoff_ms},
    )
    return result.rowcount


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


def run_sentry_incident_retention(conn) -> int:
    """Delete RESOLVED sentry_incidents older than
    SENTRY_INCIDENT_RETENTION_DAYS. Active incidents (any non-resolved
    status) are NEVER pruned — they're load-bearing for the triage
    pipeline. Born 2026-05-04 same audit_db_table_growth cycle that
    caught bugfix_candidates + reviewer_assessments earlier this
    session."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=SENTRY_INCIDENT_RETENTION_DAYS
    )
    result = conn.execute(
        text(
            """
            DELETE FROM sentry_incidents
            WHERE status = 'resolved'
              AND created_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    return result.rowcount


def run_reviewer_assessment_retention(conn) -> int:
    """Delete reviewer_assessments older than REVIEWER_ASSESSMENT_RETENTION_DAYS.

    The table is append-only audit trail — every propose/apply/promote
    cycle writes 1+ assessments. Bounded growth requires age-based pruning.
    Born 2026-05-04: audit_db_table_growth caught the table at 50 → 270
    rows (+440%) in a 24h window with no retention wired.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=REVIEWER_ASSESSMENT_RETENTION_DAYS
    )
    result = conn.execute(
        text(
            "DELETE FROM reviewer_assessments WHERE created_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    return result.rowcount


def run_bugfix_candidate_retention(conn) -> int:
    """Delete TERMINAL bugfix_candidates older than
    BUGFIX_CANDIDATE_RETENTION_DAYS. Terminal = discarded | apply_failed.

    Active states (open / analyzed / patch_proposed / applied /
    superseded) are NEVER pruned — they are load-bearing for the
    self-healing pipeline state machine. The 92.7% of candidates that
    accumulate forever are discarded triage breadcrumbs, kept long
    enough for short-term trend analysis but not forever.

    Born 2026-05-04: audit_db_table_growth caught bugfix_candidates
    growing 62 → 1294 (+1987%) over the first month of pipeline
    operation; no retention had been wired.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=BUGFIX_CANDIDATE_RETENTION_DAYS
    )
    result = conn.execute(
        text(
            """
            DELETE FROM bugfix_candidates
            WHERE status IN ('discarded', 'apply_failed')
              AND created_at < :cutoff
            """
        ),
        {"cutoff": cutoff},
    )
    return result.rowcount
