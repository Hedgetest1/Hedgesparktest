"""
p95_snapshot.py — A4 per-route p95 latency snapshot flusher.

Closes the gap left by `observability_spikes.detect_p95_slow_trends`
(which was a no-op until a data source existed).

Design
------
Every 5 minutes, the backend process flushes its in-memory request
latency histograms to Redis as per-(route, hour) buckets. Each bucket
records the p95 in ms + sample count across every 5-min tick within
that hour (so the hour's p95 is the max-observed p95 across ticks —
conservative, catches transient spikes).

Why Redis not DB
----------------
  - No schema migration needed
  - Cheap: 50 routes × 8 days × 24h = 9,600 keys, ~1MB total
  - Rolling window with TTL = auto-cleanup
  - Cross-process: backend writes, aggregation_worker reads

Trigger
-------
Opportunistic: the tracking middleware in main.py calls `maybe_flush()`
on each request. A Redis lock + last-flush timestamp guarantees at
most ONE flush per 5-min window across all uvicorn workers. No
asyncio/threading inside FastAPI.

Read path
---------
`observability_spikes.detect_p95_slow_trends` SCANs the
`hs:p95:{route}:{hour}` keyspace and compares last-24h vs prior 7-day
baseline.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("p95_snapshot")

_FLUSH_INTERVAL_SECONDS = 300   # 5 min
_LAST_FLUSH_KEY = "hs:p95:last_flush_ts"
_FLUSH_LOCK_KEY = "hs:p95:flush_lock"
_FLUSH_LOCK_TTL = 60            # 1 min safety — much less than flush interval
_BUCKET_TTL_SECONDS = 8 * 86400  # 8 days — covers 7d baseline + 1d buffer
_BUCKET_KEY = "hs:p95:{route}:{hour}"


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def _should_flush(rc) -> bool:
    """True when ≥ _FLUSH_INTERVAL_SECONDS have elapsed since last flush
    AND we can acquire the short-lived flush lock. Fail-closed on any
    Redis error (don't double-flush, don't crash the request)."""
    try:
        last = rc.get(_LAST_FLUSH_KEY)
        if last:
            try:
                last_ts = float(last.decode() if isinstance(last, bytes) else last)
                if time.time() - last_ts < _FLUSH_INTERVAL_SECONDS:
                    return False
            except (ValueError, TypeError):
                pass  # SILENT-EXCEPT-OK: malformed timestamp → treat as stale
        # Try to acquire flush lock — only ONE uvicorn worker should
        # actually do the write this tick.
        acquired = rc.set(_FLUSH_LOCK_KEY, "1", nx=True, ex=_FLUSH_LOCK_TTL)
        return bool(acquired)
    except Exception:
        return False


def _write_bucket(rc, route: str, hour: str, p95_ms: float, count: int) -> None:
    """Write (or merge) the per-(route, hour) bucket. If a bucket already
    exists for this hour, take the MAX p95 seen — conservative, catches
    transient spikes that would wash out in a mean."""
    key = _BUCKET_KEY.format(route=route, hour=hour)
    try:
        raw = rc.get(key)
        merged = {"p95_ms": float(p95_ms), "count": int(count), "hour": hour}
        if raw:
            try:
                prev = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                merged["p95_ms"] = max(float(p95_ms), float(prev.get("p95_ms", 0)))
                merged["count"] = int(count) + int(prev.get("count", 0))
            except Exception:
                pass  # SILENT-EXCEPT-OK: malformed prior value → overwrite with fresh
        rc.setex(key, _BUCKET_TTL_SECONDS, json.dumps(merged))
    except Exception as exc:
        log.warning("p95_snapshot: bucket write failed route=%s: %s", route, exc)


def maybe_flush() -> int:
    """Opportunistic flush — called from the request middleware. Returns
    the number of routes flushed (0 if skipped or nothing to flush)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("p95_snapshot.flush.no_client")
            return 0
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("p95_snapshot.flush.exception")
        return 0

    if not _should_flush(rc):
        return 0

    try:
        from app.core.metrics import compute_p95_per_route
        snapshot = compute_p95_per_route()
    except Exception as exc:
        log.warning("p95_snapshot: compute failed: %s", exc)
        return 0

    if not snapshot:
        # Nothing to flush, but still advance the timestamp so we don't
        # thrash _should_flush in this request window.
        try:
            rc.setex(_LAST_FLUSH_KEY, _FLUSH_INTERVAL_SECONDS * 2, str(time.time()))
        except Exception:
            pass  # SILENT-EXCEPT-OK: timestamp advance is best-effort
        return 0

    hour = _hour_key(datetime.now(timezone.utc).replace(tzinfo=None))
    for route, stats in snapshot.items():
        _write_bucket(rc, route, hour, stats["p95_ms"], stats["count"])

    try:
        rc.setex(_LAST_FLUSH_KEY, _FLUSH_INTERVAL_SECONDS * 2, str(time.time()))
    except Exception:
        pass  # SILENT-EXCEPT-OK: timestamp advance is best-effort

    log.info("p95_snapshot: flushed %d routes for hour=%s", len(snapshot), hour)
    return len(snapshot)


def iter_bucket_keys(rc, pattern: str = "hs:p95:*"):
    """SCAN yields bucket keys — non-blocking at any merchant count.
    Used by the reader (observability_spikes.detect_p95_slow_trends)."""
    cursor = 0
    while True:
        cursor, keys = rc.scan(cursor=cursor, match=pattern, count=500)
        for k in keys:
            yield k.decode("utf-8") if isinstance(k, bytes) else str(k)
        if cursor == 0:
            break


def load_route_history(rc, route: str, hours_back: int) -> list[dict]:
    """Return the most recent `hours_back` hourly buckets for a route,
    newest first. Empty list when route has no samples or Redis fails."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for h_offset in range(hours_back):
        hour_dt = now.replace(minute=0, second=0, microsecond=0)
        hour_dt = hour_dt.replace(hour=hour_dt.hour)  # noop — placeholder
        # Properly subtract h_offset hours via datetime arithmetic:
        from datetime import timedelta as _td
        hour_dt = now - _td(hours=h_offset)
        key = _BUCKET_KEY.format(
            route=route,
            hour=hour_dt.strftime("%Y-%m-%dT%H"),
        )
        try:
            raw = rc.get(key)
            if raw:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                out.append(json.loads(s))
        except Exception:
            continue
    return out
