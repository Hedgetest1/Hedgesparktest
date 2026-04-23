"""
realtime_stream.py — Phase Ω''' Server-Sent Events live dashboard.

  GET /pro/stream/dashboard  — text/event-stream

Streams a periodic snapshot of the merchant's anomaly fusion + RARS
delta + last fired event. Polls deterministically every 10 seconds and
emits SSE events with named types:

    event: snapshot
    data: {...}

    event: heartbeat
    data: ts

The endpoint hand-rolls SSE — no third-party dep. Auth piggybacks on
the existing pro session cookie; no API key, no separate token.

Why SSE not WebSockets:
  * One-way (server → client) is enough for a dashboard
  * Built into every browser EventSource, zero handshake
  * Survives proxies and TLS termination cleanly
  * No reconnect logic to write — browser handles it
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.database import SessionLocal
from app.core.deps import require_pro_session

router = APIRouter(tags=["realtime"])
log = logging.getLogger(__name__)

_TICK_SECONDS = 10
_HEARTBEAT_INTERVAL = 25  # browsers timeout EventSource around 30s of silence
_MAX_TICKS_PER_CONNECTION = 360  # 1 hour at 10s ticks → forces clean reconnect

# Connection-count gate — SSE holds a DB session + coroutine per client.
# At 10k merchants we cannot let this grow unbounded, so we cap per-process
# live connections and cache snapshots so a second concurrent viewer of the
# same shop reuses the previous tick's payload instead of re-querying.
_MAX_LIVE_CONNECTIONS = 500
_SNAPSHOT_CACHE_TTL_S = 8.0  # must be < _TICK_SECONDS so we refresh each tick
_active_connections = 0
# multi-worker: redis-backed — Redis primary at hs:liverts:snap:{shop} with
# 8s TTL, cross-worker coherent. In-process dict kept only as the
# Redis-unavailable fallback path (single-worker and Redis-outage safe).
_snapshot_cache: dict[str, tuple[float, dict]] = {}
_SNAPSHOT_REDIS_PREFIX = "hs:liverts:snap:v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_snapshot(shop: str) -> dict:
    """Compose one snapshot. Opens its own DB session for isolation.

    Cached per-shop for _SNAPSHOT_CACHE_TTL_S so that multiple concurrent
    viewers of the same dashboard share one heavy query pass per tick.
    Cache is Redis-primary (cross-worker coherent); falls back to the
    per-process dict when Redis is unreachable.
    """
    # Redis primary
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(f"{_SNAPSHOT_REDIS_PREFIX}:{shop}")
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    pass  # SILENT-EXCEPT-OK: corrupt entry falls through to rebuild
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("realtime_stream.snapshot_read.redis_error")

    # In-process fallback (single-worker or Redis outage)
    now = time.monotonic()
    cached = _snapshot_cache.get(shop)
    if cached is not None and (now - cached[0]) < _SNAPSHOT_CACHE_TTL_S:
        return cached[1]

    db: Session = SessionLocal()
    try:
        try:
            from app.services.anomaly_fusion import fuse
            fusion = fuse(db, shop)
        except Exception as exc:
            fusion = {"error": str(exc)[:200]}

        try:
            from app.services.causal_explainer import explain
            causal = explain(db, shop)
            top = (causal.get("hypotheses") or [None])[0]
            causal_narrative = causal.get("narrative")
        except Exception:
            top = None
            causal_narrative = None

        # Vertical benchmarks — just a lightweight signature (total recovery
        # potential) so the card knows when to re-pull the full payload.
        try:
            from app.services.benchmarks_vertical import get_vertical_benchmark_report
            vb = get_vertical_benchmark_report(db, shop) or {}
            vb_sig = {
                "total_recovery_eur": float(vb.get("total_recovery_potential_eur") or 0),
                "peer_count": int(vb.get("peer_count") or 0),
                "scope": vb.get("scope"),
            }
        except Exception:
            vb_sig = None

        # Night shift status — cheap read from Redis only
        try:
            from app.services.night_shift_agent import get_latest_for_shop
            ns = get_latest_for_shop(shop) or {}
            ns_sig = {
                "status": ns.get("status"),
                "headline": ns.get("headline"),
                "sleep_confidence": ns.get("sleep_confidence"),
                "day": ns.get("day"),
            } if ns else None
        except Exception:
            ns_sig = None

        snapshot = {
            "shop_domain": shop,
            "ts": _now_iso(),
            "fusion": {
                "alert_count": len(fusion.get("alerts", [])),
                "top_alert": (fusion.get("alerts") or [None])[0],
            },
            "causal_top": top,
            "causal_narrative": causal_narrative,
            "benchmarks": vb_sig,
            "night_shift": ns_sig,
        }
        # Redis write (primary) + in-process cache (fallback)
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                rc.setex(
                    f"{_SNAPSHOT_REDIS_PREFIX}:{shop}",
                    int(_SNAPSHOT_CACHE_TTL_S) + 1,
                    json.dumps(snapshot, default=str),
                )
        except Exception:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("realtime_stream.snapshot_write.redis_error")

        _snapshot_cache[shop] = (time.monotonic(), snapshot)
        # Bound cache size — LRU-style eviction, cheap at this scale
        if len(_snapshot_cache) > 2000:
            oldest = sorted(_snapshot_cache.items(), key=lambda kv: kv[1][0])[:500]
            for k, _ in oldest:
                _snapshot_cache.pop(k, None)
        return snapshot
    finally:
        db.close()


async def _event_stream(shop: str):
    """Async generator yielding SSE-formatted bytes."""
    global _active_connections
    _active_connections += 1
    try:
        yield f"event: hello\ndata: {json.dumps({'ts': _now_iso()})}\n\n".encode()

        ticks = 0
        last_heartbeat = 0.0
        while ticks < _MAX_TICKS_PER_CONNECTION:
            try:
                snapshot = _build_snapshot(shop)
                payload = json.dumps(snapshot, default=str)
                yield f"event: snapshot\ndata: {payload}\n\n".encode()
            except Exception as exc:
                err = json.dumps({"error": str(exc)[:200]})
                yield f"event: error\ndata: {err}\n\n".encode()

            # Heartbeat every 25s to keep proxies + browsers happy
            await asyncio.sleep(_TICK_SECONDS)
            last_heartbeat += _TICK_SECONDS
            if last_heartbeat >= _HEARTBEAT_INTERVAL:
                yield f"event: heartbeat\ndata: {_now_iso()}\n\n".encode()
                last_heartbeat = 0.0

            ticks += 1

        # Force browser to reconnect after the cap — keeps memory bounded
        yield f"event: rotate\ndata: {json.dumps({'reason': 'tick_cap', 'ts': _now_iso()})}\n\n".encode()
    finally:
        _active_connections -= 1


@router.get("/pro/stream/dashboard", include_in_schema=False)
async def stream_dashboard(shop: str = Depends(require_pro_session)):
    """
    SSE stream for the live dashboard. Browsers must use EventSource
    with credentials (`{ withCredentials: true }`) to send the session
    cookie.
    """
    if _active_connections >= _MAX_LIVE_CONNECTIONS:
        raise HTTPException(
            status_code=503,
            detail="Live stream at capacity — please fall back to polling.",
            headers={"Retry-After": "30"},
        )
    return StreamingResponse(
        _event_stream(shop),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
