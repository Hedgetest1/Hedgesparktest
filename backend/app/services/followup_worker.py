"""
followup_worker.py — 48h follow-up email logic.

Scans merchants eligible for a 48h follow-up after beta invite,
determines the correct variant based on journey state, sends exactly
one follow-up per merchant, and logs everything.

Variants:
    followup_noopen  — invite not opened after 48h (re-engage)
    followup_opened  — opened but not clicked (nudge to action)
    followup_clicked — clicked but not completed onboarding (unblock)

Called by: agent_worker.py phase 7f (every 15min cycle)

Safety guarantees:
    1. Redis SET NX guard BEFORE sending → prevents duplicate on crash/restart
    2. Per-merchant commit → partial failure doesn't re-process already-sent
    3. Journey state check (followup_48h_sent_at IS NULL) → DB-level dedup
    4. Blocklist exclusion

Deterministic. No LLM. No guessing. Exactly-once delivery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.merchant_journey_state import MerchantJourneyState

log = logging.getLogger("followup_worker")

# Blocklist — dead dev placeholders, never email
_BLOCKLIST = {"legacy.myshopify.com"}

_REDIS_FOLLOWUP_PREFIX = "hs:followup_guard:"
_REDIS_FOLLOWUP_TTL = 86400 * 7  # 7 days — long enough to survive any retry window


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _claim_followup_slot(shop_domain: str) -> bool:
    """
    Atomically claim the follow-up send slot for this shop via Redis SET NX.

    Returns True if we got the slot (safe to send), False if already claimed
    (another cycle/process already sent or is sending). This is the critical
    guard against duplicate sends on crash between send_email() and db.commit().
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("followup_worker.claim_slot")
            # Redis down — fall through to DB-level dedup only
            log.warning("followup_worker: Redis unavailable — relying on DB dedup only for %s", shop_domain)
            return True
        key = f"{_REDIS_FOLLOWUP_PREFIX}{shop_domain}"
        # SET NX: only succeeds if key doesn't exist
        result = rc.set(key, "1", nx=True, ex=_REDIS_FOLLOWUP_TTL)
        return bool(result)
    except Exception as exc:
        log.warning("followup_worker: Redis guard failed for %s: %s — proceeding with DB dedup", shop_domain, exc)
        return True


def _release_followup_slot(shop_domain: str) -> None:
    """Release the Redis guard on send failure (so retry is possible)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(f"{_REDIS_FOLLOWUP_PREFIX}{shop_domain}")
    except Exception as exc:
        log.warning("followup_worker: _release_followup_slot failed: %s", exc)


def _is_email_suppressed(shop_domain: str) -> bool:
    """Check if this shop's email is suppressed due to bounce or complaint."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return bool(rc.get(f"hs:email_suppressed:{shop_domain}"))
    except Exception as exc:
        log.warning("followup_worker: _is_email_suppressed failed: %s", exc)
    return False


def _pick_variant(journey: MerchantJourneyState) -> str:
    """
    Determine which follow-up variant to send based on journey state.

    Decision tree:
        clicked → followup_clicked (unblock onboarding)
        opened  → followup_opened  (nudge to click)
        neither → followup_noopen  (re-engage)
    """
    if journey.beta_invite_clicked_at:
        return "followup_clicked"
    if journey.beta_invite_opened_at:
        return "followup_opened"
    return "followup_noopen"


def run_followup_cycle(db: Session) -> dict:
    """
    Scan and send 48h follow-up emails.

    Each merchant is processed independently with its own commit boundary.
    Redis SET NX guard prevents duplicate sends across crash/restart cycles.

    Returns summary dict:
        {"eligible": int, "sent": int, "skipped": int, "failed": int, "errors": list}
    """
    from app.services.email_journey import get_followup_eligible

    summary = {"eligible": 0, "sent": 0, "skipped": 0, "failed": 0, "errors": []}

    try:
        eligible = get_followup_eligible(db)
    except Exception as exc:
        log.error("followup_worker: query failed: %s", exc)
        return {"eligible": 0, "sent": 0, "skipped": 0, "failed": 0, "errors": [str(exc)]}

    summary["eligible"] = len(eligible)

    if not eligible:
        return summary

    log.info("followup_worker: found %d eligible merchants", len(eligible))

    for journey in eligible:
        shop_domain = journey.shop_domain

        # Skip blocklisted
        if shop_domain in _BLOCKLIST:
            summary["skipped"] += 1
            continue

        try:
            _send_one_followup(db, journey, summary)
            # Per-merchant commit — prevents one failure from rolling back others
            db.commit()
        except Exception as exc:
            log.error("followup_worker: error for %s: %s", shop_domain, exc)
            summary["failed"] += 1
            summary["errors"].append(f"{shop_domain}: {exc}")
            db.rollback()

    return summary


def _send_one_followup(
    db: Session,
    journey: MerchantJourneyState,
    summary: dict,
) -> None:
    """Send a single follow-up email for one merchant."""
    from app.services.email_journey import record_followup_sent

    shop_domain = journey.shop_domain

    # Re-check journey state inside transaction (another cycle may have committed)
    fresh_journey = (
        db.query(MerchantJourneyState)
        .filter(MerchantJourneyState.shop_domain == shop_domain)
        .first()
    )
    if fresh_journey and fresh_journey.followup_48h_sent_at is not None:
        summary["skipped"] += 1
        return

    # Look up merchant for contact email
    merchant = (
        db.query(Merchant)
        .filter(Merchant.shop_domain == shop_domain)
        .first()
    )
    if not merchant or merchant.install_status != "active":
        # Mark journey to stop re-fetching this merchant every cycle
        if fresh_journey:
            fresh_journey.followup_48h_sent_at = _now()
            fresh_journey.followup_48h_variant = "skipped:merchant_unavailable"
            fresh_journey.updated_at = _now()
            db.flush()
        log.warning("followup_worker: merchant unavailable for %s — marked to skip", shop_domain)
        summary["skipped"] += 1
        return

    to_email = (merchant.contact_email or "").strip()
    if not to_email:
        log.info("followup_worker: no contact email for %s", shop_domain)
        summary["skipped"] += 1
        return

    # Check bounce/complaint suppression (Redis + DB)
    if _is_email_suppressed(shop_domain) or (fresh_journey and fresh_journey.email_suppressed):
        log.info("followup_worker: email suppressed (bounce/complaint) for %s", shop_domain)
        summary["skipped"] += 1
        return

    # Check email budget — follow-ups share the same Resend quota as lifecycle emails
    try:
        from app.core.resend_usage import get_resend_usage, RESEND_MONTHLY_LIMIT
        usage = get_resend_usage(db)
        if usage["sent"] >= RESEND_MONTHLY_LIMIT:
            log.warning("followup_worker: email budget exhausted (%d/%d) — skipping %s",
                        usage["sent"], RESEND_MONTHLY_LIMIT, shop_domain)
            summary["skipped"] += 1
            return
    except Exception as exc:
        log.warning("followup_worker: _send_one_followup failed: %s", exc)
        pass  # Budget check failure is non-fatal — proceed with send

    # Redis SET NX guard — claim send slot BEFORE calling Resend
    if not _claim_followup_slot(shop_domain):
        log.info("followup_worker: Redis guard blocked duplicate for %s", shop_domain)
        summary["skipped"] += 1
        return

    # Pick variant from FRESH journey state (not stale query result)
    variant = _pick_variant(fresh_journey or journey)

    # Render template
    sent = False
    try:
        from app.services.email_templates import render_email
        shop_name = shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
        ctx = {
            "shop_name": shop_name,
            "merchant_name": shop_name,
        }
        subject, html, plain_text = render_email(variant, ctx)

        # Send through orchestrator — immediate mode (full governance, low latency)
        from app.services.email_orchestrator import EmailIntent, send_immediate
        intent = EmailIntent(
            shop_domain=shop_domain,
            email_type=variant,
            to_email=to_email,
            subject=subject,
            html=html,
            plain_text=plain_text,
            from_address="Andrea from HedgeSpark <andrea@hedgesparkhq.com>",
            producer="followup_worker",
        )
        result = send_immediate(db, intent)
        sent = result["status"] == "sent"
    except Exception as exc:
        log.error("followup_worker: render/send error variant=%s shop=%s: %s", variant, shop_domain, exc)
        summary["failed"] += 1
        return
    finally:
        # Release Redis guard if send did not succeed
        if not sent:
            _release_followup_slot(shop_domain)

    if sent:
        # Record in journey state (audit logging handled by orchestrator)
        record_followup_sent(db, shop_domain, variant, intent.intent_id)

        summary["sent"] += 1
        from app.core.privacy import mask_email
        log.info(
            "followup_worker: sent variant=%s to=%s shop=%s",
            variant, mask_email(to_email), shop_domain,
        )
    else:
        summary["failed"] += 1
        from app.core.privacy import mask_email
        log.warning(
            "followup_worker: blocked variant=%s to=%s shop=%s",
            variant, mask_email(to_email), shop_domain,
        )
