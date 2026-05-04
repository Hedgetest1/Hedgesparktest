"""
integrations.py — Merchant integration settings endpoints.

GET    /merchant/integrations           — list all integration statuses
PUT    /merchant/integrations/klaviyo   — save Klaviyo private key
POST   /merchant/integrations/klaviyo/test — verify stored key
DELETE /merchant/integrations/klaviyo   — disconnect Klaviyo

All endpoints require authenticated merchant session (Pro plan).
The raw Klaviyo key is accepted ONLY on the PUT (save) call.
It is never returned in any response — only a masked hint.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_merchant_session
from app.services.klaviyo_connection import (
    disconnect_klaviyo,
    get_connection_status,
    save_klaviyo_key,
    verify_klaviyo_connection,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/merchant/integrations", tags=["integrations"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class KlaviyoSaveRequest(BaseModel):
    klaviyo_private_key: str = Field(..., min_length=8, max_length=512)


class KlaviyoConnectionResponse(BaseModel):
    status: str
    has_key: bool = False
    key_hint: str | None = None
    last_verified_at: str | None = None
    last_error: str | None = None
    last_sync_at: str | None = None
    last_sync_error: str | None = None


class KlaviyoTestResponse(BaseModel):
    status: str
    detail: str


class IntegrationsResponse(BaseModel):
    klaviyo: KlaviyoConnectionResponse


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=IntegrationsResponse)
def get_integrations(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> IntegrationsResponse:
    """
    Return all integration statuses for the authenticated merchant.

    Currently only Klaviyo — designed to extend with additional
    channels (SMS, push, etc.) without breaking the response shape.
    """
    klaviyo_status = get_connection_status(db, shop)
    return IntegrationsResponse(
        klaviyo=KlaviyoConnectionResponse(**klaviyo_status),
    )


@router.put("/klaviyo", response_model=KlaviyoConnectionResponse)
def save_klaviyo(
    body: KlaviyoSaveRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> KlaviyoConnectionResponse:
    """
    Save (or replace) the merchant's Klaviyo private key.

    The key is encrypted at rest using AES-256-GCM (same scheme as
    Shopify access tokens). It is never returned in any API response.

    After saving, the connection status is set to 'unverified'.
    Call POST /merchant/integrations/klaviyo/test to verify.
    """
    result = save_klaviyo_key(db, shop, body.klaviyo_private_key)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Save failed"))

    db.commit()

    status = get_connection_status(db, shop)
    return KlaviyoConnectionResponse(**status)


@router.post("/klaviyo/test", response_model=KlaviyoTestResponse)
def test_klaviyo(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> KlaviyoTestResponse:
    """
    Verify the stored Klaviyo key against the Klaviyo API.

    Makes a lightweight read-only call to Klaviyo Accounts API.
    Updates connection status based on the result.

    Possible statuses:
        connected    — key is valid
        invalid_key  — auth failed (key revoked or wrong)
        error        — network/timeout issue
        not_connected — no key saved
    """
    result = verify_klaviyo_connection(db, shop)
    db.commit()
    return KlaviyoTestResponse(**result)


@router.delete("/klaviyo", response_model=KlaviyoConnectionResponse)
def remove_klaviyo(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> KlaviyoConnectionResponse:
    """
    Disconnect Klaviyo — removes the stored key and resets state.
    """
    result = disconnect_klaviyo(db, shop)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Disconnect failed"))

    db.commit()

    status = get_connection_status(db, shop)
    return KlaviyoConnectionResponse(**status)
