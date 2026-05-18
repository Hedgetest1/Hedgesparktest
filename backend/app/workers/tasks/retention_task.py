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
import os
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

_log = logging.getLogger("worker.aggregation.retention")

RETENTION_DAYS = 90
# J4-part-1 (jewel-structure, 2026-05-18, design-led + verified by an
# independent Agent): fully-aged WHOLE monthly `events` partitions are
# DROPPED (instant metadata op, zero dead tuples / vacuum pressure)
# instead of row-by-row DELETEd. The straddle partition + events_default
# still go through the unchanged batched row-DELETE (a complete
# superset fallback ⟹ correctness holds even if the drop phase is
# skipped). Bound format observed on PG 15.17: FOR VALUES
# FROM ('<int-ms>') TO ('<int-ms>'); partition key = RANGE("timestamp")
# (bigint epoch-ms), half-open [a,b). DROP-safe iff b <= cutoff (every
# row the child can hold has timestamp < b <= cutoff ⟹ already past
# retention; an in-window row cannot route into such a child). Circuit
# breaker caps drops/run so a clock-skew/pathological cutoff cannot
# nuke many months. events_default + unparseable bounds NEVER match.
_RETENTION_MAX_PARTITION_DROPS = int(
    os.getenv("RETENTION_MAX_PARTITION_DROPS", "12"))
_PARTITION_BOUND_RE = re.compile(r"FROM \('(-?\d+)'\) TO \('(-?\d+)'\)")
_PARTITION_NAME_RE = re.compile(r"\Aevents_y\d{4}m\d{2}\Z")
NUDGE_EVENT_RETENTION_DAYS = 60
WORKER_LOG_RETENTION_DAYS = 30
# Sentry incident table is pipeline-driven; resolved incidents older
# than 60d are analytical breadcrumbs not load-bearing. Active
# incidents (resolved=False) are NEVER pruned.
SENTRY_INCIDENT_RETENTION_DAYS = 60

_RETENTION_INTERVAL_S = 86_400  # once per 24h

# Module-level state — owned here, no longer in aggregation_worker.
_last_retention_run: float | None = None

# ---------------------------------------------------------------------------
# Batched-delete invariant (10k structural)
#
# Truth (2026-05-16): every retention DELETE here was a SINGLE unbatched
# statement, and aggregation_worker.py wrapped all four in ONE
# transaction (one outer conn.commit()). At 10k merchants the `events`
# table is ~100M rows AND is the storefront tracker's hot-path INSERT
# target. A single `DELETE FROM events WHERE timestamp < cutoff` over a
# 90-day backlog is a multi-minute transaction that (a) takes row locks
# on millions of rows, (b) generates a WAL spike, (c) holds the xmin
# horizon back so autovacuum cannot reclaim dead tuples table-wide —
# degrading event ingestion for EVERY merchant for the duration. The
# prior comment claimed the single DELETE "removed the N+1"; it traded
# an N+1 for an unbounded long-txn hot-path stall.
#
# Structural fix (NOT a band-aid): the proven in-repo pattern from
# app/services/data_retention.py — an id-scoped `LIMIT` sub-select,
# COMMIT per batch (bounds txn + lock duration to ~batch_size rows),
# bounded by a circuit-breaker iteration cap (§2 rule 8). Partial
# progress is retained on interruption and resumes next cycle (correct
# for retention). Locked by scripts/audit_retention_batched.py.
# ---------------------------------------------------------------------------
_RETENTION_BATCH_SIZE = int(os.getenv("RETENTION_DELETE_BATCH_SIZE", "5000"))
# 50000 × 5000 = 250M-row hard ceiling per run — generous for steady
# state, a real stop for a pathological cutoff (e.g. clock skew). On
# hit it logs + resumes next cycle rather than looping unbounded.
_RETENTION_MAX_BATCHES = int(os.getenv("RETENTION_DELETE_MAX_BATCHES", "50000"))


def _run_batched(conn, stmt, params: dict, *, label: str) -> int:
    """Run a self-limiting batched DELETE to completion.

    `stmt` MUST be a DELETE whose victim set is bounded by an id-scoped
    sub-select ending in `ORDER BY id LIMIT :_lim` (so each execution
    deletes at most _RETENTION_BATCH_SIZE rows). Commits per batch so a
    huge backlog never holds a long transaction or a table-wide lock on
    the hot-path table. Returns total rows deleted. Best-effort: the
    caller's existing try/except keeps a partial sweep non-fatal and the
    next cycle resumes from where this left off.
    """
    total = 0
    p = dict(params)
    p["_lim"] = _RETENTION_BATCH_SIZE
    for _ in range(_RETENTION_MAX_BATCHES):
        n = conn.execute(stmt, p).rowcount or 0
        conn.commit()  # bound txn + lock duration to one batch
        total += n
        if n < _RETENTION_BATCH_SIZE:
            return total
    _log.warning(
        "retention[%s]: circuit breaker hit (%d batches, %d rows) — "
        "backlog exceeds one run, resuming next cycle",
        label, _RETENTION_MAX_BATCHES, total,
    )
    return total


def cleanup_expired_signals(conn) -> int:
    """
    Delete opportunity_signals rows whose hard expiry has passed.
    Runs every cycle; batched id-scoped so a post-downtime backlog
    never stalls the cycle (steady state = one short batch).
    """
    return _run_batched(
        conn,
        text(
            "DELETE FROM opportunity_signals WHERE id IN ("
            "SELECT id FROM opportunity_signals "
            "WHERE expires_at < now() ORDER BY id LIMIT :_lim)"
        ),
        {},
        label="opportunity_signals",
    )


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
    # operator-filter: retention sweep MUST clean every merchant's
    # events including operator/dev tenants — GDPR retention applies
    # uniformly. Including operator shops here is correct.
    result = conn.execute(
        text("SELECT shop_domain FROM merchants WHERE shop_domain IS NOT NULL")
    )
    return [row.shop_domain for row in result.fetchall()]


def _enumerate_partition_bounds(conn) -> list[tuple[str, int]]:
    """(child_name, upper_bound_ms) for every PARSEABLE non-DEFAULT
    range child of `events`. DEFAULT (catch-all) and any bound not
    matching the observed PG-15 format are EXCLUDED — fail-safe ⟹
    their aged rows are reclaimed by the unchanged row-DELETE."""
    rows = conn.execute(text(
        "SELECT c.relname AS name, "
        "pg_get_expr(c.relpartbound, c.oid) AS bound "
        "FROM pg_inherits i "
        "JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent "
        "WHERE p.relname = 'events' AND c.relkind = 'r'"
    )).fetchall()
    out: list[tuple[str, int]] = []
    for r in rows:
        b = r.bound or ""
        if b == "DEFAULT":
            continue  # never drop the catch-all
        m = _PARTITION_BOUND_RE.search(b)
        if not m:
            continue  # unparseable ⟹ fail-safe: row-DELETE handles it
        out.append((r.name, int(m.group(2))))  # group(2) = upper bound
    return out


def _detach_then_drop(name: str) -> None:
    """Lock-minimal drop of ONE fully-aged child. `DROP TABLE` on an
    attached child AccessExclusive-locks the PARENT `events` (stalls
    all 10k ingest) — REJECTED. Correct (PG 14+, verified 15.17):
    `DETACH PARTITION ... CONCURRENTLY` takes only
    ShareUpdateExclusive on the parent (does NOT block INSERT/SELECT),
    then `DROP TABLE` the now-standalone child (locks only itself).
    DETACH CONCURRENTLY cannot run in a txn block ⟹ autocommit raw
    conn. Idempotent: a crash between detach and drop leaves a
    standalone table ⟹ next run's DETACH raises 'is not a partition'
    (tolerated) ⟹ DROP IF EXISTS reaps it. Strict name fullmatch =
    defence-in-depth (name is already a filtered pg_class.relname —
    no injection surface)."""
    if not _PARTITION_NAME_RE.match(name):
        raise ValueError(f"refusing DDL on non-events-partition: {name!r}")
    from app.core.database import engine
    raw = engine.raw_connection()
    try:
        # DETACH CONCURRENTLY forbids a txn block. `raw` is a
        # SQLAlchemy _ConnectionFairy: setting autocommit on the fairy
        # itself is silent attribute-shadowing (no descriptor; never
        # reaches psycopg2) ⟹ DETACH would raise ActiveSqlTransaction
        # and J4 would be silently dead. It MUST be set on the real
        # DBAPI connection via .driver_connection (independent Agent
        # a28854e empirically verified this form makes the
        # same-constraint DDL succeed).
        raw.driver_connection.autocommit = True
        cur = raw.cursor()
        try:
            cur.execute(
                f'ALTER TABLE events DETACH PARTITION "{name}" CONCURRENTLY')
        except Exception as exc:
            if "not a partition" not in str(exc).lower():
                raise  # a real failure — surface (caller fail-safes)
        cur.execute(f'DROP TABLE IF EXISTS "{name}"')
    finally:
        raw.close()


def _drop_fully_aged_event_partitions(conn, cutoff_ms: int) -> int:
    """DROP every WHOLE `events` child whose entire range is < the
    retention cutoff. Double clock guard: a forward-skewed APP clock
    cannot widen the drop set past what the DB's OWN now() agrees is
    aged. Circuit-breaker capped. FULLY fail-safe — ANY error logs +
    returns 0; the unchanged batched row-DELETE that runs AFTER this
    is a complete superset fallback (cleans straddle + events_default
    + anything this skipped). Returns #partitions dropped."""
    try:
        db_now_ms = int(conn.execute(text(
            "SELECT (extract(epoch from now())*1000)::bigint")).scalar())
        guard_ms = db_now_ms - (RETENTION_DAYS * 86_400_000) - 86_400_000
        effective = min(cutoff_ms, guard_ms)  # app-clock-skew guard
        droppable = sorted(
            n for (n, hi) in _enumerate_partition_bounds(conn)
            if hi <= effective)
        dropped = 0
        for name in droppable[:_RETENTION_MAX_PARTITION_DROPS]:
            _detach_then_drop(name)
            dropped += 1
            _log.info("retention[events]: dropped fully-aged partition "
                      "%s (upper bound <= %d)", name, effective)
        if len(droppable) > _RETENTION_MAX_PARTITION_DROPS:
            _log.warning(
                "retention[events]: %d droppable but breaker cap %d — "
                "remainder next run", len(droppable),
                _RETENTION_MAX_PARTITION_DROPS)
        return dropped
    except Exception:
        _log.exception(
            "retention[events]: partition-drop phase failed; the "
            "batched row-DELETE fallback still reclaims all aged rows")
        return 0


def run_event_retention(conn, now_ms: int) -> int:
    """
    Delete events older than RETENTION_DAYS, batched + committed per
    batch so the 100M-row hot-path `events` table is never stalled by a
    long retention transaction at 10k scale.

    History: an earlier change replaced an N+1 (one DELETE per shop)
    with ONE unbatched table-wide DELETE. That removed the round-trips
    but introduced an unbounded long-txn that holds row locks + the
    xmin horizon during the sweep — degrading tracker ingestion for
    every merchant. This batches it (id-scoped LIMIT, same `timestamp`
    index) — the round-trips stay collapsed AND the txn stays short.
    Returns total rows deleted.
    """
    cutoff_ms = now_ms - (RETENTION_DAYS * 24 * 3_600 * 1_000)
    # J4-part-1: DROP fully-aged WHOLE partitions FIRST (instant
    # metadata op, no row scan, no dead tuples). Fail-safe by
    # construction — never raises; the batched row-DELETE below is a
    # complete superset fallback that still cleans the straddle
    # partition + events_default + anything the drop phase skipped.
    # The DELETE literal is UNCHANGED ⟹ audit_retention_batched green.
    _drop_fully_aged_event_partitions(conn, cutoff_ms)
    return _run_batched(
        conn,
        text(
            "DELETE FROM events WHERE id IN ("
            "SELECT id FROM events WHERE timestamp < :cutoff_ms "
            "ORDER BY id LIMIT :_lim)"
        ),
        {"cutoff_ms": cutoff_ms},
        label="events",
    )


def run_nudge_event_retention(conn) -> int:
    """Delete nudge_events older than NUDGE_EVENT_RETENTION_DAYS
    (batched + commit-per-batch — nudge_events is high-volume at 10k)."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=NUDGE_EVENT_RETENTION_DAYS)
    return _run_batched(
        conn,
        text(
            "DELETE FROM nudge_events WHERE id IN ("
            "SELECT id FROM nudge_events WHERE created_at < :cutoff "
            "ORDER BY id LIMIT :_lim)"
        ),
        {"cutoff": cutoff},
        label="nudge_events",
    )


def run_worker_log_retention(conn) -> int:
    """
    Delete worker_log entries older than WORKER_LOG_RETENTION_DAYS.

    NB: column is `started_at` (not `created_at`) — the original
    aggregation_worker.py had a typo that meant this retention job had
    been silently failing for months, deleting nothing and filling the
    error log. Fixed 2026-04-13 as part of the post-refactor bug sweep.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WORKER_LOG_RETENTION_DAYS)
    return _run_batched(
        conn,
        text(
            "DELETE FROM worker_log WHERE id IN ("
            "SELECT id FROM worker_log WHERE started_at < :cutoff "
            "ORDER BY id LIMIT :_lim)"
        ),
        {"cutoff": cutoff},
        label="worker_log",
    )


def run_sentry_incident_retention(conn) -> int:
    """Delete RESOLVED sentry_incidents older than
    SENTRY_INCIDENT_RETENTION_DAYS. Active incidents (any non-resolved
    status) are NEVER pruned — they're load-bearing for the triage
    pipeline. Born 2026-05-04 same audit_db_table_growth cycle."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=SENTRY_INCIDENT_RETENTION_DAYS
    )
    return _run_batched(
        conn,
        text(
            "DELETE FROM sentry_incidents WHERE id IN ("
            "SELECT id FROM sentry_incidents "
            "WHERE status = 'resolved' AND created_at < :cutoff "
            "ORDER BY id LIMIT :_lim)"
        ),
        {"cutoff": cutoff},
        label="sentry_incidents",
    )


