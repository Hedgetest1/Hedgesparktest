"""
silence_detector.py — Detect merchants going silent and trigger re-engagement.

A merchant is "silent" if:
    - install_status == "active"
    - onboarding_status == "ready"
    - 0 events in the last 14 days
    - not already marked silent this month

Actions:
    - Create info-level ops_alert (type=merchant_silent)
    - Update journey state to "silent" stage
    - Queue re-engagement email (via lifecycle email system)

Called by: agent_worker.py phase 7h (every 15min cycle)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("silence_detector")

_SILENCE_WINDOW_DAYS = 14
_REDIS_SILENCE_PREFIX = "hs:silence_detected:"
_REDIS_SILENCE_TTL = 86400 * 14  # 14 days — allow re-engagement attempt after 2 weeks


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_silence_detection(db: Session) -> dict:
    """
    Detect silent merchants and create alerts.

    Submits re-engagement emails as intents to the email orchestrator.

    Returns {"detected": int, "alerted": int, "skipped": int}
    """
    summary = {"detected": 0, "alerted": 0, "skipped": 0}

    cutoff = _now() - timedelta(days=_SILENCE_WINDOW_DAYS)

    # Find active, ready merchants with no events in the window
    silent_shops = db.execute(text("""
        SELECT m.shop_domain, m.contact_email
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.onboarding_status = 'ready'
          AND m.shop_domain != 'legacy.myshopify.com'
          AND NOT EXISTS (
              SELECT 1 FROM events e
              WHERE e.shop_domain = m.shop_domain
                AND e.timestamp > :cutoff_ms
          )
        LIMIT 20
    """), {"cutoff_ms": int(cutoff.timestamp() * 1000)}).fetchall()

    if not silent_shops:
        # No currently-silent shops — every previously-alerted merchant
        # has resumed activity. Heal any open merchant_silent alerts.
        try:
            from app.services.alerting import heal_per_shop_alerts
            heal_per_shop_alerts(
                db,
                source="silence_detector",
                alert_type="merchant_silent",
                currently_affected_shops=set(),
            )
        except Exception as exc:
            log.debug("silence_detector: heal_per_shop_alerts failed: %s", exc)
        return summary

    currently_silent = {row[0] for row in silent_shops}

    for row in silent_shops:
        shop = row[0]
        summary["detected"] += 1

        # Redis dedup — only alert once per 30 days
        if _is_already_alerted(shop):
            summary["skipped"] += 1
            continue

        # Create ops_alert + send re-engagement email
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="info",
                source="silence_detector",
                alert_type="merchant_silent",
                summary=f"Merchant {shop} has 0 events in {_SILENCE_WINDOW_DAYS} days — possible churn risk",
                shop_domain=shop,
            )

            # Submit re-engagement email intent to orchestrator
            contact = row[1]
            if contact:
                _submit_reengagement_intent(db, shop, contact)

            _mark_alerted(shop)
            summary["alerted"] += 1

            log.info("silence_detector: detected silent merchant %s — alert + re-engagement sent", shop)
        except Exception as exc:
            log.warning("silence_detector: alert failed for %s: %s", shop, exc)

    # Heal merchants that previously had merchant_silent alerts but
    # are no longer silent — currently_silent is the ground-truth set
    # of shops still failing the condition this scan cycle.
    try:
        from app.services.alerting import heal_per_shop_alerts
        heal_per_shop_alerts(
            db,
            source="silence_detector",
            alert_type="merchant_silent",
            currently_affected_shops=currently_silent,
        )
    except Exception as exc:
        log.debug("silence_detector: heal_per_shop_alerts failed: %s", exc)

    return summary


def _submit_reengagement_intent(db: Session, shop_domain: str, contact_email: str) -> None:
    """Submit a re-engagement email as an intent to the orchestrator.

    Uses the shared brand wrapper for visual consistency.
    """
    from app.services.email_orchestrator import EmailIntent, submit_intent
    from app.services.email_templates import _wrap_html, _heading, _p, _button

    shop_name = shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
    subject = f"{shop_name} — no visitor activity in {_SILENCE_WINDOW_DAYS} days"
    plain = (
        f"Hi,\n\n"
        f"HedgeSpark hasn't recorded any visitor activity on {shop_name} "
        f"in the last {_SILENCE_WINDOW_DAYS} days. This usually means the tracking "
        f"script isn't loading on your storefront.\n\n"
        f"If you're still using HedgeSpark, open your dashboard to check the connection "
        f"status. The setup panel will show what needs attention.\n\n"
        f"Check your dashboard: https://app.hedgesparkhq.com\n\n"
        f"If something is broken, reply to this email — we'll look into it.\n\n"
        f"Andrea\n"
        f"HedgeSpark"
    )
    body = (
        _heading("No visitor activity detected")
        + _p(
            f"HedgeSpark hasn't recorded any visitor activity on "
            f"<strong style='color:#f1f5f9;'>{shop_name}</strong> "
            f"in the last {_SILENCE_WINDOW_DAYS} days. "
            f"This usually means the tracking script isn't loading on your storefront."
        )
        + _p(
            "Your dashboard shows the connection status and will guide you "
            "through any steps needed to restore tracking.",
            color="#94a3b8",
        )
        + _button("Check your dashboard", "https://app.hedgesparkhq.com")
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If something is broken, reply to this email — we'll look into it."
        + "</p>"
    )
    html = _wrap_html(subject, body)

    intent = EmailIntent(
        shop_domain=shop_domain,
        email_type="reengagement",
        to_email=contact_email,
        subject=subject,
        html=html,
        plain_text=plain,
        from_address="HedgeSpark <dev@hedgesparkhq.com>",
        producer="silence_detector",
    )
    submit_intent(db, intent)



def _is_already_alerted(shop_domain: str) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return bool(rc.get(f"{_REDIS_SILENCE_PREFIX}{shop_domain}"))
    except Exception as exc:
        log.warning("silence_detector: _is_already_alerted failed: %s", exc)
    return False


def _mark_alerted(shop_domain: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.set(f"{_REDIS_SILENCE_PREFIX}{shop_domain}", "1", ex=_REDIS_SILENCE_TTL)
    except Exception as exc:
        log.warning("silence_detector: _mark_alerted failed: %s", exc)
