"""
merchant_email_service.py — Lifecycle email sending with dedup and audit logging.

Handles all merchant-facing lifecycle emails:
    welcome           — install confirmed (once per shop, ever)
    setup_incomplete   — onboarding stuck (72h cooldown)
    first_insight      — first signal found (once per shop, ever)
    connection_issue   — store disconnected (72h cooldown)

Public interface:
    submit_lifecycle_intent(db, shop_domain, email_type, context) -> dict
    get_email_history(db, shop_domain, limit) -> list[dict]

Every call is logged to merchant_emails table regardless of outcome.
Redis is used for fast dedup; DB is the durable audit trail.

Called by:
    - onboarding.py (welcome, on successful onboarding)
    - agent_worker.py (setup_incomplete, first_insight, connection_issue)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.merchant_email import MerchantEmail

log = logging.getLogger("merchant_email")

# ---------------------------------------------------------------------------
# Cooldown configuration per email type
#
# "once" = send at most once per shop, ever (checked via DB)
# int    = minimum seconds between sends of same type to same shop
# ---------------------------------------------------------------------------
_COOLDOWNS: dict[str, str | int] = {
    "welcome": "once",
    "setup_incomplete": 259200,   # 72 hours
    "first_insight": "once",
    "connection_issue": 259200,   # 72 hours
    "reengagement": 1209600,      # 14 days — matches silence detector dedup
}

_REDIS_PREFIX = "hs:memail:"
_REDIS_TTL = 259200  # 72 hours — matches longest cooldown


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------

def _is_suppressed(
    db: Session,
    shop_domain: str,
    email_type: str,
) -> str | None:
    """
    Check if this email should be suppressed.

    Returns the suppression reason string, or None if sending is allowed.

    Two-layer check:
      1. Redis for fast-path (avoids DB query on every worker cycle)
      2. DB for durable "once" emails (survives Redis flush)
    """
    cooldown = _COOLDOWNS.get(email_type)
    if cooldown is None:
        return f"unknown_email_type:{email_type}"

    # Layer 1: Redis fast check
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            redis_key = f"{_REDIS_PREFIX}{shop_domain}:{email_type}"
            if rc.get(redis_key):
                return "cooldown_active"
    except Exception:
        pass  # Redis down — fall through to DB check

    # Layer 2: DB durable check
    if cooldown == "once":
        existing = (
            db.query(MerchantEmail.id)
            .filter(
                MerchantEmail.shop_domain == shop_domain,
                MerchantEmail.email_type == email_type,
                MerchantEmail.status == "sent",
            )
            .first()
        )
        if existing:
            return "already_sent_once"
    else:
        cutoff = _now() - timedelta(seconds=int(cooldown))
        recent = (
            db.query(MerchantEmail.id)
            .filter(
                MerchantEmail.shop_domain == shop_domain,
                MerchantEmail.email_type == email_type,
                MerchantEmail.status == "sent",
                MerchantEmail.created_at >= cutoff,
            )
            .first()
        )
        if recent:
            return "cooldown_active"

    return None


_EMAIL_BUDGET_ALERTED = False  # in-process dedup for budget alerts


def _alert_email_budget_once(usage: dict) -> None:
    """Send a single Telegram alert when email budget is near/at exhaustion."""
    global _EMAIL_BUDGET_ALERTED
    if _EMAIL_BUDGET_ALERTED:
        return
    try:
        from app.services.telegram_agent import send_message, is_configured
        if is_configured():
            sent = send_message(
                f"*EMAIL BUDGET ALERT*\n\n"
                f"Sent: {usage['sent']}/{usage['limit']} ({usage['pct']}%)\n"
                f"Status: {usage['status']}\n\n"
                f"Email sends will be blocked when limit is reached."
            )
            if sent:
                _EMAIL_BUDGET_ALERTED = True
                # Only mark alerted if message actually sent — retry next cycle otherwise
    except Exception:
        pass  # Don't set flag on failure — will retry next cycle


def _mark_sent_in_redis(shop_domain: str, email_type: str) -> None:
    """Set Redis marker after successful send."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cooldown = _COOLDOWNS.get(email_type, 259200)
            ttl = _REDIS_TTL if cooldown == "once" else int(cooldown)
            rc.set(f"{_REDIS_PREFIX}{shop_domain}:{email_type}", "1", ex=ttl)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit_lifecycle_intent(
    db: Session,
    shop_domain: str,
    email_type: str,
    context: dict | None = None,
) -> dict:
    """
    Submit a lifecycle email as an intent to the orchestrator.

    Same validation as send_lifecycle_email (merchant lookup, dedup, template
    render) but does NOT send — instead submits an EmailIntent.

    Returns same shape as send_lifecycle_email for compatibility.
    """
    ctx = context or {}

    merchant = (
        db.query(Merchant)
        .filter(Merchant.shop_domain == shop_domain)
        .first()
    )
    if not merchant:
        return {"status": "failed", "reason": "merchant_not_found"}

    to_email = (merchant.contact_email or "").strip()
    if not to_email:
        return {"status": "no_email", "reason": "no_contact_email"}

    if merchant.install_status != "active":
        return {"status": "suppressed", "reason": "merchant_uninstalled"}

    # Dedup check (still needed — orchestrator doesn't know about per-type cooldowns)
    suppression = _is_suppressed(db, shop_domain, email_type)
    if suppression:
        return {"status": "suppressed", "reason": suppression}

    # Render template
    try:
        from app.services.email_templates import render_email
        shop_name = shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
        ctx.setdefault("shop_name", shop_name)
        subject, html, plain_text = render_email(email_type, ctx)
    except Exception as exc:
        log.error("merchant_email: template render failed type=%s shop=%s: %s", email_type, shop_domain, exc)
        return {"status": "failed", "reason": "template_error"}

    # Submit intent
    from app.services.email_orchestrator import EmailIntent, submit_intent
    intent = EmailIntent(
        shop_domain=shop_domain,
        email_type=email_type,
        to_email=to_email,
        subject=subject,
        html=html,
        plain_text=plain_text,
        from_address="HedgeSpark <dev@hedgesparkhq.com>",
        producer="lifecycle",
        context=ctx,
    )
    intent_id = submit_intent(db, intent)
    return {"status": "queued", "reason": None, "intent_id": intent_id}


    # NOTE: send_lifecycle_email was removed. All lifecycle emails now go through
    # submit_lifecycle_intent → orchestrator → governance → send_email.
    # See submit_lifecycle_intent() above.


def _log_email(
    db: Session,
    shop_domain: str,
    email_type: str,
    to_email: str | None,
    subject: str | None,
    status: str,
    suppressed_by: str | None,
    resend_id: str | None = None,
) -> None:
    """Persist email attempt to merchant_emails table."""
    try:
        entry = MerchantEmail(
            shop_domain=shop_domain,
            email_type=email_type,
            to_email=to_email,
            subject=subject,
            status=status,
            suppressed_by=suppressed_by,
            resend_id=resend_id,
        )
        db.add(entry)
        db.flush()
    except Exception as exc:
        log.warning("merchant_email: log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Operator visibility
# ---------------------------------------------------------------------------

def get_email_history(
    db: Session,
    shop_domain: str | None = None,
    email_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Query email history for operator inspection.

    Filters:
        shop_domain — filter by shop (optional)
        email_type  — filter by type (optional)
        limit       — max rows (default 100)

    Returns list of dicts with all MerchantEmail fields.
    """
    q = db.query(MerchantEmail).order_by(MerchantEmail.created_at.desc())
    if shop_domain:
        q = q.filter(MerchantEmail.shop_domain == shop_domain)
    if email_type:
        q = q.filter(MerchantEmail.email_type == email_type)
    rows = q.limit(limit).all()

    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "shop_domain": r.shop_domain,
            "email_type": r.email_type,
            "to_email": r.to_email,
            "subject": r.subject,
            "status": r.status,
            "suppressed_by": r.suppressed_by,
        }
        for r in rows
    ]
