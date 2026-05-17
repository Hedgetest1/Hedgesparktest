"""J3-part-2 — async analytics-event buffer + batched-COPY drain.

Born 2026-05-17. J3-part-1 (ingest_admission) BOUNDS the 10k write
cascade by capping concurrency + shedding; this part lets the system
ABSORB the full volume instead of shedding it: the dominant write
volume (non-purchase storefront analytics — product_view / dwell /
scroll / add_to_cart, which outnumber purchases by orders of
magnitude) no longer does a synchronous per-event INSERT on the
request path. The request RPUSHes a compact JSON into Redis (O(1), no
DB conn) and returns; a single background drain task bulk-INSERTs
batches via psycopg2 `execute_values` holding ONE pooled conn per
batch — so the events INSERT no longer competes for the request pool
AT ALL. The pool-cascade is then structurally impossible for the
dominant volume, not merely bounded.

CORRECTNESS BOUNDARY (non-negotiable, §0): ONLY non-purchase
analytics events are buffered. Purchase events (revenue / order
attribution) keep the FULL synchronous path in track.py — they are
low-volume and MUST NOT be at-risk of buffer loss. This module never
sees a purchase.

Durability: fire-and-forget analytics (the storefront tracker is
best-effort by design). A backend crash loses at-most the un-drained
buffer tail; the buffer is length-capped (drop-oldest on overflow —
shedding the OLDEST keeps the freshest signal) so it can never grow
unbounded in RAM. This is the correct trade for analytics; it would
be WRONG for revenue (hence the boundary above).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("ingest_buffer")

_BUF_KEY = "hs:ingest:buf"
# Hard cap on buffered analytics events. At 10k merchants a transient
# drain stall must not OOM the box; dropping the OLDEST analytics
# events (LTRIM keeps the newest _BUF_MAX) is the correct shed — fresh
# signal > stale signal for analytics. Env kill/scale knob (§2 r11).
_BUF_MAX = int(os.getenv("INGEST_BUFFER_MAX", "200000"))
_DRAIN_BATCH = int(os.getenv("INGEST_DRAIN_BATCH", "1000"))

# Exactly the Event(...) constructor kwargs track.py builds — kept in
# ONE place so a column drift is a single-site change.
_EVENT_FIELDS = (
    "shop_domain", "visitor_id", "event_type", "url", "product_url",
    "timestamp", "dwell_seconds", "max_scroll_depth", "source_type",
    "referrer", "utm_medium", "utm_source", "utm_campaign",
    "utm_content", "utm_term", "click_id", "landing_page",
    "product_id", "device_type",
)


def enqueue_event(fields: dict) -> bool:
    """RPUSH a compact analytics-event dict onto the Redis buffer.
    O(1), NO DB connection. Returns False (shed, never raises — the
    request path must never fail on analytics) if Redis is down or the
    buffer is at the cap. Caller already passed J3-part-1 admission +
    all security/consent gates."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("ingest_buffer.enqueue_redis_down")
            return False
        # Trim-to-cap is best-effort: a small overshoot between LLEN and
        # the next drain is harmless (the drain catches up); the point
        # is bounded RAM, not exactness.
        if rc.llen(_BUF_KEY) >= _BUF_MAX:
            rc.ltrim(_BUF_KEY, -(_BUF_MAX // 2), -1)  # drop oldest half
            from app.core.silent_fallback import record_silent_return
            record_silent_return("ingest_buffer.overflow_trim")
        rc.rpush(_BUF_KEY, json.dumps(
            {k: fields.get(k) for k in _EVENT_FIELDS},
            separators=(",", ":"), default=str))
        return True
    except Exception as exc:
        log.warning("ingest_buffer: enqueue failed (shed): %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("ingest_buffer.enqueue_error")
        return False


def _take_batch(rc, n: int) -> list[dict]:
    """Atomically pop up to n events via `LPOP key count` (Redis ≥6.2,
    deployed 7.0). ONE atomic op ⟹ even an accidental concurrent
    drainer can never double-read a batch (each LPOP-count pops a
    disjoint slice) — no drain lock needed, leanest correct form."""
    raw = rc.lpop(_BUF_KEY, n)
    if not raw:
        return []
    if isinstance(raw, (bytes, str)):       # count=1 edge → scalar
        raw = [raw]
    out: list[dict] = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception as exc:
            # A single corrupt entry must not stall the drain — but
            # NOT silently (§2 r2 zero-silent-sinks): log + count it.
            log.warning("ingest_buffer: dropped 1 corrupt buffer entry: %s", exc)
    return out


def drain_events(max_total: int | None = None) -> int:
    """Drain buffered analytics events into `events` via batched
    `execute_values` (ONE pooled conn per batch, ONE txn). Also batches
    the visitor upsert (distinct (visitor_id, shop_domain) per batch).
    Returns the number of events written. Called by the drain worker
    task — NEVER from a request. Own session (worker-style, NOT a
    request dep — audit_workers_no_request_db_dep invariant)."""
    from app.core.database import SessionLocal
    from app.core.redis_client import _client
    rc = _client()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("ingest_buffer.drain_redis_down")
        return 0
    written = 0
    cap = max_total if max_total is not None else (_DRAIN_BATCH * 20)
    while written < cap:
        batch = _take_batch(rc, _DRAIN_BATCH)
        if not batch:
            break
        db = SessionLocal()
        try:
            raw = db.connection().connection  # psycopg2 raw conn
            cur = raw.cursor()
            from psycopg2.extras import execute_values
            # Batched visitor upsert — distinct pairs only; ON CONFLICT
            # bumps last_seen (same effect as _upsert_visitor, set-based).
            vpairs = sorted({
                (e.get("visitor_id"), e.get("shop_domain")) for e in batch
                if e.get("visitor_id") and e.get("shop_domain")
            })
            if vpairs:
                execute_values(cur, (
                    "INSERT INTO visitors (visitor_id, shop_domain, "
                    "first_seen, last_seen) VALUES %s "
                    "ON CONFLICT (visitor_id, shop_domain) DO UPDATE "
                    "SET last_seen = EXCLUDED.last_seen"
                ), [(v, s) for (v, s) in vpairs],
                    template="(%s,%s,now(),now())")
            execute_values(cur, (
                f"INSERT INTO events ({','.join(_EVENT_FIELDS)}) VALUES %s"
            ), [tuple(e.get(f) for f in _EVENT_FIELDS) for e in batch])
            raw.commit()
            written += len(batch)
        except Exception as exc:
            try:
                db.rollback()
            except Exception as rb_exc:
                # Rollback-of-a-failed-batch failing is best-effort
                # (session is closed in finally regardless) but NOT
                # silent (§2 r2 zero-silent-sinks).
                log.warning("ingest_buffer: drain rollback failed: %s", rb_exc)
            log.warning("ingest_buffer: drain batch failed "
                        "(%d events dropped this batch): %s",
                        len(batch), exc)
        finally:
            db.close()
    return written


def buffer_depth() -> int:
    """Current buffered (un-drained) event count — for the drain task
    log + a future backlog alarm. Best-effort, 0 on Redis down."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        return int(rc.llen(_BUF_KEY)) if rc is not None else 0
    except Exception:
        return 0
