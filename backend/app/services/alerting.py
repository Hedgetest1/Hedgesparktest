"""
alerting.py — Internal operational alert writer + external delivery.

Public interface:
    write_alert(db, ...) -> OpsAlert     — persist + optional external delivery
    get_unresolved_alerts(db, ...) -> list
    resolve_alert(db, alert_id) -> None

Flow:
    1. Persist to ops_alerts table (always — this is the source of truth)
    2. Attempt external delivery via Slack webhook (optional, fail-safe)
    3. Return the persisted alert regardless of delivery outcome
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ops_alert import OpsAlert

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alert storm aggregation — collapse repeat alerts into a counter
# ---------------------------------------------------------------------------
#
# Pre-2026-04-11 behavior: 5-minute dedup window. Workers running every
# 15 minutes therefore emitted a FRESH alert every cycle, producing 95
# duplicates of the same (source, type) in 24h for chronic issues
# (circuit_breaker_tripped, slow_activation, stale_level2_proposal, …).
#
# New behavior: extended dedup window to 24h for UNRESOLVED alerts.
# Instead of creating a new row, we COLLAPSE the repeat into the
# existing unresolved alert by:
#   1. incrementing `detail.occurrence_count` (stored in the JSON text field)
#   2. updating `detail.last_seen_at` to the current wall clock
#
# The original 5-minute acute-dedup is preserved: if an unresolved alert
# exists and was last seen within 5 minutes, we treat the repeat as a
# pure duplicate (no counter increment, no side-effects — just return
# the existing row).
#
# Net effect in prod: an alert storming at 15-min intervals now shows up
# as ONE ops_alert row with `occurrence_count=95`, not 95 separate rows.
# Operators see "this problem is still here" via the counter and the
# `last_seen_at` freshness, not via alert noise.
#
# TIER_2 constraint: no schema migration — `detail` is a JSON text field
# that already exists, so we store aggregation state inside it.

_DEDUP_ACUTE_WINDOW_SECONDS = 300         # 5 minutes — pure noise suppression
_DEDUP_CHRONIC_WINDOW_SECONDS = 24 * 3600  # 24 hours — aggregate ongoing issue


def _check_dedup(
    db: Session,
    source: str,
    alert_type: str,
    shop_domain: str | None,
) -> OpsAlert | None:
    """
    Legacy 5-minute acute dedup: return an existing unresolved alert if
    one was fired in the last 5 minutes. Used by write_alert as the
    first-pass dedup — if we find an acute match, no state mutation,
    return the existing row.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=_DEDUP_ACUTE_WINDOW_SECONDS)

    q = db.query(OpsAlert).filter(
        OpsAlert.source == source,
        OpsAlert.alert_type == alert_type,
        OpsAlert.resolved == False,
        OpsAlert.created_at >= cutoff,
    )
    if shop_domain:
        q = q.filter(OpsAlert.shop_domain == shop_domain)
    else:
        q = q.filter(OpsAlert.shop_domain.is_(None))

    return q.first()


def _check_chronic(
    db: Session,
    source: str,
    alert_type: str,
    shop_domain: str | None,
) -> OpsAlert | None:
    """
    24-hour chronic dedup: return the *oldest* still-unresolved alert of
    this (source, type, shop) created within the last 24 hours. If found,
    the caller collapses the repeat into it via _collapse_into_existing.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=_DEDUP_CHRONIC_WINDOW_SECONDS)

    q = db.query(OpsAlert).filter(
        OpsAlert.source == source,
        OpsAlert.alert_type == alert_type,
        OpsAlert.resolved == False,
        OpsAlert.created_at >= cutoff,
    )
    if shop_domain:
        q = q.filter(OpsAlert.shop_domain == shop_domain)
    else:
        q = q.filter(OpsAlert.shop_domain.is_(None))

    return q.order_by(OpsAlert.created_at.asc()).first()


def _collapse_into_existing(
    db: Session,
    existing: OpsAlert,
    new_summary: str,
    new_detail: Any,
) -> OpsAlert:
    """
    Collapse a repeat alert into an existing unresolved row. Increments
    `detail.occurrence_count` and sets `detail.last_seen_at`, preserves
    any prior structured detail under `detail.initial_detail` on the
    first collapse so the original context is not lost.
    """
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    # Parse existing detail (might be string, JSON string, or None)
    prior_parsed: dict[str, Any] | None = None
    prior_raw = existing.detail
    if prior_raw:
        try:
            parsed = json.loads(prior_raw)
            if isinstance(parsed, dict):
                prior_parsed = parsed
        except (ValueError, TypeError):
            prior_parsed = None

    if prior_parsed is None:
        # First collapse: preserve whatever was there as initial_detail.
        prior_parsed = {
            "initial_detail": prior_raw if prior_raw else None,
            "initial_summary": existing.summary,
            "occurrence_count": 1,
            "first_seen_at": existing.created_at.isoformat() if existing.created_at else now_iso,
        }

    # Increment + refresh
    prior_parsed["occurrence_count"] = int(prior_parsed.get("occurrence_count", 1)) + 1
    prior_parsed["last_seen_at"] = now_iso

    # Record the most recent payload so operators see what just came in,
    # not just the oldest stale context.
    if new_detail is not None:
        prior_parsed["last_detail"] = (
            json.dumps(new_detail, default=str) if not isinstance(new_detail, str) else new_detail
        )[:2000]
    if new_summary and new_summary != existing.summary:
        prior_parsed["last_summary"] = new_summary[:512]

    existing.detail = json.dumps(prior_parsed, default=str)
    # Touch a visible mutation so ORM flushes it — SQLAlchemy tracks
    # assignment on Text fields reliably.
    try:
        db.flush()
    except Exception as exc:
        log.warning("alerting: flush after collapse failed (non-fatal): %s", exc)

    return existing


def write_alert(
    db: Session,
    *,
    severity: str,
    source: str,
    alert_type: str,
    summary: str,
    shop_domain: str | None = None,
    detail: Any = None,
) -> OpsAlert:
    """
    Write an operational alert and attempt external delivery.

    Dedup: suppresses duplicate alerts with the same (source, alert_type,
    shop_domain) within a 5-minute window to prevent alert storms.

    DB persist happens FIRST — the alert exists regardless of delivery outcome.
    External delivery is attempted SECOND — failure is logged but never raised.
    """
    # Step 0a: Acute dedup — pure noise suppression within 5 minutes.
    # If an identical alert was raised in the last 5 minutes, drop this
    # one entirely. No state mutation — we don't even bump the counter,
    # because 5-minute-apart duplicates are noise (retry loops, racing
    # worker cycles), not operationally meaningful repeats.
    acute = _check_dedup(db, source, alert_type, shop_domain)
    if acute:
        log.debug(
            "alert: acute dedup suppressed [%s] %s shop=%s — existing alert_id=%d",
            alert_type, source, shop_domain or "global", acute.id,
        )
        return acute

    # Step 0b: Chronic aggregation — if an unresolved alert exists within
    # 24h but older than the acute window, COLLAPSE this repeat into it.
    # Result: one ops_alert row per ongoing problem, with an
    # `occurrence_count` counter in its detail JSON that shows how many
    # times the pipeline has re-observed the issue. The operator then
    # sees the single row updating over time, not a storm of duplicates.
    chronic = _check_chronic(db, source, alert_type, shop_domain)
    if chronic:
        log.info(
            "alert: chronic aggregation — collapsing [%s] %s shop=%s "
            "into existing alert_id=%d",
            alert_type, source, shop_domain or "global", chronic.id,
        )
        return _collapse_into_existing(db, chronic, summary, detail)

    # Step 1: Persist to DB (always)
    alert = OpsAlert(
        severity=severity,
        source=source,
        alert_type=alert_type,
        shop_domain=shop_domain,
        summary=summary,
        detail=json.dumps(detail, default=str) if detail and not isinstance(detail, str) else detail,
    )
    db.add(alert)
    db.flush()
    log.info(
        "alert: %s [%s] %s shop=%s — %s",
        severity, alert_type, source, shop_domain or "global", summary,
    )

    # Step 2: Attempt external delivery + record outcome
    try:
        from app.core.alert_delivery import deliver_alert_externally
        from datetime import datetime, timezone

        delivered = deliver_alert_externally(
            severity=severity,
            source=source,
            alert_type=alert_type,
            summary=summary,
            shop_domain=shop_domain,
        )
        if delivered:
            alert.delivery_status = "sent"
            alert.delivered_at = datetime.now(timezone.utc).replace(tzinfo=None)
        elif not os.getenv("OPS_SLACK_WEBHOOK_URL", "").strip():
            alert.delivery_status = "skipped"
        else:
            alert.delivery_status = "failed"
        db.flush()
    except Exception as exc:
        alert.delivery_status = "failed"
        alert.delivery_error = str(exc)[:250]
        try:
            db.flush()
        except Exception as exc2:
            log.warning("alerting: flush after delivery failure failed: %s", exc2)
        log.debug("alert: external delivery error (non-fatal): %s", exc)

    # Phase Ω'' — outbound webhook fan-out. Compliance + GDPR sources go to
    # 'compliance.alert', everything else to 'anomaly.detected'. Shop-scoped
    # only — global alerts (shop_domain=None) are not published.
    if shop_domain:
        try:
            from app.services.event_emitter import emit
            event_type = (
                "compliance.alert"
                if source in ("compliance_score", "compliance_evidence", "gdpr_processor",
                              "regulatory_feed_monitor", "breach_notification", "uninstall_erasure")
                else "anomaly.detected"
            )
            emit(db, shop_domain, event_type, {
                "alert_id": alert.id,
                "severity": severity,
                "source": source,
                "alert_type": alert_type,
                "summary": summary,
            })
        except Exception as exc:
            log.warning("alerting: event emit failed: %s", exc)

    return alert


def get_unresolved_alerts(
    db: Session,
    severity: str | None = None,
    limit: int = 50,
) -> list[OpsAlert]:
    """Return unresolved alerts, optionally filtered by severity."""
    q = db.query(OpsAlert).filter(OpsAlert.resolved == False)
    if severity:
        q = q.filter(OpsAlert.severity == severity)
    return q.order_by(OpsAlert.created_at.desc()).limit(limit).all()


def resolve_alert(db: Session, alert_id: int) -> None:
    """Mark an alert as resolved."""
    alert = db.get(OpsAlert, alert_id)
    if alert and not alert.resolved:
        alert.resolved = True
        alert.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()


# Tiered staleness — urgency-proportional auto-resolution.
# Critical alerts get a long window (should be resolved by humans, not time).
# Warnings get a medium window (actionable but not urgent).
# Info gets a short window (they are telemetry, not signals).
_STALE_ALERT_AGE_HOURS = 48       # fallback for everything
_STALE_INFO_AGE_HOURS = 6         # info-severity: pure telemetry, short TTL
_STALE_WARNING_AGE_HOURS = 24     # warning: 1 day to act before noise
_STALE_CRITICAL_AGE_HOURS = 72    # critical: 3 days — enough for response

# Alert types that are known-harmless telemetry and should be auto-resolved
# aggressively. These are observation-only signals (heartbeats, usage logs)
# that pile up in the unresolved table and inflate the alert pressure metric
# without representing actionable incidents.
_AUTO_RESOLVE_NOISE_TYPES = frozenset({
    "heartbeat_ok",
    "deploy_succeeded",
    "positive_feedback",
    "product_feedback",
})


def resolve_stale_alerts(db: Session) -> int:
    """Tiered auto-resolution of stale alerts.

    Severity-scaled: info >6h, warning >24h, critical >72h.
    Plus: always clear known-noise alert types regardless of age.
    Returns the total number of alerts resolved.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total = 0

    # Tier 1: severity-based staleness
    severity_cutoffs = [
        ("info", now - timedelta(hours=_STALE_INFO_AGE_HOURS)),
        ("warning", now - timedelta(hours=_STALE_WARNING_AGE_HOURS)),
        ("critical", now - timedelta(hours=_STALE_CRITICAL_AGE_HOURS)),
    ]
    for severity, cutoff in severity_cutoffs:
        result = db.execute(
            text("""
                UPDATE ops_alerts
                SET resolved = true,
                    resolved_at = :now
                WHERE resolved = false
                  AND severity = :sev
                  AND created_at < :cutoff
            """),
            {"now": now, "sev": severity, "cutoff": cutoff},
        )
        total += result.rowcount or 0

    # Tier 2: known-noise alert types — resolve aggressively (no age gate)
    if _AUTO_RESOLVE_NOISE_TYPES:
        result = db.execute(
            text("""
                UPDATE ops_alerts
                SET resolved = true,
                    resolved_at = :now
                WHERE resolved = false
                  AND alert_type = ANY(:types)
                  AND created_at < :cutoff
            """),
            {
                "now": now,
                "types": list(_AUTO_RESOLVE_NOISE_TYPES),
                "cutoff": now - timedelta(hours=1),  # 1h grace period
            },
        )
        total += result.rowcount or 0

    return total
