"""
night_shift_email.py — MA-6 moat amplification.

Sends the Night Shift digest as an email the morning after the pipeline
runs. Amplifies MA-3 (public self-heal counter on /status) by putting
the receipt-style report directly in the merchant's inbox every day:
"here's what HedgeSpark did while you slept".

Design
------
1. Build the EmailIntent from the existing night_shift_agent report
   (no duplicate compute — reuses generate_for_shop's cached output).
2. Route through email_orchestrator.submit_intent so it respects
   merchant.email_paused, rate limits, merge-with-other-intents, and
   governance.
3. Per-email-type opt-out via Redis key `hs:email_optout:{shop}:night_shift_digest`.
   A merchant can pause just this email without pausing all transactional
   lifecycle emails.
4. Pro-tier only — Lite merchants don't get Night Shift reports on the
   dashboard, so they don't get the email either.
5. Dedup per shop per UTC day via Redis key so re-runs of the nightly
   worker don't send twice.

Producer name for orchestration = "night_shift_email".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("night_shift_email")

# Dedup window — one email per shop per UTC day. Refreshed by actual
# send; skipped sends do NOT consume the slot so we retry if the first
# attempt was blocked by rate-limit or orchestrator-governance decline.
_DEDUP_TTL_SECONDS = 30 * 3600

# Opt-out preference TTL — 1 year, refreshed on every read/write so an
# active merchant's preference effectively sticks forever. Matches the
# pattern goals.py uses for merchant-set preferences. Without a TTL the
# key would violate the "every Redis key has a TTL" 10k-scale invariant.
_OPTOUT_TTL_SECONDS = 365 * 24 * 3600

# Per-email-type opt-out registry. A merchant POSTing to
# /merchant/email-preferences can set any email_type to paused=true.
_OPTOUT_KEY_FMT = "hs:email_optout:{shop}:{email_type}"
_SENT_TODAY_KEY_FMT = "hs:ns_email_sent:{shop}:{day}"
_EMAIL_TYPE = "night_shift_digest"


def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _is_opted_out(shop_domain: str) -> bool:
    rc = _redis()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("night_shift_email.optout_check.redis_down")
        return False
    key = _OPTOUT_KEY_FMT.format(shop=shop_domain, email_type=_EMAIL_TYPE)
    try:
        val = rc.get(key)
        if val:
            # Refresh TTL on every read so active-merchant preferences
            # keep rolling forward and never silently expire.
            try:
                rc.expire(key, _OPTOUT_TTL_SECONDS)
            except Exception as exc:
                # SILENT-EXCEPT-OK: TTL refresh is best-effort. The key is
                # already present; failing to extend just means a 1-year-
                # quiet merchant could eventually re-opt-in automatically,
                # which is a safer failure mode than breaking the read path.
                log.debug("night_shift_email: optout TTL refresh failed: %s", exc)
            return True
        return False
    except Exception as exc:
        log.warning("night_shift_email: optout check failed: %s", exc)
        return False


def set_optout(shop_domain: str, opted_out: bool) -> bool:
    """Public helper used by the email-preferences endpoint. Writes the
    opt-out flag (or clears it) in Redis. Returns True on success."""
    rc = _redis()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("night_shift_email.set_optout.redis_down")
        return False
    key = _OPTOUT_KEY_FMT.format(shop=shop_domain, email_type=_EMAIL_TYPE)
    try:
        if opted_out:
            rc.setex(key, _OPTOUT_TTL_SECONDS, "1")
        else:
            rc.delete(key)
        return True
    except Exception as exc:
        log.warning("night_shift_email: set_optout failed: %s", exc)
        return False


def _already_sent_today(shop_domain: str) -> bool:
    rc = _redis()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("night_shift_email.dedup_check.redis_down")
        return False
    try:
        return bool(rc.get(_SENT_TODAY_KEY_FMT.format(shop=shop_domain, day=_day_key())))
    except Exception as exc:
        log.warning("night_shift_email: dedup check failed: %s", exc)
        return False


def _mark_sent_today(shop_domain: str) -> None:
    rc = _redis()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("night_shift_email.mark_sent.redis_down")
        return
    try:
        rc.setex(
            _SENT_TODAY_KEY_FMT.format(shop=shop_domain, day=_day_key()),
            _DEDUP_TTL_SECONDS,
            "1",
        )
    except Exception as exc:
        log.warning("night_shift_email: sent-dedup write failed: %s", exc)


def _resolve_recipient(db: Session, shop_domain: str) -> str | None:
    """Resolve the merchant's contact_email. Returns None if the
    merchant row has no contact email — we never fabricate one."""
    try:
        from app.models.merchant import Merchant
        m = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if m is None:
            return None
        email = getattr(m, "contact_email", None)
        if not email or "@" not in email:
            return None
        return email.strip()
    except Exception as exc:
        log.warning("night_shift_email: merchant lookup failed: %s", exc)
        return None


def send_for_shop(db: Session, shop_domain: str, report: dict) -> dict:
    """
    Build + submit the night-shift digest intent for one shop. Returns
    {"status": "sent"|"skipped"|"failed", "reason": str|None}.

    Idempotent per UTC day: a second call on the same day is a no-op.
    Never raises — caller is the nightly worker and must not be broken
    by one merchant's email failing.
    """
    if not report:
        return {"status": "skipped", "reason": "no_report"}
    if _is_opted_out(shop_domain):
        return {"status": "skipped", "reason": "opted_out"}
    if _already_sent_today(shop_domain):
        return {"status": "skipped", "reason": "already_sent_today"}

    to_email = _resolve_recipient(db, shop_domain)
    if not to_email:
        return {"status": "skipped", "reason": "no_contact_email"}

    # Build the renderer context from the existing night_shift report
    # shape. No recomputation — all fields come from generate_for_shop.
    shop_name = (
        shop_domain.split(".myshopify.com")[0].replace("-", " ").title()
        if shop_domain else "your store"
    )
    top_action = report.get("top_action") or {}
    journal = report.get("journal") or []
    ctx = {
        "shop_name": shop_name,
        "headline": report.get("headline") or "Overnight shift complete",
        "narrative": report.get("narrative") or "",
        "sleep_score": report.get("sleep_score"),
        "sleep_label": report.get("sleep_label"),
        "prevented_eur_24h": report.get("prevented_eur_24h") or 0,
        "currency": report.get("currency") or "USD",
        "rars_total": (report.get("rars") or {}).get("total_at_risk_eur"),
        "top_action": {
            "source": top_action.get("source"),
            "narrative": top_action.get("narrative"),
            "impact_eur": top_action.get("loss_eur") or top_action.get("impact_eur"),
        } if top_action else None,
        "journal": [
            {
                "signal": e.get("signal"),
                "verdict": e.get("verdict"),
                "reason": e.get("reason"),
                "weight": e.get("weight"),
            }
            for e in journal if isinstance(e, dict)
        ],
    }

    try:
        from app.services.email_templates import render_email
        subject, html, plain = render_email(_EMAIL_TYPE, ctx)
    except Exception as exc:
        log.warning("night_shift_email: render failed for %s: %s", shop_domain, exc)
        return {"status": "failed", "reason": f"render_error:{type(exc).__name__}"}

    try:
        from app.services.email_orchestrator import EmailIntent, submit_intent
        intent = EmailIntent(
            shop_domain=shop_domain,
            email_type=_EMAIL_TYPE,
            to_email=to_email,
            subject=subject,
            html=html,
            plain_text=plain,
            producer="night_shift_email",
            context={"day": _day_key(), "shop_name": shop_name},
        )
        intent_id = submit_intent(db, intent)
    except Exception as exc:
        log.warning("night_shift_email: submit failed for %s: %s", shop_domain, exc)
        return {"status": "failed", "reason": f"submit_error:{type(exc).__name__}"}

    _mark_sent_today(shop_domain)
    return {"status": "sent", "reason": None, "intent_id": intent_id}


def run_for_all_pro(db: Session) -> dict:
    """
    Nightly worker hook: after night_shift_agent.run_nightly_for_all_pro
    generates reports, this function sends an email per shop. Returns
    a tally {"sent": N, "skipped": N, "failed": N, "reasons": {...}}.

    Never raises — one bad shop must not block the rest.
    """
    tally = {"sent": 0, "skipped": 0, "failed": 0, "reasons": {}}
    try:
        from app.models.merchant import Merchant
        shops = (
            db.query(Merchant.shop_domain)
            .filter(Merchant.plan == "pro", Merchant.billing_active == True)  # noqa: E712
            .all()
        )
    except Exception as exc:
        log.warning("night_shift_email: merchant list failed: %s", exc)
        return tally

    from app.services.night_shift_agent import get_latest_for_shop

    for row in shops:
        shop = row[0] if not isinstance(row, str) else row
        if not shop:
            continue
        try:
            report = get_latest_for_shop(shop)
        except Exception as exc:
            log.warning("night_shift_email: get_latest failed for %s: %s", shop, exc)
            tally["failed"] += 1
            tally["reasons"].setdefault("report_fetch_error", 0)
            tally["reasons"]["report_fetch_error"] += 1
            continue

        result = send_for_shop(db, shop, report or {})
        status = result.get("status", "failed")
        tally.setdefault(status, 0)
        tally[status] += 1
        if result.get("reason"):
            tally["reasons"].setdefault(result["reason"], 0)
            tally["reasons"][result["reason"]] += 1

    return tally
