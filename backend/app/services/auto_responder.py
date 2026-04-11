"""
auto_responder.py — Safe Phase 1 auto-response for inbound merchant emails.

Only responds to classifications where automated response is SAFE:
    - onboarding_confusion → guided help
    - praise → acknowledgment

NEVER auto-responds to:
    - billing_or_legal (requires human)
    - complaint (requires human)
    - bug_report (requires investigation)
    - feature_request (requires product decision)

All responses are:
    - Short (max 3 sentences)
    - Templated (no LLM, no improvisation)
    - Validated through response_guardrails before sending
    - Logged for operator review

Called by: inbound_action_executor.py after classification + routing
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.inbound_email import InboundEmail

log = logging.getLogger("auto_responder")

# Classifications safe for auto-response (Phase 1)
_SAFE_CLASSIFICATIONS = {"onboarding_confusion", "praise"}

# Response templates — short, human, non-hallucinatory
_TEMPLATES: dict[str, str] = {
    "onboarding_confusion": (
        "Thanks for reaching out! "
        "The quickest way to get started is to open your dashboard at "
        "https://app.hedgesparkhq.com and follow the setup steps there. "
        "If you're still stuck, just reply and a human will help you directly."
    ),
    "praise": (
        "Thank you — that means a lot to us! "
        "We're a small team building this for merchants like you, "
        "and hearing that keeps us going."
    ),
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def should_auto_respond(email: InboundEmail) -> bool:
    """Check if this email is safe for auto-response."""
    if email.classification not in _SAFE_CLASSIFICATIONS:
        return False
    if email.agent_response_sent_at is not None:
        return False  # Already responded
    if email.routing_status == "escalated":
        return False  # Human is handling it
    return True


def draft_response(email: InboundEmail) -> str | None:
    """
    Generate a safe auto-response for the given inbound email.

    Returns the response text, or None if not safe to respond.
    All responses are validated through guardrails.
    """
    if not should_auto_respond(email):
        return None

    template = _TEMPLATES.get(email.classification)
    if not template:
        return None

    # Validate through guardrails
    from app.services.response_guardrails import validate_response
    result = validate_response(template, context="auto_response", classification=email.classification)
    if not result.safe:
        log.warning(
            "auto_responder: guardrail blocked template for %s: %s",
            email.classification, result.violations,
        )
        return None

    return template


_AUTO_RESPONSE_DAILY_CAP = 3  # max auto-responses per merchant per day
_REDIS_AR_PREFIX = "hs:auto_resp:"


def _check_auto_response_rate(shop_domain: str) -> bool:
    """Check if we've exceeded the daily auto-response cap for this merchant."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return True  # fail-open if Redis unavailable
        key = f"{_REDIS_AR_PREFIX}{shop_domain}"
        count = rc.get(key)
        if count is not None and int(count) >= _AUTO_RESPONSE_DAILY_CAP:
            return False
        pipe = rc.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, 86400)
        pipe.execute()
        return True
    except Exception:
        return True  # fail-open


def send_auto_response(db: Session, email: InboundEmail) -> bool:
    """
    Send an auto-response to a classified inbound email.

    Returns True if sent, False if skipped/failed.
    Rate-limited to 3 per merchant per day.
    """
    response_text = draft_response(email)
    if not response_text:
        return False

    # Rate limit check — prevent auto-response spam
    shop = email.shop_domain if hasattr(email, "shop_domain") and email.shop_domain else None
    if shop and not _check_auto_response_rate(shop):
        log.info("auto_responder: rate limited for %s (>%d/day)", shop, _AUTO_RESPONSE_DAILY_CAP)
        return False

    # Build email content
    to_addr = email.from_email
    if not to_addr:
        return False

    subject = f"Re: {email.subject}" if email.subject else "Re: your message"

    from app.services.email_templates import _wrap_html, _p
    body = _p(response_text)
    html = _wrap_html(subject, body)

    # Send through orchestrator — immediate mode (low latency, full governance)
    from app.services.email_orchestrator import EmailIntent, send_immediate
    intent = EmailIntent(
        shop_domain=shop or "unknown",
        email_type="auto_response",
        to_email=to_addr,
        subject=subject,
        html=html,
        plain_text=response_text,
        from_address="HedgeSpark <dev@hedgesparkhq.com>",
        producer="auto_responder",
    )
    result = send_immediate(db, intent)

    if result["status"] == "sent":
        email.agent_response_draft = response_text
        email.agent_response_sent_at = _now()
        email.routing_status = "responded"
        db.flush()

        log.info(
            "auto_responder: sent to=%s classification=%s email_id=%d",
            to_addr, email.classification, email.id,
        )
        return True
    else:
        log.warning(
            "auto_responder: blocked to=%s email_id=%d reason=%s",
            to_addr, email.id, result.get("reason"),
        )
        return False


    # NOTE: run_auto_responses() was removed — auto-response is called inline
    # from inbound_action_executor._execute_one() to ensure correct ordering
    # (auto-response fires before routing_status is set to "executed").
