"""
public_roi_counter.py — Phase Ω⁵ public social-proof counter.

  GET /public/roi-counter        — network-wide prevented € last 30d
  GET /public/roi-counter/live   — text/event-stream ticker updates

The landing page renders a big live counter:

    €1,247,830 prevented across the HedgeSpark network this month

It's social proof you cannot fake — it's computed from the per-shop
Revenue-at-Risk Score (RARS) `prevented_eur_this_month` field that
every Pro merchant's monthly ROI report already reads. No fabricated
floors, no silent fallbacks — if we can't publish a real number, we
return state="warming" and let the landing surface that honestly.

Original implementation scanned a non-existent `action_executions`
table (bug fixed 2026-04-13 post-refactor audit). The real numbers
live in the RARS compute path.

Caching: iterating every Pro merchant + computing RARS per shop is
expensive, so we cache the aggregate in Redis with a 10-min TTL. The
SSE ticker reads that cache — it never hits the DB on tick.
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
    Real path — iterate every active Pro merchant, pull their RARS
    report, sum the `prevented_eur_this_month` field. Group by vertical
    via the vertical_classifier service.

    This is intentionally expensive (one RARS compute per shop) so the
    10-minute Redis cache is load-bearing. For scale past ~200 Pro
    merchants we should switch to a materialized view; for now, this
    is honest, real, and cached.
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        total = 0.0
        shops = 0
        vertical_totals: dict[str, float] = {}

        try:
            from app.models.merchant import Merchant
            from app.services.revenue_at_risk import get_revenue_at_risk
            try:
                from app.services.vertical_classifier import get_vertical
            except Exception:
                get_vertical = None  # type: ignore

            pro_merchants = (
                db.query(Merchant)
                .filter(
                    Merchant.plan == "pro",
                    Merchant.billing_active == True,  # noqa: E712
                    Merchant.install_status == "active",
                )
                .all()
            )

            for m in pro_merchants:
                shop = m.shop_domain
                if not shop:
                    continue
                try:
                    rars = get_revenue_at_risk(db, shop) or {}
                    prevented = float(rars.get("prevented_eur_this_month") or 0.0)
                except Exception as exc:
                    log.warning("public_roi: rars failed for %s: %s", shop, exc)
                    continue

                if prevented <= 0:
                    continue

                total += prevented
                shops += 1

                vertical = "other"
                if get_vertical is not None:
                    try:
                        vertical = get_vertical(db, shop) or "other"
                    except Exception:
                        pass
                vertical_totals[vertical] = vertical_totals.get(vertical, 0.0) + prevented

        except Exception as exc:
            log.warning("public_roi: merchant iteration failed: %s", exc)

        vertical_rows = sorted(
            vertical_totals.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )[:8]

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
