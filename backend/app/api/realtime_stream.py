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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.database import SessionLocal
from app.core.deps import require_pro_session

router = APIRouter(tags=["realtime"])
log = logging.getLogger(__name__)

_TICK_SECONDS = 10
_HEARTBEAT_INTERVAL = 25  # browsers timeout EventSource around 30s of silence
_MAX_TICKS_PER_CONNECTION = 360  # 1 hour at 10s ticks → forces clean reconnect


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_snapshot(shop: str) -> dict:
    """Compose one snapshot. Opens its own DB session for isolation."""
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
        except Exception:
            top = None

        return {
            "shop_domain": shop,
            "ts": _now_iso(),
            "fusion": {
                "alert_count": len(fusion.get("alerts", [])),
                "top_alert": (fusion.get("alerts") or [None])[0],
            },
            "causal_top": top,
        }
    finally:
        db.close()


async def _event_stream(shop: str):
    """Async generator yielding SSE-formatted bytes."""
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


@router.get("/pro/stream/dashboard")
async def stream_dashboard(shop: str = Depends(require_pro_session)):
    """
    SSE stream for the live dashboard. Browsers must use EventSource
    with credentials (`{ withCredentials: true }`) to send the session
    cookie.
    """
    return StreamingResponse(
        _event_stream(shop),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
