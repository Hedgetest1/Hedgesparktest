"""
merchant_digest.py — Automated merchant email digest delivery.

Sends weekly intelligence summaries to merchants with valid email addresses.
Uses existing weekly_digest.assemble_digest() + digest_formatter.format_digest()
for content generation. Adds automated scheduling, dedup, and operator visibility.

Schedule: runs once per week (Monday, Europe/Rome calendar day).
Dedup: Redis-backed, one digest per merchant per calendar week.
Delivery: via existing app.core.email.send_email() (Resend).

Public interface:
    run_merchant_digest_cycle(db) -> dict  — process all eligible merchants
    get_digest_delivery_status(db) -> dict — operator fleet view
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("merchant_digest")

_REDIS_PREFIX = "hs:mdigest:"
_DEDUP_TTL = 691200  # 8 days — covers weekly cycle with margin


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _current_week_key() -> str:
    """ISO week key for dedup, e.g. '2026-W13'."""
    from zoneinfo import ZoneInfo
    rome_now = datetime.now(ZoneInfo("Europe/Rome"))
    return rome_now.strftime("%G-W%V")


def _is_monday_rome() -> bool:
    """Check if today is Monday in Europe/Rome timezone."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).weekday() == 0


def _digest_sent_for_merchant(shop_domain: str, week_key: str) -> bool:
    """Check if digest was already sent to this merchant this week."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return bool(rc.get(f"{_REDIS_PREFIX}{shop_domain}:{week_key}"))
    except Exception as exc:
        log.warning("merchant_digest: dedup check failed: %s", exc)
    return False


def _mark_digest_sent(shop_domain: str, week_key: str, success: bool):
    """Record digest delivery result for dedup and operator visibility."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            value = json.dumps({
                "sent_at": _now().isoformat() + "Z",
                "success": success,
                "week": week_key,
            })
            rc.set(f"{_REDIS_PREFIX}{shop_domain}:{week_key}", value, ex=_DEDUP_TTL)
    except Exception as exc:
        log.warning("merchant_digest: mark sent failed: %s", exc)


def run_merchant_digest_cycle(db: Session) -> dict:
    """
    Process all eligible merchants and submit weekly digest intents.

    All sends go through the email orchestrator.

    Eligibility:
      - install_status == "active"
      - contact_email is not NULL and not empty
      - billing_active == True (Pro merchants only — digest is Pro value)
      - not already sent this week (Redis dedup)

    Returns summary: {processed, sent, skipped, failed, no_data}
    """
    week_key = _current_week_key()
    summary = {"processed": 0, "sent": 0, "skipped": 0, "failed": 0, "no_data": 0, "week": week_key}

    # Get eligible merchants — paginated to avoid memory spike at 10k+ merchants
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    _BATCH_SIZE = 200

    offset = 0
    while True:
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
                Merchant.billing_active == True,
            )
            .order_by(Merchant.id)
            .offset(offset)
            .limit(_BATCH_SIZE)
            .all()
        )
        if not merchants:
            break
        offset += _BATCH_SIZE

        for m in merchants:
            if m.shop_domain in _ONBOARDING_BLOCKLIST:
                continue

            summary["processed"] += 1

            # Dedup check
            if _digest_sent_for_merchant(m.shop_domain, week_key):
                summary["skipped"] += 1
                continue

            # Assemble digest
            try:
                from app.services.weekly_digest import assemble_digest
                digest = assemble_digest(db, m.shop_domain, merchant_plan=m.plan or "lite")

                if not digest:
                    summary["no_data"] += 1
                    _mark_digest_sent(m.shop_domain, week_key, success=True)
                    log.info("merchant_digest: %s — no data, skipping", m.shop_domain)
                    continue

                # Format
                from app.services.digest_formatter import format_digest
                html, plain_text = format_digest(digest)

                # Submit intent to email orchestrator
                shop_name = m.shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
                subject = f"Your Weekly Intelligence — {shop_name}"

                from app.services.email_orchestrator import EmailIntent, submit_intent
                intent = EmailIntent(
                    shop_domain=m.shop_domain,
                    email_type="weekly_digest",
                    to_email=m.contact_email,
                    subject=subject,
                    html=html,
                    plain_text=plain_text,
                    from_address="HedgeSpark <digest@hedgesparkhq.com>",
                    producer="merchant_digest",
                )
                submit_intent(db, intent)
                _mark_digest_sent(m.shop_domain, week_key, success=True)
                summary["sent"] += 1
                log.info("merchant_digest: intent queued for %s (%s)", m.shop_domain, m.contact_email)

            except Exception as exc:
                summary["failed"] += 1
                log.warning("merchant_digest: error for %s: %s", m.shop_domain, exc)

    if summary["processed"] > 0:
        log.info(
            "merchant_digest: week=%s processed=%d sent=%d skipped=%d failed=%d no_data=%d",
            week_key, summary["processed"], summary["sent"],
            summary["skipped"], summary["failed"], summary["no_data"],
        )

    return summary


def get_digest_delivery_status(db: Session) -> dict:
    """
    Operator view: merchant digest delivery status for current week.

    Returns:
        {
            "week": str,
            "eligible_merchants": int,
            "delivered": int,
            "pending": int,
            "failed_shops": [str],
        }
    """
    week_key = _current_week_key()

    # Count eligible
    try:
        eligible = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
                Merchant.billing_active == True,
            )
            .count()
        )
    except Exception as exc:
        log.warning("merchant_digest: eligible count query failed: %s", exc)
        eligible = 0

    # Check delivery status from Redis
    delivered = 0
    failed_shops: list[str] = []

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor, match=f"{_REDIS_PREFIX}*:{week_key}", count=100)
                for key in keys:
                    try:
                        raw = rc.get(key)
                        if raw:
                            data = json.loads(raw)
                            if data.get("success"):
                                delivered += 1
                            else:
                                shop = key.replace(_REDIS_PREFIX, "").replace(f":{week_key}", "")
                                failed_shops.append(shop)
                    except Exception as exc:
                        log.warning("merchant_digest: delivery status parse failed: %s", exc)
                        continue
                if cursor == 0:
                    break
    except Exception as exc:
        log.warning("merchant_digest: redis delivery scan failed: %s", exc)

    return {
        "week": week_key,
        "eligible_merchants": eligible,
        "delivered": delivered,
        "pending": max(0, eligible - delivered - len(failed_shops)),
        "failed_shops": failed_shops,
    }
