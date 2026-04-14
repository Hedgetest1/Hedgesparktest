"""
event_bus.py — Internal event bus (β6).

One API. One shape. Two possible backends (postgres today, clickhouse
future). Call sites never change.

Producer
--------
    emit(event_name, shop_domain, **kwargs) -> bool
        Fire an event. Fire-and-forget. Never raises.

    emit_batch(events: list[dict]) -> int
        Bulk insert for high-volume sources.

Consumer
--------
    query(shop_domain, event_name=None, since_ms=None, limit=1000)
        Query recent events for one shop. ClickHouse-friendly SQL.

    aggregate_by_source(shop_domain, window_days=7)
        Fast source breakdown — hot path for the dashboard.

Backends
--------
The default backend is "postgres" (AnalyticsEvent table). Set env var
EVENT_BUS_BACKEND=clickhouse to route to ClickHouse (not implemented
yet — we'll add the HTTP batcher when we migrate).

Retention
---------
We don't keep analytics events forever in Postgres — the table grows
too fast. The worker_watchdog now owns a monthly sweep that deletes
rows older than 90 days.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("event_bus")

_BACKEND = os.getenv("EVENT_BUS_BACKEND", "postgres").lower()
_RETENTION_DAYS = int(os.getenv("EVENT_BUS_RETENTION_DAYS", "90"))

# Allowed event names — prevents typos and namespace pollution.
# Add new event names here as features emit them.
_ALLOWED_EVENT_NAMES = frozenset({
    "page_view",
    "add_to_cart",
    "checkout_started",
    "checkout_completed",
    "nudge_shown",
    "nudge_clicked",
    "nudge_dismissed",
    "nudge_recovered",
    "signal_detected",
    "trust_action_executed",
    "goal_at_risk_detected",
    "rars_snapshot",
    "session_start",
    "session_end",
    "product_view",
    "search",
})


def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_db() -> Session:
    from app.core.database import SessionLocal
    return SessionLocal()


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------

def emit(
    event_name: str,
    shop_domain: str,
    *,
    visitor_id: str | None = None,
    session_id: str | None = None,
    source: str | None = None,
    campaign: str | None = None,
    product_url: str | None = None,
    revenue_eur: float | None = None,
    props: dict[str, Any] | None = None,
    db: Session | None = None,
) -> bool:
    """Emit a single analytics event. Fire-and-forget."""
    if event_name not in _ALLOWED_EVENT_NAMES:
        log.debug("event_bus: unknown event_name %s — dropped", event_name)
        return False

    row = {
        "ts_ms": _now_ms(),
        "event_name": event_name,
        "shop_domain": shop_domain,
        "visitor_id": visitor_id,
        "session_id": session_id,
        "source": source,
        "campaign": campaign,
        "product_url": product_url,
        "revenue_eur": revenue_eur,
        "props": props,
    }

    if _BACKEND == "postgres":
        return _emit_postgres(row, db=db)
    if _BACKEND == "clickhouse":
        # TODO when ClickHouse is installed: batched HTTP insert. For now
        # we refuse silently so the caller never blocks on a broken
        # future backend.
        log.warning("event_bus: clickhouse backend not yet implemented")
        return False

    log.warning("event_bus: unknown backend %s", _BACKEND)
    return False


def emit_batch(events: list[dict[str, Any]], db: Session | None = None) -> int:
    """Bulk-emit multiple events. Returns the number successfully written."""
    if not events:
        return 0
    rows = []
    now_ms = _now_ms()
    for e in events:
        name = e.get("event_name")
        if name not in _ALLOWED_EVENT_NAMES:
            continue
        shop = e.get("shop_domain")
        if not shop:
            continue
        rows.append(
            {
                "ts_ms": int(e.get("ts_ms") or now_ms),
                "event_name": name,
                "shop_domain": shop,
                "visitor_id": e.get("visitor_id"),
                "session_id": e.get("session_id"),
                "source": e.get("source"),
                "campaign": e.get("campaign"),
                "product_url": e.get("product_url"),
                "revenue_eur": e.get("revenue_eur"),
                "props": e.get("props"),
            }
        )

    if not rows:
        return 0
    return _emit_postgres_bulk(rows, db=db)


_EMIT_FAIL_REDIS_KEY = "hs:event_bus:emit_fail_count"
_EMIT_FAIL_ALERT_THRESHOLD = 20  # alert after 20 failures in a row


def _record_emit_failure() -> None:
    """Track consecutive failures; emit ops_alert if chronic."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("event_bus.emit_failure")
            return
        count = rc.incr(_EMIT_FAIL_REDIS_KEY)
        rc.expire(_EMIT_FAIL_REDIS_KEY, 3600)
        if count == _EMIT_FAIL_ALERT_THRESHOLD:
            from app.core.database import SessionLocal
            from app.services.alerting import write_alert
            db = SessionLocal()
            try:
                write_alert(
                    db,
                    severity="warning",
                    source="event_bus",
                    alert_type="event_bus_emit_chronic_failure",
                    summary=f"Event bus has failed {count} emits in the last hour — data pipeline degraded",
                    detail={"count": count, "threshold": _EMIT_FAIL_ALERT_THRESHOLD},
                )
                db.commit()
            finally:
                db.close()
    except Exception:
        pass


def _record_emit_success() -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(_EMIT_FAIL_REDIS_KEY)
    except Exception:
        pass


def _emit_postgres(row: dict[str, Any], db: Session | None = None) -> bool:
    close_after = False
    if db is None:
        db = _get_db()
        close_after = True
    try:
        import json as _json
        db.execute(
            sql_text(
                """
                INSERT INTO analytics_events
                    (ts_ms, event_name, shop_domain, visitor_id, session_id,
                     source, campaign, product_url, revenue_eur, props)
                VALUES
                    (:ts_ms, :event_name, :shop_domain, :visitor_id, :session_id,
                     :source, :campaign, :product_url, :revenue_eur,
                     CAST(:props AS JSONB))
                """
            ),
            {
                **row,
                "props": _json.dumps(row.get("props")) if row.get("props") else None,
            },
        )
        db.commit()
        _record_emit_success()
        return True
    except Exception as exc:
        log.debug("event_bus: postgres emit failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        _record_emit_failure()
        return False
    finally:
        if close_after:
            db.close()


def _emit_postgres_bulk(rows: list[dict], db: Session | None = None) -> int:
    close_after = False
    if db is None:
        db = _get_db()
        close_after = True
    try:
        import json as _json
        db.execute(
            sql_text(
                """
                INSERT INTO analytics_events
                    (ts_ms, event_name, shop_domain, visitor_id, session_id,
                     source, campaign, product_url, revenue_eur, props)
                VALUES
                    (:ts_ms, :event_name, :shop_domain, :visitor_id, :session_id,
                     :source, :campaign, :product_url, :revenue_eur,
                     CAST(:props AS JSONB))
                """
            ),
            [
                {**r, "props": _json.dumps(r.get("props")) if r.get("props") else None}
                for r in rows
            ],
        )
        db.commit()
        return len(rows)
    except Exception as exc:
        log.warning("event_bus: bulk emit failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        if close_after:
            db.close()


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------

def query(
    db: Session,
    shop_domain: str,
    event_name: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Query events for a shop. ClickHouse-friendly SQL."""
    clauses = ["shop_domain = :shop"]
    params: dict[str, Any] = {"shop": shop_domain, "limit": min(limit, 10000)}
    if event_name:
        clauses.append("event_name = :event_name")
        params["event_name"] = event_name
    if since_ms is not None:
        clauses.append("ts_ms >= :since_ms")
        params["since_ms"] = since_ms
    if until_ms is not None:
        clauses.append("ts_ms < :until_ms")
        params["until_ms"] = until_ms

    where_clause = " AND ".join(clauses)
    try:
        rows = db.execute(
            sql_text(
                f"""
                SELECT id, ts_ms, event_name, shop_domain, visitor_id, session_id,
                       source, campaign, product_url, revenue_eur, props
                FROM analytics_events
                WHERE {where_clause}
                ORDER BY ts_ms DESC
                LIMIT :limit
                """
            ),
            params,
        ).fetchall()
    except Exception as exc:
        log.warning("event_bus: query failed: %s", exc)
        return []

    return [
        {
            "id": r[0],
            "ts_ms": r[1],
            "event_name": r[2],
            "shop_domain": r[3],
            "visitor_id": r[4],
            "session_id": r[5],
            "source": r[6],
            "campaign": r[7],
            "product_url": r[8],
            "revenue_eur": r[9],
            "props": r[10],
        }
        for r in rows
    ]


def aggregate_by_source(
    db: Session, shop_domain: str, window_days: int = 7
) -> dict:
    """Fast source breakdown — hot path for dashboard cards."""
    since_ms = _now_ms() - window_days * 86400 * 1000
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT
                    COALESCE(source, 'unknown') AS source,
                    COUNT(*) AS event_count,
                    COUNT(DISTINCT visitor_id) AS visitors,
                    COALESCE(SUM(revenue_eur), 0) AS revenue_eur
                FROM analytics_events
                WHERE shop_domain = :s AND ts_ms >= :since
                GROUP BY source
                ORDER BY event_count DESC
                LIMIT 20
                """
            ),
            {"s": shop_domain, "since": since_ms},
        ).fetchall()
    except Exception as exc:
        log.warning("event_bus: aggregate failed: %s", exc)
        return {"sources": []}

    return {
        "shop_domain": shop_domain,
        "window_days": window_days,
        "sources": [
            {
                "source": r[0],
                "event_count": int(r[1] or 0),
                "visitors": int(r[2] or 0),
                "revenue_eur": float(r[3] or 0),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def cleanup_old_events(db: Session) -> int:
    """Delete analytics_events older than the retention window. Called by
    the agent_worker on a monthly cadence."""
    cutoff_ms = _now_ms() - _RETENTION_DAYS * 86400 * 1000
    try:
        result = db.execute(
            sql_text("DELETE FROM analytics_events WHERE ts_ms < :cutoff"),
            {"cutoff": cutoff_ms},
        )
        db.commit()
        return result.rowcount or 0
    except Exception as exc:
        log.warning("event_bus: retention cleanup failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
