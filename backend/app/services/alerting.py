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

from app.core.database import savepoint_scope
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
    # session-rollback: ok — best-effort flush in caller-owned session; all write_alert callers either wrap (rollback_quiet/_safe_check, see 16-site regression-lock), worker_scope cycle exit, OR are request-scoped FastAPI (dep teardown closes session). The except below is the LAST stmt before `return existing` — no chained writes on the poisoned txn.
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
    # Step 0 (preempt): synthetic test-shop guard.
    # If the shop_domain matches a known test-fixture pattern, don't
    # touch the DB. The caller is almost always a service path
    # (risk_forecast / signal_webhooks / etc.) that opens its own
    # SessionLocal() to persist outside the caller's transaction —
    # which bypasses test SAVEPOINTs and leaks rows. 2026-05-06 audit
    # found 1079 orphan rows accumulated this way.
    # Return a fresh in-memory OpsAlert so the (rare) caller that uses
    # the return value still gets a typed object (id stays None,
    # callers that try to chain to DB ops see no-op semantics).
    try:
        from app.core.test_shop_blocklist import is_synthetic_test_shop
        if shop_domain and is_synthetic_test_shop(shop_domain):
            log.debug(
                "alert: synthetic-test-shop guard suppressed [%s] %s shop=%s",
                alert_type, source, shop_domain,
            )
            stub = OpsAlert(
                severity=severity, source=source, alert_type=alert_type,
                shop_domain=shop_domain, summary=summary,
                detail=json.dumps(detail, default=str) if detail and not isinstance(detail, str) else detail,
            )
            return stub  # not added to session; never persisted
    except Exception as exc:
        log.warning("alerting: synthetic-shop guard failed: %s", exc)
        # Fall through — fail-open so a guard bug never blocks real alerts.

    # Step 0bis: synthetic alert-source guard. Parallel to the shop-side
    # blocklist above, but keys on `source` for global-scope synthetic
    # tests (NULL shop_domain). Born 2026-05-11 after a phase_c_synthetic_
    # test alert persisted 16 days as orphan noise. See
    # app/core/alert_source_blocklist.py.
    try:
        from app.core.alert_source_blocklist import is_synthetic_alert_source
        if is_synthetic_alert_source(source):
            log.debug(
                "alert: synthetic-source guard suppressed [%s] %s shop=%s",
                alert_type, source, shop_domain or "global",
            )
            stub = OpsAlert(
                severity=severity, source=source, alert_type=alert_type,
                shop_domain=shop_domain, summary=summary,
                detail=json.dumps(detail, default=str) if detail and not isinstance(detail, str) else detail,
            )
            return stub  # not added to session; never persisted
    except Exception as exc:
        log.warning("alerting: synthetic-source guard failed: %s", exc)
        # Fail-open per the same rationale as the shop-side guard above.

    # Step 0ter: operator-shop alert-type guard. Drops merchant-funnel-
    # class alerts (slow_activation, onboarding_drift, etc.) when the
    # target is an operator dev tenant (hedgespark-dev). The funnel-
    # state alert is correctly detecting "stuck" on these shops because
    # the founder uses /app to test, not to convert. Real-bug alerts
    # (LLM failures, code errors) STILL fire — gate is narrow.
    # Born 2026-05-13 closing 2 stale alerts (id=137153 slow_activation,
    # id=136901 onboarding_slow_progress) on hedgespark-dev that
    # persisted 12-50d as noise.
    try:
        from app.core.operator_blocklist import is_operator_silenced_alert
        if is_operator_silenced_alert(shop_domain, alert_type):
            log.debug(
                "alert: operator-shop guard suppressed [%s] %s shop=%s",
                alert_type, source, shop_domain,
            )
            stub = OpsAlert(
                severity=severity, source=source, alert_type=alert_type,
                shop_domain=shop_domain, summary=summary,
                detail=json.dumps(detail, default=str) if detail and not isinstance(detail, str) else detail,
            )
            return stub  # not added to session; never persisted
    except Exception as exc:
        log.warning("alerting: operator-shop guard failed: %s", exc)
        # Fail-open per the same rationale as the shop-side guard above.

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

    # Step 2: Attempt external delivery + record outcome.
    #
    # Wrapped in savepoint_scope so a delivery_status flush failure does
    # not poison the caller's session. The alert row from Step 1 (line
    # ~308-309) is preserved regardless — the docstring contract "DB
    # persist happens FIRST" is honored. If the savepoint fails, the
    # in-memory `alert.delivery_status` mutation is rolled back (the
    # status stays at the model default) but the alert ROW remains
    # durable. Born 2026-05-20 closing the §21 nested-flush-poison
    # class: pre-fix, a Step-2 flush failure left the session in
    # PendingRollbackError state and the next caller op (emit() at
    # ~356 or the caller's continuation) raised InFailedSqlTransaction.
    try:
        with savepoint_scope(db):
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
        # log.warning (not log.debug): the savepoint isolated the
        # poison from the caller's txn, but a delivery + status-flush
        # failure IS prod-relevant — operators need visibility into
        # Slack/DB-side failures on the alert delivery path.
        log.warning("alert: external delivery error (savepoint-isolated, non-fatal): %s", exc)

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
# aggressively. These are observation-only signals (heartbeats, usage logs,
# snapshot-style drift comparators) that pile up in the unresolved table and
# inflate the alert pressure metric without representing actionable incidents.
#
# p95_slow_trend semantics: each alert is a snapshot comparing the last 24h
# vs prior 7d for a single hour-bucket of a single route. If the route is
# *still* slow on the next snapshot it will fire again — the alert represents
# a moment of drift, not an ongoing condition. Without this auto-resolve
# entry, 24h of normal drift events accumulate to ~30 unresolved rows that
# misrepresent live system state. Auto-resolved with 1h grace per the same
# logic as heartbeat_ok / deploy_succeeded.
_AUTO_RESOLVE_NOISE_TYPES = frozenset({
    "heartbeat_ok",
    "deploy_succeeded",
    "positive_feedback",
    "product_feedback",
    "p95_slow_trend",
})


def auto_resolve_alerts(
    db: Session,
    source: str | None = None,
    alert_type: str | None = None,
    shop_domain: str | None = None,
) -> int:
    """Generic heal-detection helper. Resolve prior unresolved alerts
    matching the given criteria.

    Use from any alert writer when the underlying condition has cleared:

        from app.services.alerting import auto_resolve_alerts
        if condition_now_healthy:
            auto_resolve_alerts(db, source="onboarding_health",
                                alert_type="slow_activation",
                                shop_domain=shop)

    At least one of (source, alert_type) MUST be supplied — passing
    neither would resolve the entire unresolved set, which is almost
    never what a writer wants. Returns count of alerts auto-resolved.

    Best-effort: never raises (closure path must not cascade-fail the
    surrounding probe/write loop). Born 2026-05-05 to close the heal-
    but-stay-open class across 8 writers (onboarding_stuck,
    pixel_abandonment, slow_activation, onboarding_failed,
    llm_safety_input/output, pipeline_stall_analyzed/proposed) — same
    pattern as invariant_monitor._auto_resolve_prior_invariant, which
    now delegates here.
    """
    if not source and not alert_type:
        log.warning(
            "auto_resolve_alerts called without source or alert_type — refusing"
        )
        return 0
    clauses = ["resolved=false"]
    params: dict[str, Any] = {}
    if source is not None:
        clauses.append("source=:source")
        params["source"] = source
    if alert_type is not None:
        clauses.append("alert_type=:alert_type")
        params["alert_type"] = alert_type
    if shop_domain is not None:
        clauses.append("shop_domain=:shop_domain")
        params["shop_domain"] = shop_domain
    where = " AND ".join(clauses)
    # Use SAVEPOINT so a failure in the heal UPDATE does not poison the
    # caller's outer transaction (the caller may be in the middle of a
    # multi-step writer flow). Born 2026-05-05 evening after Sentry
    # surfaced "This Session's transaction has been rolled back due to
    # a previous exception during flush" inside invariant_monitor —
    # a sequential audit fail → audit ok pattern was poisoning the
    # session because the previous failed write_alert had not been
    # rolled back before the heal UPDATE ran.
    # session-rollback: ok — `with db.begin_nested():` (next line) is the SAVEPOINT; outer except observes release-failure but the SAVEPOINT auto-rollback already cleaned the txn. Heuristic missed the SAVEPOINT because the try body is multi-stmt with the `with` as the first child.
    try:
        with db.begin_nested():
            # elite-hardening-allowed: {where} interpolated from hardcoded clauses ("source=:source", "alert_type=:alert_type", "shop_domain=:shop_domain", "resolved=false") joined with " AND ". No user input enters the SQL — only bind values are user-supplied (bound via params).
            result = db.execute(
                text(
                    f"UPDATE ops_alerts SET resolved=true, resolved_at=NOW() "
                    f"WHERE {where}"
                ),
                params,
            )
        return result.rowcount or 0
    except Exception as exc:
        log.warning(
            "auto_resolve_alerts failed for source=%s type=%s shop=%s: %s",
            source, alert_type, shop_domain, exc,
        )
        return 0


def heal_per_shop_alerts(
    db: Session,
    source: str,
    alert_type: str,
    currently_affected_shops: list[str] | set[str] | None,
) -> int:
    """Per-shop heal helper for population-scanner writers.

    Used by writers that periodically re-evaluate a merchant population
    (onboarding_health, drifting installs, etc.) — any merchant no longer
    in the currently-affected set has healed and their open alert should
    auto-resolve.

    Semantics:
      - currently_affected_shops empty / None → ALL unresolved (source,
        alert_type) alerts heal (the population is clean now).
      - non-empty → unresolved alerts whose shop_domain is NOT in the
        affected set heal.

    Returns count auto-resolved. Best-effort — never raises.
    """
    if not source or not alert_type:
        log.warning(
            "heal_per_shop_alerts called without source or alert_type — refusing"
        )
        return 0
    affected = sorted({s for s in (currently_affected_shops or []) if s})
    if not affected:
        return auto_resolve_alerts(db, source=source, alert_type=alert_type)
    # SAVEPOINT-isolate to protect caller's outer transaction (same
    # rationale as auto_resolve_alerts above).
    # session-rollback: ok — `with db.begin_nested():` (next line) is the SAVEPOINT; same SAVEPOINT-isolation pattern as auto_resolve_alerts. Heuristic missed the SAVEPOINT because the try body is multi-stmt with the `with` as the first child.
    try:
        with db.begin_nested():
            result = db.execute(
                text(
                    "UPDATE ops_alerts SET resolved=true, resolved_at=NOW() "
                    "WHERE source=:source AND alert_type=:alert_type "
                    "  AND resolved=false "
                    "  AND (shop_domain IS NULL OR NOT (shop_domain = ANY(:affected)))"
                ),
                {"source": source, "alert_type": alert_type, "affected": affected},
            )
        return result.rowcount or 0
    except Exception as exc:
        log.warning(
            "heal_per_shop_alerts failed for %s/%s: %s",
            source, alert_type, exc,
        )
        return 0


def resolve_stale_alerts(db: Session) -> int:
    """Tiered auto-resolution of stale alerts.

    Severity-scaled: info >6h, warning >24h, critical >72h.
    Plus: always clear known-noise alert types regardless of age.
    Returns the total number of alerts resolved.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total = 0

    # Tier 1: severity-based staleness — single UPDATE with a
    # per-severity cutoff disjunction. Previously 3 separate UPDATEs
    # produced 3 round-trips + 3 index scans on the (resolved, severity,
    # created_at) index. The combined statement keeps the same matching
    # semantics but touches the table once.
    cut_info = now - timedelta(hours=_STALE_INFO_AGE_HOURS)
    cut_warn = now - timedelta(hours=_STALE_WARNING_AGE_HOURS)
    cut_crit = now - timedelta(hours=_STALE_CRITICAL_AGE_HOURS)
    result = db.execute(
        text("""
            UPDATE ops_alerts
            SET resolved = true,
                resolved_at = :now
            WHERE resolved = false
              AND (
                   (severity = 'info'     AND created_at < :cut_info)
                OR (severity = 'warning'  AND created_at < :cut_warn)
                OR (severity = 'critical' AND created_at < :cut_crit)
              )
        """),
        {"now": now, "cut_info": cut_info, "cut_warn": cut_warn, "cut_crit": cut_crit},
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
