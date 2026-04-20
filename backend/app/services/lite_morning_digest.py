"""
lite_morning_digest.py — Daily morning email for Lite merchants.

Founder observation 2026-04-20: Lite is "purely pull" — the merchant
has to log into the dashboard to see the brief. Competitors at $35–150
all push a morning email. This module closes Gap A of the €39-ready
sprint by turning the daily brief into a push channel for Lite.

Design:
  - One email per Lite merchant per calendar day (Europe/Rome).
  - Send window: 08:00–09:59 Europe/Rome (2h window so a 15-min agent
    worker cycle gets ~8 chances to land within the day).
  - Redis dedup: `hs:lite_digest:{shop}:{YYYY-MM-DD}` with 35h TTL.
  - Content: headline + lead story + top action + CTA to dashboard.
    Same payload the BriefHero card shows — one truth channel, pushed.
  - Even on "clean slate" mornings (no findings) we still send a short
    reassurance message. Founder principle: never leave Lite merchants
    wondering if the product is alive. Better one quiet email than
    silence that looks like the service is dead.
  - Goes through the email orchestrator (`submit_intent`). Never bypass.

Public interface:
    run_lite_morning_digest_cycle(db) -> dict  — process all Lite merchants
    _is_morning_rome() -> bool                 — send window gate
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("lite_morning_digest")

_REDIS_PREFIX = "hs:lite_digest:"
_DEDUP_TTL = 126000  # 35h — one send per calendar day + margin
_DASHBOARD_URL = "https://app.hedgesparkhq.com/app/lite"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_rome() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def _is_morning_rome() -> bool:
    """True between 08:00 and 09:59 Europe/Rome.

    The 2h window means a 15-min agent worker cycle has ~8 chances per
    day to land. Redis dedup keeps it to one actual send regardless.
    """
    from zoneinfo import ZoneInfo
    rome_hour = datetime.now(ZoneInfo("Europe/Rome")).hour
    return 8 <= rome_hour < 10


def _digest_sent_today(shop_domain: str, date_key: str) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return bool(rc.get(f"{_REDIS_PREFIX}{shop_domain}:{date_key}"))
    except Exception as exc:
        log.warning("lite_morning_digest: dedup check failed: %s", exc)
    return False


def _mark_sent(shop_domain: str, date_key: str, success: bool) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            value = json.dumps({
                "sent_at": _now().isoformat() + "Z",
                "success": success,
                "date": date_key,
            })
            rc.set(f"{_REDIS_PREFIX}{shop_domain}:{date_key}", value, ex=_DEDUP_TTL)
    except Exception as exc:
        log.warning("lite_morning_digest: mark sent failed: %s", exc)


def _build_email(shop_domain: str, brief: dict) -> Tuple[str, str, str]:
    """Build (subject, html, plain_text) from a brief payload."""
    from app.services.email_templates import _wrap_html, _button, _p, _heading

    shop_name = (
        shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
    )
    signals_count = int(brief.get("signals_count") or 0)
    headline = (brief.get("headline") or "").strip()
    top_product = (brief.get("top_product_label") or "").strip()
    top_action = (brief.get("top_action") or "").strip()

    if signals_count == 0:
        subject = f"Your morning brief — clean slate today, {shop_name}"
    elif top_product:
        subject = f"Today's top signal: {top_product}"
    else:
        plural = "s" if signals_count != 1 else ""
        subject = f"Your morning brief — {signals_count} finding{plural} today"

    body_parts: list[str] = [_heading(f"Good morning, {shop_name}.")]

    if signals_count == 0:
        body_parts.append(_p(
            "No significant findings overnight. Your funnel is clean and your "
            "tracker is watching. The moment any signal crosses threshold — "
            "abandoned intent, leaking pages, hot-product surges — you'll see "
            "it in your dashboard and in tomorrow's brief."
        ))
    else:
        plural = "s" if signals_count != 1 else ""
        body_parts.append(_p(
            f"I reviewed the last 24 hours on your store and ranked "
            f"<strong>{signals_count} finding{plural}</strong> by economic "
            f"impact. Here's today's lead."
        ))

        if top_product:
            lead_block = (
                '<div style="margin:24px 0;padding:20px 22px;background:#0b0b14;'
                'border:1px solid rgba(167,139,250,0.18);border-radius:12px;">'
                '<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;'
                'text-transform:uppercase;color:#a78bfa;margin-bottom:10px;">'
                'Lead story</div>'
                f'<div style="font-size:18px;font-weight:700;color:#f1f5f9;'
                f'margin-bottom:10px;">{top_product}</div>'
            )
            if top_action:
                lead_block += (
                    f'<div style="font-size:14px;line-height:1.6;color:#c8d1dc;">'
                    f'<span style="color:#64748b;">Action suggested:</span> '
                    f'{top_action}</div>'
                )
            lead_block += '</div>'
            body_parts.append(lead_block)

        if headline and headline != top_product:
            body_parts.append(_p(f"<em>{headline}</em>", color="#94a3b8"))

        if signals_count > 1:
            others = signals_count - 1
            other_plural = "s" if others != 1 else ""
            body_parts.append(_p(
                f"The other {others} finding{other_plural} are ranked in the "
                f"brief on your dashboard — work them in that order."
            ))

    body_parts.append(
        '<div style="text-align:center;margin-top:28px;">'
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '</div>'
    )

    html = _wrap_html("Your morning brief", "".join(body_parts))

    # Plain-text version — short, linkable, same narrative beats.
    lines: list[str] = [f"Good morning, {shop_name}.", ""]
    if signals_count == 0:
        lines.append("No significant findings overnight. Funnel clean, tracker watching.")
    else:
        plural = "s" if signals_count != 1 else ""
        lines.append(
            f"{signals_count} finding{plural} today, ranked by economic impact."
        )
        if top_product:
            lines.append("")
            lines.append(f"Lead story: {top_product}")
            if top_action:
                lines.append(f"  → {top_action}")
        if signals_count > 1:
            others = signals_count - 1
            other_plural = "s" if others != 1 else ""
            lines.append("")
            lines.append(
                f"{others} more finding{other_plural} in your dashboard brief."
            )
    lines.append("")
    lines.append(f"Open your dashboard: {_DASHBOARD_URL}")
    plain_text = "\n".join(lines)

    return subject, html, plain_text


def run_lite_morning_digest_cycle(db: Session) -> dict:
    """Process all eligible Lite merchants for today's morning digest.

    Eligibility:
      - install_status == "active"
      - contact_email is not NULL and not empty
      - plan == "lite" OR plan is None (default = Lite)
      - not in _ONBOARDING_BLOCKLIST
      - not already sent today (Redis dedup)

    Each intent goes through the email orchestrator — rate limits,
    PII guards, and suppression flags apply as normal.

    Returns: {processed, sent, skipped, failed, date}
    """
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    from app.services.brief_engine import generate_brief
    from app.services.email_orchestrator import EmailIntent, submit_intent

    date_key = _today_rome()
    summary = {
        "processed": 0, "sent": 0, "skipped": 0, "failed": 0,
        "date": date_key,
    }

    _BATCH_SIZE = 200

    offset = 0
    while True:
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
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

            # Lite-only. Pro merchants get the weekly digest already;
            # stacking a daily email on top would be noise. Merchants
            # with plan=None are treated as Lite (default band).
            plan = (m.plan or "lite").lower()
            if plan != "lite":
                continue

            summary["processed"] += 1

            if _digest_sent_today(m.shop_domain, date_key):
                summary["skipped"] += 1
                continue

            try:
                brief = generate_brief(db, m.shop_domain)
                subject, html, plain_text = _build_email(m.shop_domain, brief)

                intent = EmailIntent(
                    shop_domain=m.shop_domain,
                    email_type="lite_morning_digest",
                    to_email=m.contact_email,
                    subject=subject,
                    html=html,
                    plain_text=plain_text,
                    from_address="HedgeSpark <brief@hedgesparkhq.com>",
                    producer="lite_morning_digest",
                    context={
                        "signals_count": int(brief.get("signals_count") or 0),
                        "top_product": brief.get("top_product_label") or "",
                    },
                )
                submit_intent(db, intent)
                _mark_sent(m.shop_domain, date_key, success=True)
                summary["sent"] += 1
                log.info(
                    "lite_morning_digest: intent queued for %s (%s) signals=%s",
                    m.shop_domain, m.contact_email,
                    brief.get("signals_count", 0),
                )

                # Strada 3.5 — forward the brief to Slack too if the
                # merchant has connected a webhook. Best-effort; a
                # Slack failure doesn't affect the email send.
                try:
                    from app.services.slack_dispatcher import post_daily_brief
                    post_daily_brief(db, m.shop_domain, brief)
                except Exception as exc:
                    log.warning(
                        "lite_morning_digest: Slack forward failed for %s: %s",
                        m.shop_domain, exc,
                    )

            except Exception as exc:
                summary["failed"] += 1
                log.warning(
                    "lite_morning_digest: error for %s: %s",
                    m.shop_domain, exc,
                )

    if summary["processed"] > 0:
        log.info(
            "lite_morning_digest: date=%s processed=%d sent=%d skipped=%d failed=%d",
            date_key, summary["processed"], summary["sent"],
            summary["skipped"], summary["failed"],
        )

    return summary
