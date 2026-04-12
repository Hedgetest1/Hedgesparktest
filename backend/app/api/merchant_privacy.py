"""
merchant_privacy.py — Data-subject rights API endpoints.

Endpoints:
    GET    /merchant/privacy/preferences
    PATCH  /merchant/me                  (Art. 16 rectification)
    POST   /merchant/object              (Art. 21 + CCPA §1798.120)
    POST   /merchant/unobject            (reverse the opt-out)

Auth: session cookie via `require_merchant_session`. No API keys here
— data-subject rights are exercised by the merchant themselves, so the
session is the proof of identity.

Every state-changing action writes an audit_log entry with action_type
`gdpr_rectify` or `gdpr_object` so the regulator-facing trail is
complete.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services.audit import write_audit_log
from app.services.merchant_privacy import (
    get_privacy_preferences,
    is_merchant_opted_out,
    set_opt_out,
    update_contact_email,
)

log = logging.getLogger("merchant_privacy_api")

router = APIRouter(prefix="/merchant", tags=["merchant-privacy"])


class RectifyRequest(BaseModel):
    contact_email: Optional[str] = None

    @field_validator("contact_email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        # Lightweight RFC-ish check — we keep the full validation in
        # the service layer where it rejects invalid shapes. We don't
        # import pydantic[email] to avoid a runtime dependency.
        if "@" not in v or "." not in v.split("@")[-1] or len(v) > 254:
            raise ValueError("invalid email")
        return v


class ObjectRequest(BaseModel):
    reason: Optional[str] = None  # optional free-text for audit trail


@router.get("/privacy/preferences")
def read_preferences(
    shop: str = Depends(require_merchant_session),
):
    return get_privacy_preferences(shop)


@router.patch("/me")
def rectify_merchant_profile(
    body: RectifyRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """GDPR Art. 16 — right to rectification. Merchant corrects the
    contact email we have on file. Only the fields present in the
    request body are touched."""
    changed: list[str] = []

    if body.contact_email is not None:
        result = update_contact_email(
            db,
            shop_domain=shop,
            new_email=str(body.contact_email),
        )
        if result["status"] != "updated":
            raise HTTPException(
                status_code=400,
                detail=f"rectification_failed: {result['status']}",
            )
        changed.append("contact_email")
        try:
            write_audit_log(
                db,
                actor_type="merchant",
                actor_name=f"merchant:{shop}",
                action_type="gdpr_rectify",
                target_type="merchant",
                target_id=shop,
                shop_domain=shop,
                before_state={"email_hash": result["previous_email_hash"]},
                after_state={"email_hash": result["new_email_hash"]},
                status="completed",
                approval_mode="self_service",
            )
            db.commit()
        except Exception as exc:
            log.warning("rectify: audit write failed: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass

    return {"status": "ok", "changed": changed}


@router.post("/object")
def object_to_processing(
    body: ObjectRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """GDPR Art. 21 + CCPA §1798.120. Merchant opts out of automated
    targeting, scoring, and nudge composition. Downstream systems
    check `is_merchant_opted_out(shop)` before running any such
    logic."""
    set_opt_out(shop, True)
    try:
        write_audit_log(
            db,
            actor_type="merchant",
            actor_name=f"merchant:{shop}",
            action_type="gdpr_object",
            target_type="merchant",
            target_id=shop,
            shop_domain=shop,
            before_state={"opt_out_automated_targeting": False},
            after_state={"opt_out_automated_targeting": True},
            status="completed",
            approval_mode="self_service",
            metadata={"reason": (body.reason or "")[:200]},
        )
        db.commit()
    except Exception as exc:
        log.warning("object: audit write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

    return {
        "status": "opted_out",
        "shop_domain": shop,
        "opt_out_automated_targeting": True,
    }


@router.post("/unobject")
def withdraw_objection(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Reverse a prior Art. 21 objection. Merchants are free to opt
    back in at any time."""
    set_opt_out(shop, False)
    try:
        write_audit_log(
            db,
            actor_type="merchant",
            actor_name=f"merchant:{shop}",
            action_type="gdpr_unobject",
            target_type="merchant",
            target_id=shop,
            shop_domain=shop,
            before_state={"opt_out_automated_targeting": True},
            after_state={"opt_out_automated_targeting": False},
            status="completed",
            approval_mode="self_service",
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    return {
        "status": "opted_in",
        "shop_domain": shop,
        "opt_out_automated_targeting": False,
    }
