"""
inbound_email_processor.py — Classify and route inbound merchant emails.

Phase 1 (deterministic):
    Keyword-based classification with explicit rules.
    No LLM. No inference. No guessing.

Phase 2 (future):
    LLM-assisted classification with confidence scoring.
    Auto-response drafting with guardrails.

Public interface:
    process_inbound(db, message_id, from_email, to_email, subject, body_text, body_html) -> dict
    classify_intent(subject, body) -> tuple[str, str]  # (classification, confidence)
    route_email(db, inbound: InboundEmail) -> str       # routing_action taken

Classifications:
    bug_report, onboarding_confusion, feature_request, suggestion,
    praise, complaint, billing_or_legal, noise
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.inbound_email import InboundEmail
from app.models.merchant import Merchant

log = logging.getLogger("inbound_email")

# Our own sending addresses — never process replies FROM ourselves
_OWN_ADDRESSES = {
    "dev@hedgesparkhq.com",
    "hello@hedgesparkhq.com",
    "digest@hedgesparkhq.com",
    "alerts@hedgesparkhq.com",
    "noreply@hedgesparkhq.com",
}

# Max inbound emails from a single sender within a window before suppressing
_LOOP_DETECTION_WINDOW_HOURS = 1
_LOOP_DETECTION_MAX_EMAILS = 5


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Keyword classification rules
# ---------------------------------------------------------------------------

# Each rule: (classification, confidence, patterns)
# Patterns are checked against subject + body (lowercased).
# First match wins — order matters (most specific first).
_CLASSIFICATION_RULES: list[tuple[str, str, list[str]]] = [
    # Billing / legal — must escalate
    ("billing_or_legal", "high", [
        r"\b(invoice|billing|charge|refund|payment|subscription|cancel\s+(?:my\s+)?(?:plan|account|subscription))\b",
        r"\b(legal|lawyer|attorney|gdpr|data\s+(?:deletion|request|privacy)|terms\s+of\s+service)\b",
        r"\b(unsubscribe|opt[\s-]?out|stop\s+(?:sending|emails))\b",
    ]),

    # Complaint — must escalate
    ("complaint", "high", [
        r"\b(complaint|unacceptable|terrible|awful|worst|scam|fraud|rip[\s-]?off)\b",
        r"\b(very\s+(?:unhappy|disappointed|frustrated)|extremely\s+(?:unhappy|disappointed))\b",
        r"\b(demand\s+(?:a\s+)?refund|want\s+my\s+money\s+back)\b",
    ]),

    # Bug report
    ("bug_report", "high", [
        r"\b(bug|error|crash|broken|not\s+working|doesn.t\s+work|won.t\s+(?:load|open|work))\b",
        r"\b(issue|problem)\b.*\b(with|when|after)\b",
        r"\b(500|404|exception|traceback|stack\s+trace)\b",
        r"\b(can.t\s+(?:access|login|log\s+in|see|view|load))\b",
    ]),

    # Onboarding confusion
    ("onboarding_confusion", "high", [
        r"\b(how\s+(?:do\s+I|to|does)|where\s+(?:do\s+I|is|can\s+I))\b",
        r"\b(setup|set\s+up|getting\s+started|onboarding)\b.*\b(help|stuck|confused|lost)\b",
        r"\b(don.t\s+(?:understand|know\s+(?:how|what|where)))\b",
        r"\b(what\s+(?:do\s+I\s+do|should\s+I|is\s+(?:this|next)))\b",
        r"\b(stuck|confused|lost)\b.*\b(setup|install|dashboard|pixel|tracker)\b",
    ]),

    # Feature request
    ("feature_request", "medium", [
        r"\b(feature\s+request|would\s+be\s+(?:great|nice|cool)|can\s+you\s+add)\b",
        r"\b(it\s+would\s+be\s+(?:great|nice|helpful)|wish\s+(?:you|it)\s+(?:had|could))\b",
        r"\b(please\s+add|could\s+you\s+(?:add|build|make|implement))\b",
        r"\b(any\s+plans?\s+(?:to|for))\b",
    ]),

    # Suggestion
    ("suggestion", "medium", [
        r"\b(suggest(?:ion)?|idea|thought|consider|maybe\s+you\s+(?:could|should))\b",
        r"\b(feedback|improvement|improve)\b",
        r"\b(have\s+you\s+(?:thought|considered))\b",
    ]),

    # Praise
    ("praise", "high", [
        r"\b(love\s+(?:it|this|your)|amazing|awesome|great\s+(?:job|work|product|tool))\b",
        r"\b(thank\s+(?:you|u)|thanks|much\s+appreciated)\b",
        r"\b(impressed|fantastic|excellent|brilliant|well\s+done)\b",
        r"\b(keep\s+(?:up\s+the|it\s+up))\b",
    ]),
]

# Noise patterns — auto-archive
_NOISE_PATTERNS: list[str] = [
    r"\b(out\s+of\s+(?:the\s+)?office|auto[\s-]?reply|automatic\s+reply)\b",
    r"\b(delivery\s+(?:status|notification)|mailer[\s-]?daemon|postmaster)\b",
    r"\b(undeliverable|returned\s+mail)\b",
    r"^(?:re:\s*)*(?:fw[d]?:\s*)*$",  # empty subject chain
]


def classify_intent(subject: str | None, body: str | None) -> tuple[str, str]:
    """
    Classify inbound email intent using keyword rules.

    Returns (classification, confidence).
    """
    text = f"{subject or ''} {body or ''}".lower().strip()

    if not text or len(text) < 3:
        return ("noise", "high")

    # Check noise first
    for pattern in _NOISE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return ("noise", "high")

    # Check classification rules (first match wins)
    for classification, confidence, patterns in _CLASSIFICATION_RULES:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return (classification, confidence)

    # Default: unclassified → treat as suggestion with low confidence
    return ("suggestion", "low")


# ---------------------------------------------------------------------------
# Merchant resolution
# ---------------------------------------------------------------------------

def _normalize_email(raw: str) -> str:
    """Extract and normalize email address from raw sender string."""
    if not raw:
        return ""
    cleaned = raw.strip().lower()
    # Extract email from "Name <email>" format
    match = re.search(r"<([^>]+)>", cleaned)
    if match:
        cleaned = match.group(1).strip()
    return cleaned


def _resolve_merchant(db: Session, from_email: str) -> str | None:
    """
    Try to resolve the sender email to a shop_domain.

    Single case-insensitive, whitespace-trimmed query against
    merchants.contact_email. Handles mixed-case DB values and
    "Display Name <email>" sender formats.
    """
    email_clean = _normalize_email(from_email)
    if not email_clean:
        return None

    # operator-filter: per-tenant lookup by inbound email address —
    # finds the single merchant whose contact_email matches the sender.
    # If founder emails the system, this resolves to hedgespark-dev,
    # which is correct: the founder IS the dev-tenant owner.
    from sqlalchemy import func
    merchant = (
        db.query(Merchant.shop_domain)
        .filter(func.lower(func.trim(Merchant.contact_email)) == email_clean)
        .first()
    )
    return merchant.shop_domain if merchant else None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# Route table: classification → (routing_action, requires_human)
_ROUTE_TABLE: dict[str, tuple[str, bool]] = {
    "bug_report":            ("create_support_incident", False),
    "onboarding_confusion":  ("create_support_incident", False),
    "feature_request":       ("log_product_feedback", False),
    "suggestion":            ("log_product_feedback", False),
    "complaint":             ("escalate_human", True),
    "billing_or_legal":      ("escalate_human", True),
    "praise":                ("log_positive_feedback", False),
    "noise":                 ("archive", False),
}


def _route_email(db: Session, inbound: InboundEmail) -> str:
    """
    Route classified email to the appropriate pipeline.

    Returns the routing_action taken.
    """
    classification = inbound.classification or "noise"
    route_action, requires_human = _ROUTE_TABLE.get(classification, ("archive", False))

    now = _now()

    if requires_human:
        inbound.routing_status = "escalated"
        inbound.routing_action = route_action
        inbound.routed_at = now
        # Send Telegram alert for human escalation
        _send_escalation_alert(inbound)
    elif route_action == "archive":
        inbound.routing_status = "archived"
        inbound.routing_action = route_action
        inbound.routed_at = now
    else:
        inbound.routing_status = "routed"
        inbound.routing_action = route_action
        inbound.routed_at = now

    inbound.processed_at = now
    db.flush()

    log.info(
        "inbound_email: routed id=%s classification=%s action=%s status=%s shop=%s",
        inbound.id, classification, route_action, inbound.routing_status,
        inbound.shop_domain,
    )
    return route_action


_ESCALATION_REDIS_PREFIX = "hs:email_escalation:"
_ESCALATION_COOLDOWN_SECONDS = 3600  # 1 hour per sender


def _should_send_escalation(from_email: str) -> bool:
    """
    Rate-limit Telegram escalation alerts: max 1 per sender per hour.

    Uses Redis SET NX. If Redis is down, allows the alert (fail-open for
    escalations — better to double-alert than miss a complaint).
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("inbound_email.escalation_cooldown")
            return True  # Redis down — fail-open for escalations
        key = f"{_ESCALATION_REDIS_PREFIX}{_normalize_email(from_email)}"
        result = rc.set(key, "1", nx=True, ex=_ESCALATION_COOLDOWN_SECONDS)
        return bool(result)
    except Exception:
        return True  # Fail-open


def _send_escalation_alert(inbound: InboundEmail) -> None:
    """Send Telegram alert for emails that need human attention. Rate-limited per sender."""
    if not _should_send_escalation(inbound.from_email):
        log.info(
            "inbound_email: escalation rate-limited for %s (cooldown active)",
            inbound.from_email,
        )
        return

    try:
        from app.services.telegram_agent import send_message as send_telegram_message
        msg = (
            f"EMAIL ESCALATION\n\n"
            f"From: {inbound.from_email}\n"
            f"Shop: {inbound.shop_domain or 'unknown'}\n"
            f"Classification: {inbound.classification}\n"
            f"Subject: {(inbound.subject or '')[:100]}\n\n"
            f"Body preview:\n{(inbound.body_text or '')[:300]}"
        )
        send_telegram_message(msg)
    except Exception as exc:
        log.warning("inbound_email: telegram alert failed: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def process_inbound(
    db: Session,
    message_id: str | None,
    from_email: str,
    to_email: str | None,
    subject: str | None,
    body_text: str | None,
    body_html: str | None,
) -> dict:
    """
    Process an inbound merchant email end-to-end.

    Steps:
        0. Self-loop prevention (reject our own addresses)
        1. Dedup on message_id (synthetic ID when NULL)
        2. Loop detection (max 5/hour per sender)
        3. Store email
        4. Resolve merchant
        5. Classify intent
        6. Route to pipeline
        7. Update journey state

    Returns:
        {"status": "processed" | "duplicate" | "error",
         "id": int | None,
         "classification": str | None,
         "routing_action": str | None}
    """
    # 0. Self-loop prevention — never process our own emails
    sender_normalized = _normalize_email(from_email)
    if sender_normalized in _OWN_ADDRESSES:
        from app.core.privacy import mask_email
        log.info("inbound_email: ignoring email from own address %s", mask_email(sender_normalized))
        return {"status": "ignored", "id": None, "classification": "noise", "routing_action": "self_loop_blocked"}

    # 1. Dedup — generate synthetic message_id when absent
    effective_message_id = message_id
    if not effective_message_id:
        # Hash of from + subject + body prefix to create stable dedup key
        dedup_input = f"{from_email}|{subject or ''}|{(body_text or body_html or '')[:500]}"
        effective_message_id = f"synth:{hashlib.sha256(dedup_input.encode()).hexdigest()[:32]}"

    existing = (
        db.query(InboundEmail.id)
        .filter(InboundEmail.message_id == effective_message_id)
        .first()
    )
    if existing:
        log.info("inbound_email: duplicate message_id=%s", effective_message_id)
        return {"status": "duplicate", "id": existing.id, "classification": None, "routing_action": None}

    # 2. Loop detection — suppress if sender is flooding
    loop_cutoff = _now() - timedelta(hours=_LOOP_DETECTION_WINDOW_HOURS)
    recent_count = (
        db.query(InboundEmail.id)
        .filter(
            InboundEmail.from_email == from_email,
            InboundEmail.created_at >= loop_cutoff,
        )
        .count()
    )
    if recent_count >= _LOOP_DETECTION_MAX_EMAILS:
        log.warning(
            "inbound_email: loop detection — %d emails from %s in %dh, suppressing",
            recent_count, from_email, _LOOP_DETECTION_WINDOW_HOURS,
        )
        # Still store for audit, but auto-archive
        inbound = InboundEmail(
            message_id=effective_message_id,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            classification="noise",
            classification_confidence="high",
            classification_method="loop_detection",
            routing_status="archived",
            routing_action="loop_suppressed",
            processed_at=_now(),
        )
        db.add(inbound)
        db.flush()
        return {"status": "loop_suppressed", "id": inbound.id, "classification": "noise", "routing_action": "loop_suppressed"}

    # 3. Store
    inbound = InboundEmail(
        message_id=effective_message_id,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        routing_status="pending",
    )
    db.add(inbound)
    db.flush()

    # 4. Resolve merchant
    shop_domain = _resolve_merchant(db, from_email)
    if shop_domain:
        inbound.shop_domain = shop_domain

    # 5. Classify
    classification, confidence = classify_intent(subject, body_text or body_html)
    inbound.classification = classification
    inbound.classification_confidence = confidence
    inbound.classification_method = "keyword"
    inbound.routing_status = "classified"
    db.flush()

    # 6. Route
    routing_action = _route_email(db, inbound)

    # 7. Update journey state if merchant identified
    if shop_domain:
        try:
            from app.services.email_journey import record_inbound_reply
            record_inbound_reply(db, shop_domain)
        except Exception as exc:
            log.warning("inbound_email: journey update failed: %s", exc)

    log.info(
        "inbound_email: processed id=%d from=%s shop=%s class=%s action=%s",
        inbound.id, from_email, shop_domain, classification, routing_action,
    )

    return {
        "status": "processed",
        "id": inbound.id,
        "classification": classification,
        "routing_action": routing_action,
    }
