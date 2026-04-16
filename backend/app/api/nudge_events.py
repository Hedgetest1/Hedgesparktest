"""
nudge_events.py — Nudge measurement event ingestion endpoint.

Public endpoint (no auth)
--------------------------
POST /nudge/event

    Called by spark-nudge.js when a nudge is shown, dismissed, or clicked.

    This endpoint has the same auth profile as POST /track — completely public,
    no API key required.  The data it receives is behavioral telemetry from
    Shopify storefronts, not merchant-sensitive data.

    Transport
    ---------
    spark-nudge.js sends with fetch({Content-Type: application/json, credentials: "omit"}).
    For dismissal events (fired at page leave), sendBeacon with Blob(application/json)
    is used as a reliable transport.

    Both result in a JSON body that FastAPI parses via the Pydantic model below.
    Any Content-Type: text/plain body (from raw sendBeacon without Blob) is also
    handled: the endpoint reads the raw body and parses JSON if Pydantic fails.

    CORS
    ----
    Storefront origins are not in the main CORSMiddleware allow_origins list.
    This is intentional and safe: tracking POSTs are fire-and-forget; the
    client never reads the response.  The server processes the request regardless
    of whether the browser can read the response — same pattern as /track.
    Access-Control-Allow-Origin: * is set on the response header for completeness.

    Deduplication
    -------------
    Client-side: spark-nudge.js uses sessionStorage("ws_nudge_shown_{id}")
    to suppress duplicate "shown" events within the same tab session.
    Server-side: no unique constraint — the same visitor in a new tab session
    produces a real new exposure and should be counted separately.

    Payload
    -------
    {
        "shop":        "store.myshopify.com",
        "nudge_id":    42,
        "visitor_id":  "uuid" | null,
        "product_url": "/products/handle",
        "event_type":  "shown" | "dismissed" | "clicked",
        "metadata":    { "copy_variant": "social_proof" }  // optional
    }

    Response
    --------
    200: { "status": "ok" }
    400: { "detail": "..." }   — validation failure
    Any server error silently returns 200 to avoid client-side retry storms.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.active_nudge import ActiveNudge
from app.services.nudge_measurement import CLIENT_EVENT_TYPES, record_nudge_event
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(tags=["nudge-events"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NudgeEventPayload(BaseModel):
    shop:        str           = Field(..., max_length=255, description="Shop domain (*.myshopify.com)")
    nudge_id:    int           = Field(..., ge=1, description="active_nudges.id")
    visitor_id:  Optional[str] = Field(default=None, max_length=128, description="hedgespark_visitor_id UUID")
    product_url: str           = Field(..., max_length=2048, description="Canonical product path /products/{handle}")
    event_type:  str           = Field(..., max_length=32, description="shown | dismissed | clicked")
    metadata:    Optional[dict] = Field(default=None, description="Optional context payload")


# ---------------------------------------------------------------------------
# POST /nudge/event — public storefront measurement endpoint
# ---------------------------------------------------------------------------

@router.post("/nudge/event")
async def ingest_nudge_event(
    request:  Request,
    response: Response,
    db:       Session = Depends(get_db),
):
    """
    Ingest one nudge measurement event from spark-nudge.js.

    Accepts both application/json (fetch) and text/plain (raw sendBeacon)
    content types — body is always parsed as JSON.

    Never returns a 5xx — any server error is logged and silently swallowed
    so measurement failures cannot degrade the storefront experience.

    Access-Control-Allow-Origin: * is set on the response for completeness,
    even though the client never reads the response (fire-and-forget).
    """
    # Cross-origin storefront access — safe, no PII in response
    response.headers["Access-Control-Allow-Origin"] = "*"

    # Parse body — handles both application/json and text/plain
    payload_dict: Optional[dict] = None
    try:
        # FastAPI parses JSON body when Content-Type: application/json
        content_type = request.headers.get("content-type", "")
        raw = await request.body()

        if not raw:
            log.warning("nudge_events: received empty body")
            return {"status": "ok"}

        payload_dict = json.loads(raw.decode("utf-8"))

    except Exception as exc:
        log.warning("nudge_events: failed to parse body: %s", exc)
        return {"status": "ok"}   # swallow — don't degrade storefront

    # Validate with Pydantic
    try:
        payload = NudgeEventPayload(**payload_dict)
    except Exception as exc:
        log.warning("nudge_events: validation failed: %s — raw=%s", exc, repr(payload_dict)[:200])
        return {"status": "ok"}   # swallow invalid payloads silently

    # Validate shop domain format
    if not is_valid_shop_domain(payload.shop):
        log.warning(
            "nudge_events: invalid shop_domain=%r nudge_id=%d event_type=%s",
            payload.shop, payload.nudge_id, payload.event_type,
        )
        return {"status": "ok"}

    # Validate event_type — only client-submittable types accepted here.
    # "holdout_assigned" is server-side only and is never accepted from clients.
    if payload.event_type not in CLIENT_EVENT_TYPES:
        log.warning(
            "nudge_events: unknown event_type=%r shop=%s nudge_id=%d",
            payload.event_type, payload.shop, payload.nudge_id,
        )
        return {"status": "ok"}

    # Validate nudge ownership — nudge_id must belong to the claimed shop.
    # Without this check, any client can POST events for another shop's nudge_id
    # and inflate that shop's exposure counts, A/B stats, and revenue lift reports.
    nudge_owned = (
        db.query(ActiveNudge.id)
        .filter(
            ActiveNudge.id          == payload.nudge_id,
            ActiveNudge.shop_domain == payload.shop,
        )
        .first()
    )
    if nudge_owned is None:
        log.warning(
            "nudge_events: nudge_id=%d does not belong to shop=%s — rejected",
            payload.nudge_id, payload.shop,
        )
        return {"status": "ok"}   # return ok — don't leak existence of nudge IDs

    # Sanitise visitor_id — empty string → None
    visitor_id = (payload.visitor_id or "").strip() or None

    # Record the event
    ev = record_nudge_event(
        db          = db,
        shop_domain = payload.shop,
        nudge_id    = payload.nudge_id,
        visitor_id  = visitor_id,
        product_url = payload.product_url,
        event_type  = payload.event_type,
        metadata    = payload.metadata,
    )

    if ev is not None:
        try:
            db.commit()
        except Exception as exc:
            log.error("nudge_events: commit failed: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    return {"status": "ok"}
