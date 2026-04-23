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
_local_buckets: dict[str, list[float]] = defaultdict(list)  # multi-worker: redis-backed


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
    except Exception as exc:
        log.warning("chat_support: redis rate limit check failed: %s", exc)

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
    "If you have an urgent issue, email dev@hedgesparkhq.com."
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

    if incident.status == "resolved" and incident.resolution_verified:
        raise HTTPException(409, "Incident already resolved")

    incident.status = "resolved"
    incident.resolution_summary = body.resolution_summary
    incident.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    incident.resolved_by = "operator"
    incident.resolution_verified = True  # operator manually confirmed

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


@router.get("/support/resolutions")
def chat_support_resolutions(
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Get undelivered resolution messages for this merchant.

    Returns incidents that have been resolved but whose resolution_summary
    has not yet been delivered to the merchant's chat.
    Polled by the frontend every 30s.
    """
    from app.models.support_incident import SupportIncident

    pending = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.shop_domain == shop_domain,
            SupportIncident.status == "resolved",
            SupportIncident.resolution_summary.isnot(None),
            SupportIncident.resolution_delivered_at.is_(None),
            # Gate: only deliver messages for verified resolutions.
            # Operator-resolved incidents must set resolution_verified=True.
            # Auto-fixes are verified by outcome measurement (48h delay).
            SupportIncident.resolution_verified == True,
        )
        .order_by(SupportIncident.resolved_at.asc())
        .limit(5)
        .all()
    )

    return [
        {
            "incident_id": inc.id,
            "resolution_summary": inc.resolution_summary,
            "resolved_at": inc.resolved_at.isoformat() + "Z" if inc.resolved_at else None,
        }
        for inc in pending
    ]


# ---------------------------------------------------------------------------
# Proactive messages — system-initiated check-ins
# ---------------------------------------------------------------------------

@router.get("/support/proactive")
def chat_support_proactive(
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Get undelivered proactive messages for this merchant.

    Proactive messages are system-injected check-ins, triggered by:
    - Post-onboarding welcome (first connected visit)
    - Post-fix follow-up (asking if fix worked)
    - Low activity nudge (merchant seems stuck)

    Polled by the frontend alongside resolution polling.
    Each message is delivered once, then ack'd.
    """
    from app.services.proactive_chat import get_pending_proactive_messages
    return get_pending_proactive_messages(db, shop_domain)


@router.post("/support/proactive/{message_id}/ack")
def chat_support_ack_proactive(
    message_id: str,
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Acknowledge a proactive message was displayed in the merchant's chat."""
    from app.services.proactive_chat import ack_proactive_message
    ack_proactive_message(db, shop_domain, message_id)
    db.commit()
    return {"status": "acknowledged", "message_id": message_id}


# ---------------------------------------------------------------------------
# Resolution acknowledgement
# ---------------------------------------------------------------------------

@router.post("/support/resolutions/{incident_id}/ack")
def chat_support_ack_resolution(
    incident_id: int,
    shop_domain: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Acknowledge that a resolution message was displayed in the merchant's chat.
    Sets resolution_delivered_at so it won't be returned again.
    """
    from datetime import datetime, timezone
    from app.models.support_incident import SupportIncident

    incident = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.id == incident_id,
            SupportIncident.shop_domain == shop_domain,
        )
        .first()
    )
    if not incident:
        raise HTTPException(404, "Incident not found")

    incident.resolution_delivered_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    return {"status": "acknowledged", "incident_id": incident_id}
