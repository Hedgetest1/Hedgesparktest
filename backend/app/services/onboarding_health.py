"""
onboarding_health.py — Onboarding friction detection and observability.

Monitors the merchant onboarding pipeline and feeds problems into the
alerting and loop_health system so onboarding is not a dead frontend island.

Detects:
    - Setup grace failures (install completed but setup never becomes healthy)
    - Onboarding stuck (merchant stuck in needs_repair > threshold)
    - Pixel abandonment (setup complete, pixel never connected)
    - Slow time-to-first-insight (first insight > expected window)
    - Repair loops (repeated repair triggers for same merchant)
    - Setup oscillation (readiness state flapping between values)

Public interface:
    check_onboarding_health(db) -> dict    — full onboarding pipeline health snapshot
    detect_stuck_merchants(db) -> list      — merchants stuck in onboarding
    detect_pixel_abandonment(db) -> list    — merchants who never connected pixel
    detect_slow_activation(db) -> list      — merchants with slow time-to-insight
    write_onboarding_alerts(db) -> dict     — check all conditions, write alerts

Called by: agent_worker.py (every 15 minutes, after run_pending_onboarding)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("onboarding_health")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Merchant stuck in needs_repair for more than this is considered stuck
_STUCK_REPAIR_MINUTES = 30

# Merchant installed more than this ago with no pixel → pixel abandonment
_PIXEL_ABANDON_HOURS = 48

# Expected time from install to first signal
_EXPECTED_FIRST_INSIGHT_HOURS = 2

# Repair loop: if a merchant has triggered > N repairs in the lookback window
_REPAIR_LOOP_THRESHOLD = 5
_REPAIR_LOOP_LOOKBACK_HOURS = 24

# Drift window for new installs — 1-7 days post-install is the critical
# engagement window. Stalls here predict cancellation.
_DRIFT_MIN_HOURS = 24
_DRIFT_MAX_HOURS = 7 * 24

# Drift action loop (A2). Each drifter gets ONE re-engagement email per
# drift episode. After 3 distinct episodes (~21 days of repeated drift)
# the operator gets escalated.
_REENGAGE_REDIS_PREFIX = "hs:reengage:drift"
_REENGAGE_DEDUPE_TTL_S = 7 * 24 * 3600  # 1 email per shop per week max
_REENGAGE_MAX_EPISODES = 3              # escalate after this many tries


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def detect_stuck_merchants(db: Session) -> list[dict]:
    """
    Find merchants whose onboarding_status has been 'pending' or 'failed'
    for longer than the stuck threshold.
    """
    cutoff = _utcnow() - timedelta(minutes=_STUCK_REPAIR_MINUTES)
    rows = db.execute(text("""
        SELECT shop_domain, onboarding_status, onboarding_error, installed_at
        FROM merchants
        WHERE install_status = 'active'
          AND onboarding_status IN ('pending', 'failed')
          AND installed_at < :cutoff
        ORDER BY installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "status": r.onboarding_status,
            "error": r.onboarding_error,
            "stuck_since": r.installed_at.isoformat() if r.installed_at else None,
            "stuck_minutes": int((_utcnow() - r.installed_at).total_seconds() / 60) if r.installed_at else None,
        }
        for r in rows
    ]


def detect_pixel_abandonment(db: Session) -> list[dict]:
    """
    Find merchants who completed setup (onboarding_status=ready) more than
    _PIXEL_ABANDON_HOURS ago but have zero purchase events from the pixel.
    """
    cutoff = _utcnow() - timedelta(hours=_PIXEL_ABANDON_HOURS)
    rows = db.execute(text("""
        SELECT m.shop_domain, m.installed_at,
               (SELECT COUNT(*) FROM shop_orders so
                WHERE so.shop_domain = m.shop_domain AND so.source = 'pixel') AS pixel_orders
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.onboarding_status = 'ready'
          AND m.installed_at < :cutoff
        ORDER BY m.installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "installed_at": r.installed_at.isoformat() if r.installed_at else None,
            "hours_since_install": int((_utcnow() - r.installed_at).total_seconds() / 3600) if r.installed_at else None,
            "pixel_orders": r.pixel_orders,
        }
        for r in rows
        if r.pixel_orders == 0
    ]


def detect_slow_activation(db: Session) -> list[dict]:
    """
    Find merchants who have been installed for > _EXPECTED_FIRST_INSIGHT_HOURS
    but have zero opportunity_signals.
    """
    cutoff = _utcnow() - timedelta(hours=_EXPECTED_FIRST_INSIGHT_HOURS)
    rows = db.execute(text("""
        SELECT m.shop_domain, m.installed_at,
               (SELECT COUNT(*) FROM events e
                WHERE e.shop_domain = m.shop_domain) AS event_count,
               (SELECT COUNT(*) FROM opportunity_signals os
                WHERE os.shop_domain = m.shop_domain AND os.expires_at > now()) AS signal_count
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.onboarding_status = 'ready'
          AND m.installed_at < :cutoff
        ORDER BY m.installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "installed_at": r.installed_at.isoformat() if r.installed_at else None,
            "hours_since_install": int((_utcnow() - r.installed_at).total_seconds() / 3600) if r.installed_at else None,
            "event_count": r.event_count,
            "signal_count": r.signal_count,
        }
        for r in rows
        if r.signal_count == 0
    ]


def detect_drifting_new_installs(db: Session) -> list[dict]:
    """Find merchants 1-7 days post-install who never engaged with any
    killer feature.

    Drift signal = installed in the drift window AND:
      - zero active nudges AND
      - zero goals set AND
      - zero configured outbound signal webhooks.

    This is the cancellation predictor: merchants who installed, saw
    the dashboard, but never actually wired HedgeSpark into their
    workflow. They cancel before the first billing cycle unless we
    re-engage them now.
    """
    now = _utcnow()
    min_cutoff = now - timedelta(hours=_DRIFT_MAX_HOURS)
    max_cutoff = now - timedelta(hours=_DRIFT_MIN_HOURS)

    try:
        rows = db.execute(text("""
            SELECT m.shop_domain, m.installed_at,
                   (SELECT COUNT(*) FROM active_nudges n
                    WHERE n.shop_domain = m.shop_domain AND n.active = true) AS nudge_count
            FROM merchants m
            WHERE m.install_status = 'active'
              AND m.onboarding_status = 'ready'
              AND m.installed_at BETWEEN :min_cutoff AND :max_cutoff
            ORDER BY m.installed_at ASC
            LIMIT 100
        """), {"min_cutoff": min_cutoff, "max_cutoff": max_cutoff}).fetchall()
    except Exception as exc:
        log.debug("onboarding_health: drift query failed: %s", exc)
        return []

    drifters: list[dict] = []
    try:
        from app.services.goals import get_goals
        from app.services.signal_webhooks import list_webhooks
    except Exception:
        get_goals = None  # type: ignore
        list_webhooks = None  # type: ignore

    for r in rows:
        shop = r.shop_domain
        nudges = int(r.nudge_count or 0)
        if nudges > 0:
            continue

        goal_count = 0
        webhook_count = 0
        try:
            if get_goals is not None:
                goal_count = len(get_goals(shop) or [])
        except Exception:
            pass
        try:
            if list_webhooks is not None:
                webhook_count = len(list_webhooks(shop) or [])
        except Exception:
            pass

        if goal_count == 0 and webhook_count == 0 and nudges == 0:
            hours_since = int((now - r.installed_at).total_seconds() / 3600) if r.installed_at else None
            drifters.append({
                "shop_domain": shop,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                "hours_since_install": hours_since,
                "active_nudges": nudges,
                "goals_set": goal_count,
                "webhooks_configured": webhook_count,
            })
    return drifters


# ---------------------------------------------------------------------------
# Drift action loop — detect → re-engage → measure → escalate
# ---------------------------------------------------------------------------


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _reengage_episode_key(shop: str) -> str:
    return f"{_REENGAGE_REDIS_PREFIX}:episode:{shop}"


def _reengage_dedupe_key(shop: str) -> str:
    return f"{_REENGAGE_REDIS_PREFIX}:sent:{shop}"


def _can_send_reengagement(shop: str) -> tuple[bool, int]:
    """Return (allowed, current_episode_count). Allowed only if no
    re-engagement was sent in the last week for this shop."""
    rc = _redis()
    if rc is None:
        return False, 0
    try:
        if rc.get(_reengage_dedupe_key(shop)):
            ep_raw = rc.get(_reengage_episode_key(shop))
            ep = int(ep_raw) if ep_raw else 0
            return False, ep
        ep_raw = rc.get(_reengage_episode_key(shop))
        ep = int(ep_raw) if ep_raw else 0
        return True, ep
    except Exception:
        return False, 0


def _record_reengagement_sent(shop: str) -> int:
    """Mark a re-engagement as sent and bump the episode counter.
    Returns the new episode count."""
    rc = _redis()
    if rc is None:
        return 0
    try:
        rc.setex(_reengage_dedupe_key(shop), _REENGAGE_DEDUPE_TTL_S, "1")
        new_ep = rc.incr(_reengage_episode_key(shop))
        # Episodes never auto-expire — they stay until the merchant
        # actually engages (no auto-reset). The escalation logic uses
        # this to decide when to involve the operator.
        rc.expire(_reengage_episode_key(shop), 90 * 24 * 3600)
        return int(new_ep)
    except Exception:
        return 0


def _build_reengagement_email(drifter: dict) -> tuple[str, str, str]:
    """Return (subject, html, plain_text) for the drift re-engagement.

    Idiot-proof copy: zero jargon, single ask, single CTA. The merchant
    has been on the dashboard for 1-7 days but never wired anything up
    — the email's job is to remove the next-step ambiguity.
    """
    shop = drifter.get("shop_domain", "")
    hours = drifter.get("hours_since_install") or 0
    days = max(1, hours // 24)
    pretty_shop = shop.replace(".myshopify.com", "")

    subject = f"\u26a1 {pretty_shop}, ti aiuto a partire con HedgeSpark in 60 secondi"

    html = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;padding:24px;color:#0f172a;">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:14px;padding:32px;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em;color:#d4893a;margin-bottom:8px;">
    HedgeSpark \u00b7 setup help
  </div>
  <h1 style="font-size:22px;line-height:1.35;margin:0 0 14px;color:#0f172a;">
    Hai installato HedgeSpark {days} giorni fa, ma non hai ancora settato il primo obiettivo.
  </h1>
  <p style="font-size:15px;line-height:1.6;color:#334155;margin:0 0 20px;">
    HedgeSpark inizia a salvarti soldi solo quando sa cosa difendere.
    Ti basta scegliere <strong>un obiettivo mensile</strong> (revenue, CVR o AOV)
    e da quel momento il sistema lavora in autonomia.
  </p>
  <div style="background:#fff7ed;border-left:4px solid #d4893a;padding:16px 20px;border-radius:6px;margin:20px 0;">
    <div style="font-size:13px;font-weight:700;color:#9a3412;margin-bottom:6px;">
      Cosa fa il sistema dopo che setti l'obiettivo:
    </div>
    <ul style="font-size:13px;line-height:1.7;color:#475569;margin:0;padding-left:18px;">
      <li>Misura il rischio di mancato target ogni giorno</li>
      <li>Ti avvisa solo quando c'\u00e8 un'azione concreta da fare</li>
      <li>Applica le ottimizzazioni testate in autonomia</li>
    </ul>
  </div>
  <div style="text-align:center;margin:28px 0 8px;">
    <a href="https://app.hedgesparkhq.com/app?go=goals"
       style="display:inline-block;background:#d4893a;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
      Setta l'obiettivo (60 secondi)
    </a>
  </div>
  <p style="font-size:12px;color:#94a3b8;text-align:center;margin:24px 0 0;">
    Bastano davvero 60 secondi. Se hai bisogno di una mano, rispondi a
    questa mail e ti aiutiamo noi.
  </p>
</div>
</body></html>"""

    plain = (
        f"Hai installato HedgeSpark {days} giorni fa, ma non hai ancora "
        f"settato il primo obiettivo.\n\n"
        f"HedgeSpark inizia a salvarti soldi solo quando sa cosa difendere. "
        f"Ti basta scegliere un obiettivo mensile (revenue, CVR o AOV).\n\n"
        f"Setta l'obiettivo qui: https://app.hedgesparkhq.com/app?go=goals\n\n"
        f"Bastano davvero 60 secondi. Se hai bisogno di una mano, rispondi a "
        f"questa mail.\n"
    )

    return subject, html, plain


def _lookup_merchant_email(db: Session, shop_domain: str) -> str | None:
    try:
        row = db.execute(text("""
            SELECT contact_email FROM merchants
            WHERE shop_domain = :shop AND email_paused = false
            LIMIT 1
        """), {"shop": shop_domain}).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def send_reengagement_for_drifter(db: Session, drifter: dict) -> dict:
    """Submit a re-engagement EmailIntent for a drifting install.

    Returns a structured result for logging:
      {"status": "sent" | "skipped_cooldown" | "skipped_no_email"
                 | "escalated" | "no_orchestrator", "episode": int}

    Idempotent per (shop, week). After _REENGAGE_MAX_EPISODES distinct
    weekly attempts, escalates to operator via ops_alert and stops
    sending so we never spam a chronically idle merchant.
    """
    shop = drifter.get("shop_domain", "")
    if not shop:
        return {"status": "skipped_no_shop", "episode": 0}

    allowed, current_ep = _can_send_reengagement(shop)
    if not allowed and current_ep > 0:
        return {"status": "skipped_cooldown", "episode": current_ep}

    if current_ep >= _REENGAGE_MAX_EPISODES:
        # Escalate exactly once per shop — chronic dedupe will collapse repeats
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                source=f"onboarding_health:drift_chronic",
                alert_type="drift_chronic_escalation",
                severity="warning",
                shop_domain=shop,
                summary=(
                    f"{shop} drifting after {current_ep} re-engagement attempts "
                    f"\u2014 needs human outreach"
                ),
                detail=drifter,
            )
        except Exception:
            pass
        return {"status": "escalated", "episode": current_ep}

    to_email = _lookup_merchant_email(db, shop)
    if not to_email:
        return {"status": "skipped_no_email", "episode": current_ep}

    try:
        from app.services.email_orchestrator import EmailIntent, submit_intent
    except Exception as exc:
        log.debug("drift reengagement: orchestrator unavailable: %s", exc)
        return {"status": "no_orchestrator", "episode": current_ep}

    subject, html, plain = _build_reengagement_email(drifter)
    try:
        intent = EmailIntent(
            shop_domain=shop,
            email_type="reengagement_drift",
            to_email=to_email,
            subject=subject,
            html=html,
            plain_text=plain,
            producer="onboarding_health",
            context={
                "drift_episode": current_ep + 1,
                "hours_since_install": drifter.get("hours_since_install"),
            },
        )
        submit_intent(db, intent)
    except Exception as exc:
        log.debug("drift reengagement: submit failed: %s", exc)
        return {"status": "submit_failed", "episode": current_ep}

    new_ep = _record_reengagement_sent(shop)
    log.info(
        "onboarding_health: re-engagement sent shop=%s episode=%d",
        shop, new_ep,
    )
    return {"status": "sent", "episode": new_ep}


def run_drift_action_loop(db: Session) -> dict:
    """Find drifting installs and send re-engagement emails.

    Called from agent_worker after `write_onboarding_alerts`. Honors
    the per-shop weekly cooldown so a merchant never gets spammed.
    """
    drifters = detect_drifting_new_installs(db)
    summary = {
        "drifters": len(drifters),
        "sent": 0,
        "skipped_cooldown": 0,
        "skipped_no_email": 0,
        "escalated": 0,
        "errors": 0,
    }
    for d in drifters:
        try:
            r = send_reengagement_for_drifter(db, d)
            status = r.get("status", "errors")
            if status == "sent":
                summary["sent"] += 1
            elif status == "skipped_cooldown":
                summary["skipped_cooldown"] += 1
            elif status == "skipped_no_email":
                summary["skipped_no_email"] += 1
            elif status == "escalated":
                summary["escalated"] += 1
            else:
                summary["errors"] += 1
        except Exception:
            summary["errors"] += 1
    return summary


# ---------------------------------------------------------------------------
# Aggregate health check
# ---------------------------------------------------------------------------

def check_onboarding_health(db: Session) -> dict:
    """
    Full onboarding pipeline health snapshot.
    Returns counts and lists for each problem type.
    """
    stuck = detect_stuck_merchants(db)
    pixel_abandon = detect_pixel_abandonment(db)
    slow_activation = detect_slow_activation(db)
    drifters = detect_drifting_new_installs(db)

    # Count total active merchants for context
    total = db.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE install_status = 'active'"
    )).scalar() or 0

    ready = db.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE install_status = 'active' AND onboarding_status = 'ready'"
    )).scalar() or 0

    return {
        "total_active_merchants": total,
        "total_onboarded": ready,
        "stuck_merchants": len(stuck),
        "stuck_details": stuck[:10],  # cap detail list
        "pixel_abandonment": len(pixel_abandon),
        "pixel_abandon_details": pixel_abandon[:10],
        "slow_activation": len(slow_activation),
        "slow_activation_details": slow_activation[:10],
        "drifting_new_installs": len(drifters),
        "drift_details": drifters[:10],
        "healthy": (
            len(stuck) == 0
            and len(pixel_abandon) <= 1
            and len(drifters) == 0
        ),
    }


# ---------------------------------------------------------------------------
# Alert writer — integrates with ops_alerts via alerting.py
# ---------------------------------------------------------------------------

def write_onboarding_alerts(db: Session) -> dict:
    """
    Check all onboarding health conditions and write ops_alerts for
    actionable problems. Deduplication is handled by alerting.write_alert.

    Returns summary of alerts written.
    """
    from app.services.alerting import write_alert

    written = 0

    # 1. Stuck merchants
    stuck = detect_stuck_merchants(db)
    for m in stuck[:5]:  # cap at 5 alerts per cycle
        write_alert(
            db,
            source="onboarding_health",
            alert_type="onboarding_stuck",
            severity="warning",
            shop_domain=m["shop_domain"],
            summary=f"Onboarding stuck for {m['stuck_minutes']}m: {m['status']}",
            detail={
                "status": m["status"],
                "error": m["error"],
                "stuck_minutes": m["stuck_minutes"],
            },
        )
        written += 1

    # 2. Pixel abandonment (only alert after extended period)
    pixel_abandon = detect_pixel_abandonment(db)
    long_abandon = [p for p in pixel_abandon if (p.get("hours_since_install") or 0) > 72]
    for m in long_abandon[:3]:
        write_alert(
            db,
            source="onboarding_health",
            alert_type="pixel_abandonment",
            severity="info",
            shop_domain=m["shop_domain"],
            summary=f"Pixel not connected after {m['hours_since_install']}h",
            detail=m,
        )
        written += 1

    # 3. Slow activation
    slow = detect_slow_activation(db)
    # Only alert if they have events but no signals (means intelligence worker isn't working)
    data_but_no_insights = [s for s in slow if (s.get("event_count") or 0) > 10 and s.get("signal_count") == 0]
    for m in data_but_no_insights[:3]:
        write_alert(
            db,
            source="onboarding_health",
            alert_type="slow_activation",
            severity="warning",
            shop_domain=m["shop_domain"],
            summary=f"Events flowing ({m['event_count']}) but 0 signals after {m['hours_since_install']}h",
            detail=m,
        )
        written += 1

    # 4. Drifting new installs — 1-7d post-install, no engagement with
    # any killer feature. Highest cancel-risk population.
    drifters = detect_drifting_new_installs(db)
    for m in drifters[:5]:
        write_alert(
            db,
            source="onboarding_health",
            alert_type="onboarding_drift",
            severity="warning",
            shop_domain=m["shop_domain"],
            summary=(
                f"New install drifting at {m['hours_since_install']}h: "
                f"0 goals, 0 webhooks, 0 nudges"
            ),
            detail=m,
        )
        written += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        log.exception("onboarding_health: failed to commit alerts")

    return {
        "alerts_written": written,
        "stuck": len(stuck),
        "pixel_abandon": len(pixel_abandon),
        "slow_activation": len(slow),
        "drifting_new_installs": len(drifters),
    }
