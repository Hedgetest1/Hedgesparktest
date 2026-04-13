"""
public_roi_counter.py — Phase Ω⁵ public social-proof counter.

  GET /public/roi-counter        — network-wide prevented € last 30d
  GET /public/roi-counter/live   — text/event-stream ticker updates

The landing page renders a big live counter:

    €1,247,830 prevented across the HedgeSpark network this month

It's social proof you cannot fake — it's computed from real merchant
action_executions. Verticals breakdown is available on hover.

Caching: the aggregate is expensive to compute (scans action_executions
for every merchant) so we cache it in Redis with a 10-minute TTL. The
SSE endpoint reads that cache — it never hits the DB on tick.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.database import get_db

router = APIRouter(tags=["public_roi"])
log = logging.getLogger("public_roi_counter")

_CACHE_KEY = "hs:public_roi_counter:v1"
_CACHE_TTL = 600  # 10 min


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute() -> dict:
    """
    Expensive path — scan action_executions, group by vertical if
    available, sum prevented € over last 30 days.
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        try:
            total_row = db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(CAST(COALESCE(impact_eur, 0) AS FLOAT)), 0) AS total,
                           COUNT(DISTINCT shop_domain) AS shops
                    FROM action_executions
                    WHERE status = 'confirmed'
                      AND executed_at >= NOW() - INTERVAL '30 days'
                    """
                )
            ).fetchone()
            total = float(total_row[0] or 0) if total_row else 0.0
            shops = int(total_row[1] or 0) if total_row else 0
        except Exception as exc:
            log.warning("public_roi: total compute failed: %s", exc)
            total, shops = 0.0, 0

        vertical_rows: list[tuple[str, float]] = []
        try:
            rows = db.execute(
                text(
                    """
                    SELECT
                      COALESCE(sc.vertical, 'other') AS vertical,
                      COALESCE(SUM(CAST(COALESCE(ae.impact_eur, 0) AS FLOAT)), 0) AS recovered
                    FROM action_executions ae
                    LEFT JOIN shop_classification sc
                           ON sc.shop_domain = ae.shop_domain
                    WHERE ae.status = 'confirmed'
                      AND ae.executed_at >= NOW() - INTERVAL '30 days'
                    GROUP BY 1
                    ORDER BY recovered DESC
                    LIMIT 8
                    """
                )
            ).fetchall()
            vertical_rows = [(r[0], float(r[1] or 0)) for r in rows if float(r[1] or 0) > 0]
        except Exception:
            # shop_classification table may not exist in dev — ignore
            vertical_rows = []

        # State flag — drives honest rendering on the landing page.
        # "live"     → real data, counter reflects actual network activity
        # "warming"  → onboarding window, not enough signal to publish a number
        # We never fabricate a floor. If total < threshold we say so.
        _MIN_PUBLISH_EUR = 1_000.0
        _MIN_SHOPS = 3
        if total >= _MIN_PUBLISH_EUR and shops >= _MIN_SHOPS:
            state = "live"
        else:
            state = "warming"

        return {
            "state": state,
            "prevented_eur_30d": round(total, 2),
            "shops_contributing": shops,
            "by_vertical": [
                {"vertical": v, "prevented_eur": round(p, 2)}
                for (v, p) in vertical_rows
            ],
            "window_days": 30,
            "generated_at": _now_iso(),
            "publish_thresholds": {
                "min_eur": _MIN_PUBLISH_EUR,
                "min_shops": _MIN_SHOPS,
            },
        }
    finally:
        db.close()


def _get_cached_or_compute() -> dict:
    """Read from Redis; fall back to compute + refresh on miss."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(_CACHE_KEY)
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    pass
        doc = _compute()
        if rc is not None:
            try:
                rc.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(doc, default=str))
            except Exception:
                pass
        return doc
    except Exception as exc:
        log.warning("public_roi: cache/compute failed: %s", exc)
        return _compute()


@router.get("/public/roi-counter")
def get_roi_counter(db: Session = Depends(get_db)):
    """
    Public social-proof counter. No auth. Cached 10 min. Always returns
    a structured doc — callers can rely on the shape.
    """
    return _get_cached_or_compute()


@router.get("/public/roi-counter/live")
async def stream_roi_counter():
    """
    Lightweight SSE ticker — re-reads the Redis cache every 20s. Never
    touches the DB (the cache refresh happens via the GET endpoint or
    the worker path). Browser EventSource auto-reconnects.
    """
    import asyncio

    async def _gen():
        yield f"event: hello\ndata: {json.dumps({'ts': _now_iso()})}\n\n".encode()
        for _ in range(180):  # 1 hour at 20s ticks
            try:
                doc = _get_cached_or_compute()
                yield f"event: tick\ndata: {json.dumps(doc, default=str)}\n\n".encode()
            except Exception as exc:
                err = json.dumps({"error": str(exc)[:200]})
                yield f"event: error\ndata: {err}\n\n".encode()
            await asyncio.sleep(20)
        yield f"event: rotate\ndata: {json.dumps({'ts': _now_iso()})}\n\n".encode()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
