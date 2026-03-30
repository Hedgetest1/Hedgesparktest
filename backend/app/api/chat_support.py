"""
chat_support.py — Merchant support chatbot API.

POST /chat/support          — process a merchant message
GET  /chat/support/history  — recent incidents for this merchant
PATCH /chat/support/incidents/{id}/resolve — operator resolves an incident

Rate limit: 30 messages/hour per merchant (Redis-backed, in-process fallback).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import require_merchant_session, require_operator, get_db

log = logging.getLogger("chat_support")

router = APIRouter(prefix="/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Per-merchant rate limit (30 messages / 3600 seconds)
# ---------------------------------------------------------------------------
_CHAT_MAX_PER_HOUR = 30
_CHAT_WINDOW = 3600

# In-process fallback (Redis preferred)
_local_buckets: dict[str, list[float]] = defaultdict(list)


def _check_merchant_rate_limit(shop_domain: str) -> tuple[bool, int]:
    """
    Per-merchant rate limit.  Redis first, in-process fallback.
    Returns (allowed, retry_after_seconds).
    """
    bucket_key = f"hs:chat:{shop_domain}"

    # Try Redis
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            count = rc.incr(bucket_key)
            if count == 1:
                rc.expire(bucket_key, _CHAT_WINDOW)
            ttl = rc.ttl(bucket_key)
            if count > _CHAT_MAX_PER_HOUR:
                return False, max(1, ttl if ttl > 0 else _CHAT_WINDOW)
            return True, 0
    except Exception:
        pass

    # In-process fallback
    now = time.monotonic()
    cutoff = now - _CHAT_WINDOW
    ts = _local_buckets[shop_domain]
    ts[:] = [t for t in ts if t >= cutoff]
    if len(ts) >= _CHAT_MAX_PER_HOUR:
        retry = int(_CHAT_WINDOW - (now - ts[0])) + 1
        return False, retry
    ts.append(now)
    return True, 0


_RATE_LIMIT_RESPONSE = (
    "You've sent a lot of messages recently. "
    "Please wait a few minutes before sending another. "
    "If you have an urgent issue, email support@hedgesparkhq.com."
)


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponseSchema(BaseModel):
    message: str
    classification: str
    severity: str
    affected_area: str
    incident_created: bool = False
    incident_id: int | None = None
    repair_attempted: bool = False
    repair_result: str | None = None
    diagnostic_summary: dict | None = None


class ResolveRequest(BaseModel):
    resolution_summary: str = Field(..., min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/support", response_model=ChatResponseSchema)
def chat_support(
    body: ChatRequest,
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Process a merchant support message."""
    # Per-merchant rate limit
    allowed, retry_after = _check_merchant_rate_limit(shop_domain)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=_RATE_LIMIT_RESPONSE,
            headers={"Retry-After": str(retry_after)},
        )

    from app.services.merchant_chatbot import process_message

    result = process_message(db, shop_domain, body.message)
    db.commit()

    return ChatResponseSchema(
        message=result.message,
        classification=result.classification,
        severity=result.severity,
        affected_area=result.affected_area,
        incident_created=result.incident_created,
        incident_id=result.incident_id,
        repair_attempted=result.repair_attempted,
        repair_result=result.repair_result,
        diagnostic_summary=result.diagnostic_summary,
    )


@router.get("/support/history")
def chat_support_history(
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Get recent support incidents for this merchant."""
    from app.services.merchant_chatbot import get_incident_history
    return get_incident_history(db, shop_domain)


@router.patch("/support/incidents/{incident_id}/resolve")
def resolve_support_incident(
    incident_id: int,
    body: ResolveRequest,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Operator resolves a support incident."""
    from datetime import datetime, timezone
    from app.models.support_incident import SupportIncident

    incident = db.query(SupportIncident).filter(SupportIncident.id == incident_id).first()
    if not incident:
        raise HTTPException(404, "Incident not found")

    if incident.status == "resolved":
        raise HTTPException(409, "Incident already resolved")

    incident.status = "resolved"
    incident.resolution_summary = body.resolution_summary
    incident.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    incident.resolved_by = "operator"

    from app.services.audit import write_audit_log
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="support_incident_resolved",
        target_type="support_incident",
        target_id=str(incident_id),
        shop_domain=incident.shop_domain,
        after_state={"resolution": body.resolution_summary},
        status="completed",
    )
    db.commit()

    return {
        "status": "resolved",
        "incident_id": incident_id,
        "resolution_summary": body.resolution_summary,
    }
