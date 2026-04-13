"""
shopify_refunds.py — Shopify refunds/create webhook ingestion.

Split from webhooks.py (TIER_2 protected) to keep the core OAuth/order
webhook surface untouched while still ingesting real refund data for
refund_loss.py to consume.

HMAC verification reuses the same _verify_shopify_hmac helper as the
canonical Shopify webhook handler — one algorithm, one secret, one
fail-closed contract.

Endpoint
--------
POST /webhooks/shopify/refunds
    Shopify topic: `refunds/create`
    Headers expected:
      X-Shopify-Hmac-Sha256   (base64 HMAC of the raw body)
      X-Shopify-Shop-Domain   (the shop)
      X-Shopify-Topic         ("refunds/create", validated)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.webhooks import _verify_shopify_hmac
from app.services.refund_ingest import ingest_refund

log = logging.getLogger("shopify_refunds")

router = APIRouter(prefix="/webhooks", tags=["shopify-webhooks"])


@router.post("/shopify/refunds")
async def refund_created(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_topic: str | None = Header(default=None),
) -> dict:
    raw_body = await request.body()
    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="invalid HMAC")

    if not x_shopify_shop_domain:
        raise HTTPException(status_code=400, detail="missing X-Shopify-Shop-Domain")

    if x_shopify_topic and x_shopify_topic != "refunds/create":
        log.info("shopify_refunds: ignoring unexpected topic=%s", x_shopify_topic)
        return {"ok": True, "ignored": True, "reason": "wrong_topic"}

    try:
        payload = json.loads(raw_body.decode() or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    added = ingest_refund(x_shopify_shop_domain, payload)

    # Fan out a signal so outbound webhooks + dashboards learn about it.
    # Best-effort — never fails the webhook ack.
    try:
        from app.services.signal_webhooks import emit_signal
        emit_signal(
            x_shopify_shop_domain,
            event_type="refund_spike",
            payload={
                "refund_id": str(payload.get("id") or ""),
                "order_id": str(payload.get("order_id") or ""),
                "rows_stored": added,
            },
            source="shopify_refunds_webhook",
        )
    except Exception:
        pass

    # Phase Ω''' — outbound webhook fan-out for merchant-subscribable
    # refund.processed events. Opens a short-lived db session because
    # this webhook handler is request-scoped and has no Depends(get_db).
    try:
        from app.core.database import SessionLocal
        from app.services.event_emitter import emit
        with SessionLocal() as _db:
            emit(_db, x_shopify_shop_domain, "refund.processed", {
                "refund_id": str(payload.get("id") or ""),
                "order_id": str(payload.get("order_id") or ""),
                "rows_stored": added,
                "amount_eur": float(payload.get("transactions", [{}])[0].get("amount") or 0)
                              if payload.get("transactions") else 0.0,
            })
    except Exception:
        pass

    return {"ok": True, "stored_rows": added}
