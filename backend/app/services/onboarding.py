"""
onboarding.py — Automated merchant onboarding orchestrator.

Ensures every new merchant reaches "ready" state without human intervention.

Flow:
    1. Verify access_token is valid (decryptable)
    2. Ensure webhook is registered (idempotent)
    3. Ensure tracker script tag is installed (idempotent)
    4. Mark onboarding_status = "ready"

On failure:
    - Mark onboarding_status = "failed" with error detail
    - Write ops_alert for operator/agent visibility
    - Orchestrator can retry on next cycle (with cooldown)

Idempotent: safe to call multiple times. Already-ready merchants are skipped.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant
from app.services.audit import write_audit_log

log = logging.getLogger("onboarding")

_APP_URL = os.getenv("APP_URL", "")


@dataclass
class OnboardingResult:
    shop_domain: str
    status: str = "pending"         # ready | failed | skipped | already_ready
    steps_completed: list[str] = None
    error: str | None = None

    def __post_init__(self):
        if self.steps_completed is None:
            self.steps_completed = []


def run_onboarding(db: Session, merchant: Merchant) -> OnboardingResult:
    """
    Run the full onboarding sequence for a merchant.

    Idempotent — safe to call on already-ready merchants.
    Returns a structured result. Never raises.
    """
    result = OnboardingResult(shop_domain=merchant.shop_domain)

    # Skip if already ready
    if merchant.onboarding_status == "ready":
        result.status = "already_ready"
        return result

    # Skip if not active
    if merchant.install_status != "active":
        result.status = "skipped"
        result.error = "install_inactive"
        return result

    # Backoff: skip if retry is scheduled for later
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if merchant.onboarding_next_retry_at and now < merchant.onboarding_next_retry_at:
        result.status = "skipped"
        result.error = "backoff_active"
        return result

    # Give up after 5 retries — escalate to operator
    _MAX_RETRIES = 5
    retry_count = merchant.onboarding_retry_count or 0
    if retry_count >= _MAX_RETRIES and merchant.onboarding_status == "failed":
        result.status = "skipped"
        result.error = "max_retries_exceeded"
        return result

    # Transition to configuring
    merchant.onboarding_status = "configuring"
    merchant.onboarding_error = None
    db.flush()

    try:
        # Step 1: Verify access token
        token = decrypt_token(merchant.access_token)
        if not token:
            return _fail(db, merchant, result, "token_invalid_or_missing")
        result.steps_completed.append("token_verified")

        # Step 2: Ensure webhook registered
        if not _APP_URL:
            return _fail(db, merchant, result, "APP_URL_not_configured")

        webhook_ok = _ensure_webhook(merchant, token)
        if not webhook_ok:
            return _fail(db, merchant, result, "webhook_registration_failed")
        result.steps_completed.append("webhook_configured")

        # Step 3: Ensure tracker script tag
        tracker_ok = _ensure_tracker(merchant, token)
        if not tracker_ok:
            return _fail(db, merchant, result, "tracker_installation_failed")
        result.steps_completed.append("tracker_configured")

        # All steps passed — mark ready
        merchant.onboarding_status = "ready"
        merchant.onboarding_error = None
        db.flush()

        write_audit_log(
            db,
            actor_type="system",
            actor_name="onboarding",
            action_type="onboarding_complete",
            target_type="merchant",
            target_id=merchant.shop_domain,
            shop_domain=merchant.shop_domain,
            after_state={"steps": result.steps_completed},
            status="completed",
            approval_mode="autonomous",
        )

        result.status = "ready"
        log.info("onboarding: complete shop=%s steps=%s", merchant.shop_domain, result.steps_completed)

        # Send welcome email via orchestrator (immediate mode, never blocks onboarding)
        try:
            from app.services.merchant_email_service import submit_lifecycle_intent
            submit_lifecycle_intent(db, merchant.shop_domain, "welcome")
            # Intent will be flushed by the next orchestrator cycle or agent_worker flush
        except Exception as exc:
            log.warning("onboarding: welcome email intent failed (non-fatal): %s", exc)

        return result

    except Exception as exc:
        db.rollback()
        return _fail(db, merchant, result, f"unexpected: {str(exc)[:200]}")


def _fail(db: Session, merchant: Merchant, result: OnboardingResult, error: str) -> OnboardingResult:
    """Mark onboarding as failed, set exponential backoff, and write alert."""
    from datetime import timedelta

    merchant.onboarding_status = "failed"
    merchant.onboarding_error = error[:500]

    # Exponential backoff: 1h, 4h, 12h, 24h, then give up
    _BACKOFF_HOURS = [1, 4, 12, 24, 24]
    retry_count = (merchant.onboarding_retry_count or 0) + 1
    merchant.onboarding_retry_count = retry_count
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if retry_count <= len(_BACKOFF_HOURS):
        backoff_hours = _BACKOFF_HOURS[retry_count - 1]
        merchant.onboarding_next_retry_at = now + timedelta(hours=backoff_hours)
    else:
        # Max retries exceeded — no more retries
        merchant.onboarding_next_retry_at = None

    db.flush()

    severity = "warning"
    # Escalate to critical after 3+ retries
    if retry_count >= 3:
        severity = "critical" if retry_count >= 5 else "warning"

    from app.services.alerting import write_alert
    write_alert(
        db,
        severity=severity,
        source="onboarding",
        alert_type="onboarding_failed",
        shop_domain=merchant.shop_domain,
        summary=f"Onboarding failed (attempt {retry_count}): {error}",
        detail={"steps_completed": result.steps_completed, "error": error, "retry_count": retry_count},
    )
    db.flush()

    result.status = "failed"
    result.error = error
    log.warning(
        "onboarding: FAILED shop=%s error=%s retry=%d next_retry=%s",
        merchant.shop_domain, error, retry_count,
        merchant.onboarding_next_retry_at.isoformat() if merchant.onboarding_next_retry_at else "GIVEN_UP",
    )
    return result


def _ensure_webhook(merchant: Merchant, token: str) -> bool:
    """Ensure the app/uninstalled webhook is registered. Returns True on success."""
    import asyncio
    from app.services.shopify_admin import ensure_orders_webhook

    try:
        loop = asyncio.new_event_loop()
        wh_id, created = loop.run_until_complete(
            ensure_orders_webhook(merchant.shop_domain, token, _APP_URL)
        )
        loop.close()

        if wh_id:
            merchant.webhook_id = wh_id
            if created:
                merchant.webhook_registered_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return True
        return False
    except Exception as exc:
        log.warning("onboarding: webhook error shop=%s: %s", merchant.shop_domain, exc)
        return False


def _ensure_tracker(merchant: Merchant, token: str) -> bool:
    """Ensure the tracker script tag is installed. Returns True on success."""
    import asyncio
    from app.services.shopify_admin import ensure_tracker_script_tag
    from app.core.tracker_version import get_tracker_url

    tracker_url = get_tracker_url()
    if not tracker_url:
        log.warning("onboarding: tracker URL not configured (APP_URL missing)")
        return False

    try:
        loop = asyncio.new_event_loop()
        tag_id, created = loop.run_until_complete(
            ensure_tracker_script_tag(merchant.shop_domain, token, tracker_url)
        )
        loop.close()

        if tag_id:
            merchant.script_tag_id = tag_id
            if created:
                merchant.script_tag_installed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return True
        return False
    except Exception as exc:
        log.warning("onboarding: tracker error shop=%s: %s", merchant.shop_domain, exc)
        return False


# ---------------------------------------------------------------------------
# Batch runner for worker integration
# ---------------------------------------------------------------------------

# Shops that should never be onboarded — dead/dev/legacy placeholders.
# These have no valid Shopify credentials and will always fail, polluting
# logs and alerts on every 15-minute worker cycle.
_ONBOARDING_BLOCKLIST = frozenset({
    "legacy.myshopify.com",
})


def run_pending_onboarding(db: Session) -> dict:
    """
    Find and onboard all merchants with onboarding_status in (pending, failed).

    Failed merchants are retried — the onboarding flow is idempotent.
    Blocklisted shops (legacy/dev stubs) are permanently skipped.
    Returns summary: {"processed": N, "ready": N, "failed": N, "skipped": N}
    """
    merchants = (
        db.query(Merchant)
        .filter(
            Merchant.install_status == "active",
            Merchant.onboarding_status.in_(["pending", "failed"]),
        )
        .all()
    )

    summary = {"processed": 0, "ready": 0, "failed": 0, "skipped": 0}

    for m in merchants:
        # Skip blocklisted shops — they have no credentials and always fail
        if m.shop_domain in _ONBOARDING_BLOCKLIST:
            summary["skipped"] += 1
            continue

        # Skip merchants with no access token — cannot call Shopify APIs
        if not m.access_token:
            summary["skipped"] += 1
            continue

        result = run_onboarding(db, m)
        summary["processed"] += 1
        if result.status == "ready":
            summary["ready"] += 1
        elif result.status == "failed":
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        try:
            db.commit()
        except Exception:
            db.rollback()

    if summary["processed"] > 0:
        log.info(
            "onboarding: batch complete — processed=%d ready=%d failed=%d skipped=%d",
            summary["processed"], summary["ready"], summary["failed"], summary["skipped"],
        )

    return summary
