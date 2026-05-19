"""
ops.py — Internal operator API for ops alerts and GDPR export retrieval.

All endpoints require X-API-Key header (DASHBOARD_API_KEY).
These are NOT merchant-facing — they are for operators, scripts, and AI agents.

GET    /ops/alerts               — list unresolved alerts
GET    /ops/alerts/recent        — list recent alerts (resolved + unresolved)
POST   /ops/alerts/{id}/resolve  — mark an alert as resolved
GET    /ops/gdpr/exports         — list completed data exports
GET    /ops/gdpr/exports/{id}    — retrieve a specific export
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_operator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", tags=["ops"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Orchestrator Readiness
# ---------------------------------------------------------------------------

@router.get("/readiness/orchestrator")
def orchestrator_readiness(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Check orchestrator activation readiness for supervised hybrid mode."""
    import os
    from app.services.orchestrator import ORCHESTRATOR_MODE, ACTION_REGISTRY, TIER_0, TIER_1, TIER_2

    mode = ORCHESTRATOR_MODE
    anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    operator_key = bool(os.getenv("DASHBOARD_API_KEY", "").strip())
    slack_url = bool(os.getenv("OPS_SLACK_WEBHOOK_URL", "").strip())

    missing = []
    warnings = []

    if mode in ("proposal", "hybrid") and not anthropic_key and not openai_key:
        missing.append("ANTHROPIC_API_KEY or OPENAI_API_KEY required for LLM mode")
    if not operator_key:
        missing.append("DASHBOARD_API_KEY required for approval API")
    if mode == "hybrid" and not slack_url:
        warnings.append("OPS_SLACK_WEBHOOK_URL not set — approval notifications will be DB-only")
    if mode not in ("deterministic", "proposal", "hybrid"):
        missing.append(f"ORCHESTRATOR_MODE='{mode}' is invalid — must be deterministic|proposal|hybrid")

    # Summarize action registry
    tier_counts = {0: 0, 1: 0, 2: 0}
    for name, entry in ACTION_REGISTRY.items():
        tier = entry[2] if len(entry) >= 3 else 2
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    ready = len(missing) == 0

    return {
        "ready": ready,
        "mode": mode,
        "llm_available": anthropic_key or openai_key,
        "llm_provider": "anthropic" if anthropic_key else ("openai" if openai_key else "none"),
        "provider_policy": _get_provider_policy(),
        "slack_configured": slack_url,
        "operator_key_configured": operator_key,
        "actions": {
            "total": len(ACTION_REGISTRY),
            "tier_0_auto": tier_counts.get(0, 0),
            "tier_1_approval": tier_counts.get(1, 0),
            "tier_2_human_only": tier_counts.get(2, 0),
        },
        "missing_requirements": missing,
        "warnings": warnings,
        "model_config": _get_model_config_summary(db),
    }


def _get_provider_policy() -> dict:
    from app.core.llm_router import get_provider_policy
    return get_provider_policy()


def _get_model_config_summary(db: Session) -> dict:
    """Model config visibility for readiness endpoint."""
    try:
        from app.services.model_config import get_all_active_configs
        configs = get_all_active_configs(db)
        return {
            "persistent": True,
            "modules": {c["module"]: {"provider": c["provider"], "model": c["model"], "activated_at": c["activated_at"], "activated_by": c["activated_by"]} for c in configs},
        }
    except Exception as exc:
        log.warning("ops: _get_model_config_summary failed: %s", exc)
        return {"persistent": False, "modules": {}}


# ---------------------------------------------------------------------------
# LLM Budget
# ---------------------------------------------------------------------------

@router.get("/llm-budget")
def get_llm_budget(
    _auth: bool = Depends(require_operator),
):
    """Return current LLM usage summary and limits."""
    from app.core.llm_budget import get_usage_summary
    return get_usage_summary()


@router.get("/sentry-budget")
def get_sentry_budget(
    _auth: bool = Depends(require_operator),
    refresh: bool = False,
):
    """Return Sentry quota usage for the past 30 days. Complements
    /ops/llm-budget: operator can see remaining errors / transactions /
    replays / profiles / cron-monitor budget before the monthly email
    arrives. Cached 5min; pass ?refresh=true to bypass.

    Graceful when SENTRY_AUTH_TOKEN / SENTRY_ORG not set — returns
    {"configured": false, "reason": ...} so the ops dashboard still
    renders a "not configured" tile instead of 500-ing."""
    from app.services.sentry_quota import get_quota_summary
    return get_quota_summary(refresh=refresh)


@router.get("/sentry-status")
def get_sentry_status(
    _auth: bool = Depends(require_operator),
):
    """Surfaces the current Sentry configuration split — backend DSN
    (from `SENTRY_DSN`) vs frontend DSN (from `NEXT_PUBLIC_SENTRY_DSN`
    read from `dashboard/.env.local`). Lets the operator verify at a
    glance that SENTRY-1 split is preserved (DSNs differ, distinct
    project IDs), without having to SSH and diff files.

    The DSN "project ID" component is the trailing numeric segment of
    the DSN URL (Sentry's internal numeric project identifier). We do
    NOT return the full DSN — only the project ID, the org hostname
    region, and a boolean `split_ok`. Zero secret exposure.
    """
    import os
    from pathlib import Path
    import re

    def _extract_project_id(dsn: str) -> str | None:
        m = re.search(r"/(\d+)\s*$", (dsn or "").strip())
        return m.group(1) if m else None

    def _extract_region(dsn: str) -> str | None:
        m = re.search(r"ingest\.([a-z]+)\.sentry\.io", dsn or "")
        return m.group(1) if m else None

    backend_dsn = os.getenv("SENTRY_DSN", "")

    # Read NEXT_PUBLIC_SENTRY_DSN from dashboard/.env.local. Gitignored
    # but mounted on this VPS.
    dashboard_env = Path("/opt/wishspark/dashboard/.env.local")
    frontend_dsn = ""
    if dashboard_env.is_file():
        for line in dashboard_env.read_text().splitlines():
            if line.startswith("NEXT_PUBLIC_SENTRY_DSN="):
                frontend_dsn = line.split("=", 1)[1].strip()
                break

    be_id = _extract_project_id(backend_dsn)
    fe_id = _extract_project_id(frontend_dsn)

    return {
        "backend": {
            "configured": bool(backend_dsn),
            "project_id": be_id,
            "region": _extract_region(backend_dsn),
        },
        "frontend": {
            "configured": bool(frontend_dsn),
            "project_id": fe_id,
            "region": _extract_region(frontend_dsn),
        },
        "split_ok": bool(be_id and fe_id and be_id != fe_id),
        "same_dsn_warning": bool(backend_dsn and frontend_dsn and backend_dsn == frontend_dsn),
    }


@router.get("/audit-telemetry")
def get_audit_telemetry(
    _auth: bool = Depends(require_operator),
    days: int = 7,
):
    """Return per-audit fire-rate + findings trend over `days` days.

    Backed by `app.services.audit_telemetry`. Redis HASH
    `hs:audit_telemetry:{audit}:{day}` populated by preflight audits +
    invariant_monitor. Each entry:
      - runs: total executions observed in the window
      - findings_total: cumulative findings count
      - findings_last: findings count on the most recent day seen
      - last_severity: "info" / "warn" / "critical"
      - last_day: YYYY-MM-DD of most recent record
      - days_seen: distinct days with activity

    Operator view: "which audits fired this week, and when did each
    one last produce findings?" — the slow-drift signal that a
    point-in-time preflight pass can't answer.

    `days` is clamped to [1, 90] to match the TTL envelope.
    """
    from app.services.audit_telemetry import read_all_audits

    days_clamped = max(1, min(int(days), 90))
    summary = read_all_audits(days=days_clamped)

    total_runs = sum(s["runs"] for s in summary.values())
    active_audits = len(summary)
    audits_with_findings = sum(
        1 for s in summary.values() if s["findings_total"] > 0
    )

    return {
        "window_days": days_clamped,
        "active_audits": active_audits,
        "audits_with_findings_in_window": audits_with_findings,
        "total_runs": total_runs,
        "audits": summary,
    }


@router.get("/compare-toggle-usage")
def get_compare_toggle_usage(
    _auth: bool = Depends(require_operator),
    days: int = 30,
):
    """Daily count of "compare to previous period" toggle usage.

    Backed by Redis HASH `hs:compare_toggle_usage:v1` populated at the
    `resolve_compare_utc_bounds` chokepoint — every compare-window
    request increments today's bucket. TTL 90d.

    Operator view: adoption trend for the comparison toggle. Pairs
    with the brutal Lite-vs-competitor audit hypothesis ("merchants
    expect compare deltas") — surfaces real usage so the hypothesis
    is data-backed, not vibes.

    `days` is clamped to [1, 90] to match the Redis TTL envelope.
    Response shape:
      - window_days: int
      - total_compare_requests: int (sum across window)
      - active_days: int (distinct YYYY-MM-DD buckets seen)
      - daily: list[{day: "YYYY-MM-DD", count: int}] — most recent first
    """
    from datetime import datetime, timedelta, timezone as _tzc
    from app.core.redis_client import _client

    from app.core.silent_fallback import record_silent_return

    days_clamped = max(1, min(int(days), 90))
    rc = _client()
    if rc is None:
        record_silent_return("ops.compare_toggle_usage.no_redis")
        return {
            "window_days": days_clamped,
            "total_compare_requests": 0,
            "active_days": 0,
            "daily": [],
            "redis_available": False,
        }

    raw = rc.hgetall("hs:compare_toggle_usage:v1") or {}
    today = datetime.now(_tzc.utc).date()
    cutoff = today - timedelta(days=days_clamped - 1)

    daily: list[dict] = []
    total = 0
    for raw_field, raw_value in raw.items():
        field = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        try:
            day_date = datetime.strptime(field, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day_date < cutoff:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        daily.append({"day": field, "count": count})
        total += count

    daily.sort(key=lambda r: r["day"], reverse=True)

    return {
        "window_days": days_clamped,
        "total_compare_requests": total,
        "active_days": len(daily),
        "daily": daily,
        "redis_available": True,
    }


@router.get("/dashboard-health")
def get_dashboard_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Live status of the dashboard-drift preventer pipeline.

    Surfaces:
      * unresolved `dashboard_asset_drift` alerts (detection)
      * unresolved `dashboard_asset_drift_auto_remediation_failed` alerts
        (escalations needing operator attention)
      * current UTC-hour auto-remediation attempt counter + whether the
        kill switch is engaged
      * last remediation audit_log row (success/failure + timestamp)

    Use this to verify the four-layer self-heal system is operating as
    expected without tailing logs. Frontend `/ops/` tile consumes this
    directly.
    """
    from datetime import timedelta
    from app.services import dashboard_auto_remediation as remed
    from app.core.redis_client import _client

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

    unresolved_drift = db.execute(
        text(
            "SELECT id, created_at, summary, detail "
            "FROM ops_alerts WHERE alert_type = :k AND resolved = false "
            "AND created_at >= :c ORDER BY created_at DESC LIMIT 5"
        ),
        {"k": remed._ALERT_TYPE, "c": cutoff},
    ).mappings().all()

    escalations = db.execute(
        text(
            "SELECT id, created_at, summary, detail "
            "FROM ops_alerts WHERE alert_type = :k AND resolved = false "
            "AND created_at >= :c ORDER BY created_at DESC LIMIT 5"
        ),
        {"k": remed._FOLLOWUP_FAIL, "c": cutoff},
    ).mappings().all()

    last_remediation = db.execute(
        text(
            "SELECT id, created_at, status, target_id, metadata_json "
            "FROM audit_log WHERE action_type = :a "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"a": remed._AUDIT_ACTION},
    ).mappings().first()

    hour_count = 0
    on_cooldown = False
    try:
        rc = _client()
        if rc is not None:
            count_raw = rc.get(remed._rate_limit_key())
            hour_count = int(count_raw) if count_raw is not None else 0
            on_cooldown = bool(rc.exists(remed._cooldown_key()))
    except Exception as exc:
        log.warning("dashboard-health: redis read failed: %s", exc)

    return {
        "preventer_enabled": remed.is_enabled(),
        "unresolved_drift_alerts": [dict(r) for r in unresolved_drift],
        "unresolved_escalations": [dict(r) for r in escalations],
        "this_hour": {
            "attempts": hour_count,
            "limit": remed._RATE_LIMIT_PER_HOUR,
            "on_back_to_back_cooldown": on_cooldown,
        },
        "last_remediation": dict(last_remediation) if last_remediation else None,
    }


@router.get("/email-health")
def get_email_health(
    _auth: bool = Depends(require_operator),
):
    """Email deliverability health — Resend domain verification state.

    Returns the cached domain status (verified / failed / pending / unknown)
    plus the last-verified sticky state used by the hourly flip-detection
    task. When `verified=false` every email through a `@hedgesparkhq.com`
    sender is short-circuited by `send_email()` to avoid burning Resend
    API quota and polluting `merchant_emails` with ambiguous `send_failed`
    rows — see `app/services/email_deliverability.py` and
    `docs/RESEND_DNS_RUNBOOK.md`.
    """
    from app.services.email_deliverability import (
        get_domain_status,
        read_last_verified_state,
    )
    status = get_domain_status()
    return {
        "verified": status.get("verified", True),
        "status": status.get("status", "unknown"),
        "reason": status.get("reason", ""),
        "from_cache": status.get("from_cache", False),
        "fetched_at": status.get("fetched_at"),
        "last_verified_state_sticky": read_last_verified_state(),
        "send_suppressed_while_failed": True,
        "runbook": "docs/RESEND_DNS_RUNBOOK.md",
    }


@router.get("/silent-fallback")
def get_silent_fallback_summary(
    days: int = Query(default=1, ge=1, le=7),
    _auth: bool = Depends(require_operator),
):
    """Silent-fallback observability: how often each service took its
    Redis-down fast path. When Redis is healthy these counters are all 0
    or near-0; a sudden spike in any service means the fast path has
    become the slow path and degrades a subsystem silently. Tier 2.1
    of the top-1 hardening roadmap."""
    from app.core.silent_fallback import read_summary
    return read_summary(days=days, top_n=50)


@router.get("/compliance")
def get_compliance_score(
    force_refresh: bool = Query(default=False),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return the live security + GDPR compliance score.

    Components:
      * security_probes       — security heartbeat pass rate
      * gdpr_sla              — active SLA breach count
      * consent_rate          — 7d tracker consent ratio
      * retention_sweep       — last sweep freshness
      * security_guard_wall   — preflight guard health
      * learning_isolation    — evidence_source gate status
      * pii_masking_coverage  — static PII-in-log scan

    Passing `force_refresh=true` recomputes instead of reading the cache.
    The daily digest renders a one-line summary built from the same data.
    """
    from app.services.compliance_score import (
        compute_compliance_score,
        get_cached_compliance_score,
    )
    if not force_refresh:
        cached = get_cached_compliance_score()
        if cached is not None:
            return cached
    return compute_compliance_score(db)


# ---------------------------------------------------------------------------
# Ops Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts")
def list_unresolved_alerts(
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List unresolved operational alerts."""
    from app.services.alerting import get_unresolved_alerts
    alerts = get_unresolved_alerts(db, severity=severity, limit=limit)
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "severity": a.severity,
            "source": a.source,
            "alert_type": a.alert_type,
            "shop_domain": a.shop_domain,
            "summary": a.summary,
            "detail": a.detail,
            "resolved": a.resolved,
        }
        for a in alerts
    ]


@router.get("/alerts/recent")
def list_recent_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List recent alerts (both resolved and unresolved)."""
    from app.models.ops_alert import OpsAlert
    alerts = (
        db.query(OpsAlert)
        .order_by(OpsAlert.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "severity": a.severity,
            "source": a.source,
            "alert_type": a.alert_type,
            "shop_domain": a.shop_domain,
            "summary": a.summary,
            "resolved": a.resolved,
            "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        }
        for a in alerts
    ]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert_endpoint(
    alert_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Mark an alert as resolved."""
    from app.services.alerting import resolve_alert
    from app.models.ops_alert import OpsAlert
    alert = db.get(OpsAlert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    if alert.resolved:
        return {"status": "already_resolved", "id": alert_id}
    resolve_alert(db, alert_id)
    db.commit()
    return {"status": "resolved", "id": alert_id}


# ---------------------------------------------------------------------------
# GDPR Export Retrieval
# ---------------------------------------------------------------------------

@router.get("/gdpr/exports")
def list_gdpr_exports(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List GDPR data request exports (customers_data_request type only)."""
    from app.models.gdpr_request import GdprRequest
    q = db.query(GdprRequest).filter(GdprRequest.request_type == "customers_data_request")
    if status:
        q = q.filter(GdprRequest.status == status)
    rows = q.order_by(GdprRequest.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "shop_domain": r.shop_domain,
            "customer_id": r.customer_id,
            "status": r.status,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "processed_at": r.processed_at.isoformat() + "Z" if r.processed_at else None,
            "has_export": r.result_summary is not None and r.status == "completed",
        }
        for r in rows
    ]


@router.get("/gdpr/exports/{request_id}")
def get_gdpr_export(
    request_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Retrieve a specific GDPR customer data export.

    Returns the structured export payload for completed requests.
    Pending/failed requests return status only (no data).
    """
    from app.models.gdpr_request import GdprRequest
    req = db.get(GdprRequest, request_id)
    if not req:
        raise HTTPException(404, "GDPR request not found")
    if req.request_type != "customers_data_request":
        raise HTTPException(400, "Not a data request export")

    base = {
        "id": req.id,
        "shop_domain": req.shop_domain,
        "customer_id": req.customer_id,
        "status": req.status,
        "created_at": req.created_at.isoformat() + "Z" if req.created_at else None,
        "processed_at": req.processed_at.isoformat() + "Z" if req.processed_at else None,
    }

    if req.status == "completed" and req.result_summary:
        # Receipt-only since 2026-05-14 (TIER_2 GDPR Art. 5 minimisation).
        # Result summary holds counts + delivery status + recipient_hash;
        # the raw PII export was delivered via email_orchestrator and is
        # not retained at rest. To re-send: POST /ops/gdpr/exports/{id}/redeliver.
        try:
            base["receipt"] = json.loads(req.result_summary)
        except (json.JSONDecodeError, ValueError):
            base["receipt"] = req.result_summary
    elif req.status == "failed":
        base["error"] = req.error_detail
        base["redeliver_hint"] = (
            f"POST /ops/gdpr/exports/{req.id}/redeliver to retry "
            f"(rebuilds export from source tables)"
        )
    elif req.status == "pending":
        base["note"] = "Export is queued and will be processed within the next worker cycle."

    return base


@router.post("/gdpr/exports/{request_id}/redeliver")
def redeliver_gdpr_export(
    request_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Re-trigger an Art. 15 export delivery for a customers_data_request.

    Resets the request to pending so the next gdpr_worker cycle rebuilds
    the export from source tables (events, shop_orders, etc.) and
    re-attempts email delivery. Used when:
      - the original delivery failed (status='failed')
      - the customer reports they did not receive the email
      - operator needs to refresh after a customer_email correction

    Source data must still exist; if a matching customers_redact has
    since deleted it, the rebuilt export will be empty (that's a
    legitimate state — Art. 17 wins over Art. 15 for the same subject).
    """
    from app.models.gdpr_request import GdprRequest
    req = db.get(GdprRequest, request_id)
    if not req:
        raise HTTPException(404, "GDPR request not found")
    if req.request_type != "customers_data_request":
        raise HTTPException(400, "Redeliver is only valid for customers_data_request")
    if req.status not in ("failed", "completed"):
        raise HTTPException(
            400,
            f"Request status='{req.status}' — only failed/completed rows can "
            f"be redelivered (pending/processing rows are already queued)",
        )

    req.status = "pending"
    req.processed_at = None
    req.error_detail = None
    req.result_summary = None
    db.commit()
    log.info("ops.redeliver_gdpr_export: id=%d reset to pending", request_id)
    return {"status": "queued", "id": request_id}


# ---------------------------------------------------------------------------
# Action Approvals (TIER_1 human-gated execution)
# ---------------------------------------------------------------------------

@router.get("/approvals")
def list_pending_approvals(
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List pending action approvals awaiting human decision."""
    from app.models.action_approval import ActionApproval
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Expire old approvals first
    db.execute(text(
        "UPDATE action_approvals SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at < :now"
    ), {"now": now})
    db.commit()

    approvals = (
        db.query(ActionApproval)
        .filter(ActionApproval.status == "pending")
        .order_by(ActionApproval.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "action_type": a.action_type,
            "target_id": a.target_id,
            "shop_domain": a.shop_domain,
            "status": a.status,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "expires_at": a.expires_at.isoformat() + "Z" if a.expires_at else None,
            "audit_log_id": a.audit_log_id,
        }
        for a in approvals
    ]


@router.post("/approvals/{approval_id}/approve")
def approve_action(
    approval_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Approve and execute a pending TIER_1 action.

    Executes the action through ACTION_REGISTRY (same safety path as orchestrator).
    Writes audit_log with actor_name="human_approval".
    """
    from app.models.action_approval import ActionApproval
    from app.services.orchestrator import ACTION_REGISTRY, _is_on_cooldown, _set_cooldown
    from app.services.audit import write_audit_log
    from app.services.outcome_evaluator import record_pending_outcome
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    approval = db.get(ActionApproval, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.status != "pending":
        raise HTTPException(409, f"Approval already {approval.status}")
    if approval.expires_at < now:
        approval.status = "expired"
        db.commit()
        raise HTTPException(410, "Approval expired")

    # Validate action still exists in registry
    entry = ACTION_REGISTRY.get(approval.action_type)
    if not entry:
        raise HTTPException(400, f"Unknown action: {approval.action_type}")
    action_fn = entry[0]

    # Cooldown check (advisory — human can override but we warn)
    if _is_on_cooldown(approval.action_type, approval.target_id or ""):
        log.warning("ops: executing approved action despite cooldown: %s %s", approval.action_type, approval.target_id)

    # Execute
    try:
        exec_result = action_fn(db, approval.target_id or "")
    except Exception as exc:
        approval.status = "approved"
        approval.decided_at = now
        approval.decided_by = "operator"
        approval.reason = f"approved but execution failed: {str(exc)[:200]}"
        db.commit()
        raise HTTPException(500, f"Action execution failed: {str(exc)[:200]}")

    # Update approval
    approval.status = "approved"
    approval.decided_at = now
    approval.decided_by = "operator"

    # Audit
    audit_entry = write_audit_log(
        db,
        actor_type="human",
        actor_name="human_approval",
        action_type=f"approved_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        after_state={"result": exec_result, "approval_id": approval_id},
        status="completed",
        approval_mode="human_approved",
    )

    # Outcome tracking
    record_pending_outcome(
        db,
        audit_log_id=audit_entry.id,
        action_type=f"approved_{approval.action_type}",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
    )

    _set_cooldown(approval.action_type, approval.target_id or "")
    db.commit()

    return {
        "status": "approved_and_executed",
        "approval_id": approval_id,
        "action_type": approval.action_type,
        "target_id": approval.target_id,
        "result": exec_result,
    }


@router.post("/approvals/{approval_id}/reject")
def reject_action(
    approval_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a pending action approval."""
    from app.models.action_approval import ActionApproval
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    approval = db.get(ActionApproval, approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.status != "pending":
        raise HTTPException(409, f"Approval already {approval.status}")

    approval.status = "rejected"
    approval.decided_at = now
    approval.decided_by = "operator"

    write_audit_log(
        db,
        actor_type="human",
        actor_name="human_approval",
        action_type=f"rejected_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        status="rejected",
        approval_mode="human_approved",
    )

    db.commit()
    return {"status": "rejected", "approval_id": approval_id}


# ---------------------------------------------------------------------------
# Bug Fix Candidates
# ---------------------------------------------------------------------------

@router.get("/scaling/snapshots")
def get_scaling_snapshots(
    limit: int = Query(default=14, le=90),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return recent daily system snapshots."""
    from app.services.scaling_intelligence import get_recent_snapshots
    return {"snapshots": get_recent_snapshots(db, limit)}


@router.get("/scaling/forecast")
def get_scaling_forecast(
    horizon: int = Query(default=30, le=90),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return scaling forecast projections."""
    from app.services.scaling_intelligence import build_forecast
    return build_forecast(db, horizon)


@router.get("/scaling/recommendations")
def get_scaling_recommendations(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return active scaling recommendations."""
    from app.services.scaling_intelligence import get_active_recommendations
    return {"recommendations": get_active_recommendations(db)}


# ---------------------------------------------------------------------------
# Project Brain
# ---------------------------------------------------------------------------

@router.get("/merchant-brain/summary")
def merchant_brain_summary(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Aggregated state for the merchant brain (Brain Vero):
      - feature flag state
      - decision counts (last 24h, last 7d)
      - per-action_kind breakdown (dispatched / deferred / blocked / no_action)
      - per-shop tick activity
    Useful for operator visibility before/after un-park ceremony.
    """
    from app.services.merchant_brain import is_brain_enabled
    from sqlalchemy import text as _text
    rows_24h = db.execute(_text("""
        SELECT action_kind,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE limb_dispatched IS NOT NULL) AS dispatched,
               COUNT(*) FILTER (
                 WHERE limb_response ? 'blocked_by_review'
               ) AS blocked,
               COUNT(*) FILTER (
                 WHERE limb_response ? 'deferred_to'
               ) AS deferred
        FROM brain_decisions
        WHERE decision_at > NOW() - INTERVAL '24 hours'
        GROUP BY action_kind
        ORDER BY total DESC
    """)).fetchall()
    rows_7d = db.execute(_text("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT shop_domain) AS shops,
               COUNT(*) FILTER (WHERE limb_dispatched IS NOT NULL) AS dispatched_total
        FROM brain_decisions
        WHERE decision_at > NOW() - INTERVAL '7 days'
    """)).fetchone()
    return {
        "enabled": is_brain_enabled(),
        "window_24h": [
            {
                "action_kind": r[0],
                "total": r[1],
                "dispatched": r[2],
                "blocked": r[3],
                "deferred": r[4],
            }
            for r in rows_24h
        ],
        "window_7d": {
            "total_decisions": rows_7d[0] if rows_7d else 0,
            "active_shops": rows_7d[1] if rows_7d else 0,
            "dispatched_total": rows_7d[2] if rows_7d else 0,
        },
    }


@router.get("/merchant-brain/decisions")
def merchant_brain_decisions(
    shop: str | None = Query(default=None),
    action_kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List recent brain_decisions rows with optional filters. Operator
    audit trail for un-park verification + outcome inspection."""
    from sqlalchemy import text as _text
    clauses = ["1=1"]
    params: dict = {"limit": limit}
    if shop:
        clauses.append("shop_domain = :shop")
        params["shop"] = shop
    if action_kind:
        clauses.append("action_kind = :action_kind")
        params["action_kind"] = action_kind
    sql = f"""
        SELECT id, decision_at, shop_domain, action_kind, synthesis,
               rationale, limb_dispatched, limb_response,
               expected_outcome_metric, outcome_status, outcome_evaluated_at
        FROM brain_decisions
        WHERE {" AND ".join(clauses)}
        ORDER BY decision_at DESC
        LIMIT :limit
    """
    rows = db.execute(_text(sql), params).fetchall()
    return [
        {
            "id": r[0],
            "decision_at": r[1].isoformat() + "Z" if r[1] else None,
            "shop_domain": r[2],
            "action_kind": r[3],
            "synthesis": r[4],
            "rationale": r[5],
            "limb_dispatched": r[6],
            "limb_response": r[7],
            "expected_outcome_metric": r[8],
            "outcome_status": r[9],
            "outcome_evaluated_at": (
                r[10].isoformat() + "Z" if r[10] else None
            ),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

@router.get("/incidents")
def list_support_incidents(
    status: str = Query(default="active"),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    List support incidents. status=active returns open/triaged/investigating.
    status=all returns all. status=resolved returns resolved only.
    """
    from app.models.support_incident import SupportIncident
    from sqlalchemy import desc

    q = db.query(SupportIncident)
    if status == "active":
        q = q.filter(SupportIncident.status.in_(["open", "triaged", "investigating"]))
    elif status == "resolved":
        q = q.filter(SupportIncident.status == "resolved")
    # status=all → no filter

    incidents = q.order_by(desc(SupportIncident.created_at)).limit(limit).all()

    return {
        "count": len(incidents),
        "incidents": [
            {
                "id": i.id,
                "created_at": i.created_at.isoformat() + "Z" if i.created_at else None,
                "shop_domain": i.shop_domain,
                "classification": i.classification,
                "severity": i.severity,
                "affected_area": i.affected_area,
                "status": i.status,
                "linked_ops_alert_id": i.linked_ops_alert_id,
                "resolved_by": i.resolved_by,
                "message_preview": (i.original_message or "")[:120],
            }
            for i in incidents
        ],
    }


# ---------------------------------------------------------------------------
# Meta-review
# ---------------------------------------------------------------------------

@router.get("/diagnostic")
def get_system_diagnostic(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Unified system diagnostic — single-call comprehensive health assessment.

    Returns ALL operational signals in one response:
    vitals, LLM budget, attribution pipeline, alerts, onboarding funnel,
    webhook fleet, evolution pipeline, merchant data health.

    Each section is independently resilient — one subsystem failure
    doesn't block the others.
    """
    from app.services.system_diagnostic import build_system_diagnostic
    return build_system_diagnostic(db)


@router.get("/system-health")
def get_system_health(
    _auth: bool = Depends(require_operator),
):
    """
    Unified CTO-level system health state.

    Returns the latest synthesized health assessment from the agent worker's
    Phase 0 CTO check.  Includes all dimensions, trends, urgent items, and
    recommendations.  Updated every 15 minutes (agent_worker cycle).
    """
    from app.core.redis_client import cache_get
    cached = cache_get("hs:system_health")
    if cached is not None:
        return cached

    # Fallback: compute live
    from app.core.database import SessionLocal
    from app.services.system_health_synthesizer import synthesize_health
    db = SessionLocal()
    try:
        return synthesize_health(db).to_dict()
    finally:
        db.close()


@router.get("/attribution/health")
def get_attribution_pipeline_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Attribution pipeline health — shows whether the data flow is working."""
    from sqlalchemy import func
    from app.models.shop_order import ShopOrder
    from app.models.visitor_purchase_session import VisitorPurchaseSession

    orders_total = db.query(func.count(ShopOrder.id)).scalar() or 0
    orders_by_source = db.execute(text(
        "SELECT source, COUNT(*) FROM shop_orders GROUP BY source"
    )).fetchall()

    vps_total = db.query(func.count(VisitorPurchaseSession.id)).scalar() or 0
    vps_attributed = db.execute(text(
        "SELECT COUNT(*) FROM visitor_purchase_sessions WHERE first_source IS NOT NULL"
    )).fetchone()

    return {
        "orders_total": orders_total,
        "orders_by_source": {r[0]: r[1] for r in orders_by_source},
        "visitor_purchase_sessions": vps_total,
        "attributed_sessions": vps_attributed[0] if vps_attributed else 0,
        "attribution_rate": round(
            (vps_attributed[0] if vps_attributed else 0) / max(orders_total, 1), 3
        ),
        "pipeline_status": "healthy" if vps_total > 0 else (
            "no_bridges" if orders_total > 0 else "no_data"
        ),
        "diagnosis": (
            "Orders exist but no visitor-purchase bridges. "
            "The Custom Pixel may not be reading the _hs_vid cookie (ITP, cross-origin). "
            "Consider asking the merchant to add spark-attribution.js to the checkout page."
        ) if orders_total > 0 and vps_total == 0 else (
            "No orders yet." if orders_total == 0 else "Pipeline is flowing."
        ),
    }


@router.get("/tracker/status")
def get_tracker_fleet_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Fleet-wide tracker delivery status."""
    from sqlalchemy import func
    from app.models.merchant import Merchant

    rows = (
        db.query(
            Merchant.tracker_delivery_method,
            func.count().label("count"),
            func.count(Merchant.script_tag_id).label("with_tag"),
        )
        .filter(Merchant.install_status == "active")
        .group_by(Merchant.tracker_delivery_method)
        .all()
    )

    methods = {}
    total = 0
    total_with_tag = 0
    for r in rows:
        methods[r[0]] = {"count": r[1], "with_script_tag": r[2]}
        total += r[1]
        total_with_tag += r[2]

    # operator-filter: ops admin diagnostic — listing every merchant
    # with broken script-tag is correct here, including operator shop.
    missing_tag = (
        db.query(Merchant.shop_domain)
        .filter(
            Merchant.install_status == "active",
            Merchant.script_tag_id.is_(None),
            Merchant.access_token.isnot(None),
        )
        .all()
    )

    return {
        "total_active": total,
        "with_script_tag": total_with_tag,
        "missing_script_tag": [r[0] for r in missing_tag],
        "by_delivery_method": methods,
        "tracker_version": __import__("app.core.tracker_version", fromlist=["TRACKER_VERSION"]).TRACKER_VERSION,
    }


@router.get("/digest/status")
def get_digest_delivery_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Merchant digest delivery status for current week."""
    from app.services.merchant_digest import get_digest_delivery_status
    return get_digest_delivery_status(db)


@router.get("/webhooks/status")
def get_webhook_fleet_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Fleet-wide webhook health summary."""
    from app.services.webhook_monitor import get_fleet_webhook_summary
    return get_fleet_webhook_summary(db)


@router.get("/webhooks/status/{shop_domain}")
def get_merchant_webhook_status(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
):
    """Single merchant webhook status."""
    from app.services.webhook_monitor import get_merchant_webhook_status as get_status
    status = get_status(shop_domain)
    if not status:
        return {"status": "not_checked", "shop": shop_domain}
    return status


# ---------------------------------------------------------------------------
# Sentry verification (operator-only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Loop health (operator-only)
# ---------------------------------------------------------------------------

@router.get("/onboarding-health")
def ops_onboarding_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Onboarding pipeline health: stuck merchants, pixel abandonment,
    slow activation, and overall onboarding funnel metrics.
    """
    from app.services.onboarding_health import check_onboarding_health
    return check_onboarding_health(db)


@router.get("/onboarding-funnel")
def ops_onboarding_funnel(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    """
    Aggregate onboarding funnel: step-by-step conversion rates,
    drop-off points, median times, and session counts.
    """
    from app.services.onboarding_funnel import get_aggregate_funnel
    return get_aggregate_funnel(db, days)


@router.get("/onboarding-funnel/{shop_domain}")
def ops_onboarding_funnel_shop(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Per-shop onboarding funnel state with milestones and interaction counts."""
    from app.services.onboarding_funnel import get_shop_funnel
    return get_shop_funnel(db, shop_domain)


@router.get("/onboarding-friction")
def ops_onboarding_friction(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Active friction signals: merchants exhibiting stall, confusion,
    or drop-off patterns. Includes improvement insights.
    """
    from app.services.onboarding_funnel import detect_friction, generate_insights
    return {
        "friction_signals": detect_friction(db),
        "insights": generate_insights(db),
    }


@router.get("/tier-check")
def ops_tier_check(
    files: str,
    _auth: bool = Depends(require_operator),
):
    """
    Check execution tier for a comma-separated list of file paths.
    Returns tier classification, reasons, and whether agent modification is allowed.
    """
    from app.core.tier_check import check_tier
    file_list = [f.strip() for f in files.split(",") if f.strip()]
    if not file_list:
        raise HTTPException(
            status_code=400,
            detail="No files provided. Use ?files=path1,path2",
        )
    result = check_tier(file_list)
    return {
        "tier": result.tier,
        "label": result.label,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "affected_domains": result.affected_domains,
        "reasons": result.reasons,
    }


@router.get("/file-locks")
def ops_file_locks(
    _auth: bool = Depends(require_operator),
):
    """List all currently held file locks. Returns empty list when no locks active."""
    from app.core.file_lock import list_active_locks
    locks = list_active_locks()
    return {"active_locks": locks, "count": len(locks)}


@router.get("/sentry-intake/health")
def ops_sentry_intake_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    hours: int = Query(default=24, ge=1, le=168),
):
    """
    Sentry intake health dashboard — migration readiness visibility.

    Shows:
    - webhook vs email counts in the last N hours
    - last webhook/email timestamps
    - parse error count
    - webhook health status (healthy / degraded / dark)
    - migration readiness assessment
    """
    from app.models.sentry_incident import SentryIncident
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=hours)

    # Count by source_type in window
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .filter(SentryIncident.created_at >= cutoff)
        .group_by(SentryIncident.source_type)
        .all()
    )

    webhook_count = source_counts.get("sentry_webhook", 0)
    email_count = source_counts.get("email", 0)
    total = webhook_count + email_count

    # Last timestamp per source
    last_webhook = (
        db.query(func.max(SentryIncident.created_at))
        .filter(SentryIncident.source_type == "sentry_webhook")
        .scalar()
    )
    last_email = (
        db.query(func.max(SentryIncident.created_at))
        .filter(SentryIncident.source_type == "email")
        .scalar()
    )

    # Parse errors in window
    parse_errors = (
        db.query(func.count(SentryIncident.id))
        .filter(
            SentryIncident.status == "parse_error",
            SentryIncident.created_at >= cutoff,
        )
        .scalar() or 0
    )

    # Webhook health assessment
    if webhook_count > 0 and email_count == 0:
        webhook_status = "healthy"
        migration_ready = True
    elif webhook_count > 0 and email_count > 0:
        webhook_status = "active_with_email_fallback"
        migration_ready = True  # webhook is working, email can be disabled
    elif webhook_count == 0 and email_count > 0:
        webhook_status = "dark"
        migration_ready = False
    elif total == 0:
        webhook_status = "no_incidents"
        migration_ready = None  # can't assess with no data
    else:
        webhook_status = "unknown"
        migration_ready = False

    # Hours since last webhook (for staleness detection)
    hours_since_webhook = None
    if last_webhook:
        hours_since_webhook = round((now - last_webhook).total_seconds() / 3600, 1)

    return {
        "window_hours": hours,
        "webhook_count": webhook_count,
        "email_fallback_count": email_count,
        "total_incidents": total,
        "parse_errors": parse_errors,
        "webhook_pct": round(webhook_count / total * 100, 1) if total > 0 else None,
        "last_webhook_at": last_webhook.isoformat() + "Z" if last_webhook else None,
        "last_email_at": last_email.isoformat() + "Z" if last_email else None,
        "hours_since_last_webhook": hours_since_webhook,
        "webhook_status": webhook_status,
        "migration_ready": migration_ready,
        "migration_note": (
            "Safe to disable Sentry email alerts"
            if migration_ready
            else "Webhook not receiving — keep email alerts active"
            if migration_ready is False
            else "No incidents in window — configure Sentry webhook first"
        ),
    }


@router.post("/sentry-test")
def sentry_test_error(
    _auth: bool = Depends(require_operator),
):
    """
    Intentionally raise an exception to verify Sentry is capturing errors
    with correct tags (request_id, shop_domain, route).

    Operator-only. Returns 500 if Sentry is active (exception propagates).
    Returns 200 with status if Sentry is not configured.
    """
    try:
        import sentry_sdk
        if sentry_sdk.is_initialized():
            raise RuntimeError("Sentry verification test — this error is intentional")
        return {"status": "sentry_not_initialized", "detail": "Set SENTRY_DSN in .env and restart"}
    except ImportError:
        return {"status": "sentry_not_installed", "detail": "pip install sentry-sdk[fastapi]"}


# ---------------------------------------------------------------------------
# Merchant lifecycle email visibility
# ---------------------------------------------------------------------------

@router.get("/emails")
def ops_email_history(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    email_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Email delivery history — what was sent, when, to whom, and why
    it was suppressed. Filterable by shop and email type.
    """
    from app.services.merchant_email_service import get_email_history
    return get_email_history(db, shop_domain=shop, email_type=email_type, limit=limit)


# ---------------------------------------------------------------------------
# Email journey visibility
# ---------------------------------------------------------------------------

@router.get("/journey")
def ops_journey(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
):
    """
    Merchant email journey state — per-merchant lifecycle tracking.
    Shows invite/open/click/onboarding/followup/activation timestamps.
    """
    from app.services.email_journey import get_journey_summary
    return get_journey_summary(db, shop_domain=shop)


@router.get("/journey/stats")
def ops_journey_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Journey funnel stats — count of merchants in each stage.
    Answers "how many merchants are in each stage?" without fetching all rows.
    """
    from sqlalchemy import func as sqlfunc
    from app.models.merchant_journey_state import MerchantJourneyState
    rows = (
        db.query(
            MerchantJourneyState.current_stage,
            sqlfunc.count(MerchantJourneyState.id),
        )
        .group_by(MerchantJourneyState.current_stage)
        .all()
    )
    stages = {row[0]: row[1] for row in rows}
    total = sum(stages.values())
    return {"total": total, "stages": stages}


@router.get("/email-events")
def ops_email_events(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Resend delivery events — delivered, opened, clicked, bounced, complained.
    Critical for monitoring bounce/complaint rates.
    """
    from app.models.email_event import EmailEvent

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    q = db.query(EmailEvent).order_by(EmailEvent.created_at.desc())
    if shop:
        q = q.filter(EmailEvent.shop_domain == shop)
    if event_type:
        q = q.filter(EmailEvent.event_type == event_type)
    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": _ts(r.created_at),
            "resend_email_id": r.resend_email_id,
            "event_type": r.event_type,
            "to_email": r.to_email,
            "shop_domain": r.shop_domain,
            "email_type": r.email_type,
            "event_timestamp": _ts(r.event_timestamp),
        }
        for r in rows
    ]


@router.get("/email-events/stats")
def ops_email_event_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Email event breakdown — count by event_type.
    Quick health check: are bounces/complaints growing?
    """
    from sqlalchemy import func as sqlfunc
    from app.models.email_event import EmailEvent
    rows = (
        db.query(
            EmailEvent.event_type,
            sqlfunc.count(EmailEvent.id),
        )
        .group_by(EmailEvent.event_type)
        .all()
    )
    return {row[0]: row[1] for row in rows}


@router.get("/merchant-scores")
def ops_merchant_scores(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Merchant priority scores — ranked by revenue opportunity.
    Shows which merchants to focus on for conversion and retention.
    """
    from app.services.merchant_scoring import score_all_merchants
    from dataclasses import asdict
    scores = score_all_merchants(db, limit=limit)
    return [asdict(s) for s in scores]


@router.get("/merchant/{shop_domain}/score")
def ops_merchant_score(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Single merchant priority score with sub-score breakdown."""
    from app.services.merchant_scoring import score_merchant
    from dataclasses import asdict
    try:
        return asdict(score_merchant(db, shop_domain))
    except Exception as exc:
        log.warning(
            "ops: score_merchant failed shop=%s: %s",
            shop_domain, exc,
        )
        raise HTTPException(
            status_code=500,
            detail="score_merchant_failed",
        )


@router.get("/feedback/themes")
def ops_feedback_themes(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Aggregated merchant feedback themes — recurring feature requests and suggestions
    grouped by product area. Shows demand signals for product roadmap.
    """
    from app.services.feedback_intelligence import get_feedback_summary
    return get_feedback_summary(db)


@router.get("/merchant/{shop_domain}/profile")
def ops_merchant_profile(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Unified merchant profile — everything an operator needs to understand
    a merchant's full state in one call.

    Answers: what state are they in? what did we send? what did they do?
    what did they reply? are they stuck / silent / at risk?
    """
    from app.models.merchant import Merchant
    from app.models.merchant_email import MerchantEmail
    from app.models.merchant_journey_state import MerchantJourneyState
    from app.models.inbound_email import InboundEmail
    from sqlalchemy import text as sa_text

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant_not_found")

    # Merchant identity + status
    identity = {
        "shop_domain": merchant.shop_domain,
        "contact_email": merchant.contact_email,
        "plan": merchant.plan,
        "billing_active": merchant.billing_active,
        "install_status": merchant.install_status,
        "installed_at": _ts(merchant.installed_at),
        "uninstalled_at": _ts(merchant.uninstalled_at),
        "onboarding_status": merchant.onboarding_status,
        "onboarding_error": merchant.onboarding_error,
        "onboarding_retry_count": merchant.onboarding_retry_count,
    }

    # Journey state
    journey = db.query(MerchantJourneyState).filter(
        MerchantJourneyState.shop_domain == shop_domain
    ).first()
    journey_info = None
    if journey:
        from app.services.email_journey import _journey_to_dict
        journey_info = _journey_to_dict(journey)
        journey_info["email_suppressed"] = journey.email_suppressed

    # Recent emails sent (last 10)
    emails_sent = (
        db.query(MerchantEmail)
        .filter(MerchantEmail.shop_domain == shop_domain)
        .order_by(MerchantEmail.created_at.desc())
        .limit(10)
        .all()
    )
    sent_list = [
        {"type": e.email_type, "status": e.status, "at": _ts(e.created_at),
         "suppressed_by": e.suppressed_by}
        for e in emails_sent
    ]

    # Inbound emails from merchant (last 10)
    inbound = (
        db.query(InboundEmail)
        .filter(InboundEmail.shop_domain == shop_domain)
        .order_by(InboundEmail.created_at.desc())
        .limit(10)
        .all()
    )
    inbound_list = [
        {"subject": ie.subject, "classification": ie.classification,
         "routing_status": ie.routing_status, "at": _ts(ie.created_at),
         "responded": ie.agent_response_sent_at is not None}
        for ie in inbound
    ]

    # Open incidents for this merchant
    incidents = db.execute(sa_text("""
        SELECT id, severity, alert_type, summary, created_at, resolved
        FROM ops_alerts
        WHERE shop_domain = :shop
        ORDER BY created_at DESC LIMIT 10
    """), {"shop": shop_domain}).fetchall()
    incident_list = [
        {"id": r[0], "severity": r[1], "type": r[2],
         "summary": r[3][:100] if r[3] else None,
         "at": r[4].isoformat() + "Z" if r[4] else None,
         "resolved": r[5]}
        for r in incidents
    ]

    # Activity: recent event count
    event_count_7d = db.execute(sa_text("""
        SELECT COUNT(*) FROM events
        WHERE shop_domain = :shop
          AND timestamp > :cutoff
    """), {
        "shop": shop_domain,
        "cutoff": int((_now_utc() - __import__('datetime').timedelta(days=7)).timestamp() * 1000),
    }).scalar() or 0

    # Risk assessment
    risk_signals = []
    if merchant.install_status != "active":
        risk_signals.append("UNINSTALLED")
    if merchant.onboarding_status == "failed":
        risk_signals.append(f"ONBOARDING_FAILED (retry {merchant.onboarding_retry_count or 0})")
    if journey and journey.email_suppressed:
        risk_signals.append(f"EMAIL_SUPPRESSED ({journey.email_suppressed})")
    if event_count_7d == 0 and merchant.onboarding_status == "ready":
        risk_signals.append("SILENT — 0 events in 7 days")
    if not merchant.contact_email:
        risk_signals.append("NO_CONTACT_EMAIL")
    if merchant.plan == "pro" and not merchant.billing_active:
        risk_signals.append("PRO_BUT_BILLING_INACTIVE")
    if not risk_signals:
        risk_signals.append("HEALTHY")

    return {
        "merchant": identity,
        "journey": journey_info,
        "emails_sent": sent_list,
        "inbound_emails": inbound_list,
        "incidents": incident_list,
        "activity": {"events_7d": event_count_7d},
        "risk_signals": risk_signals,
    }


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/inbound-emails")
def ops_inbound_emails(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    classification: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Inbound email log — merchant replies with classification and routing status.
    Includes body preview for operator triage without DB access.
    """
    from app.models.inbound_email import InboundEmail
    q = db.query(InboundEmail).order_by(InboundEmail.created_at.desc())
    if shop:
        q = q.filter(InboundEmail.shop_domain == shop)
    if classification:
        q = q.filter(InboundEmail.classification == classification)
    if status:
        q = q.filter(InboundEmail.routing_status == status)
    rows = q.limit(limit).all()

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    return [
        {
            "id": r.id,
            "created_at": _ts(r.created_at),
            "from_email": r.from_email,
            "shop_domain": r.shop_domain,
            "subject": r.subject,
            "body_preview": (r.body_text or "")[:200] or None,
            "classification": r.classification,
            "classification_confidence": r.classification_confidence,
            "classification_method": r.classification_method,
            "routing_status": r.routing_status,
            "routing_action": r.routing_action,
            "processed_at": _ts(r.processed_at),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-merchant email diagnostics — full trace without SSH
# ---------------------------------------------------------------------------

@router.get("/merchant/{shop_domain}/email-trace")
def ops_merchant_email_trace(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Complete email diagnostic trace for a single merchant.

    Returns everything needed to answer "why didn't merchant X get their email?"
    without SSH access.
    """
    from app.models.merchant import Merchant
    from app.models.merchant_email import MerchantEmail
    from app.models.merchant_journey_state import MerchantJourneyState
    from app.models.email_event import EmailEvent
    from app.models.inbound_email import InboundEmail

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    # 1. Merchant basics
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant_not_found")

    merchant_info = {
        "shop_domain": merchant.shop_domain,
        "contact_email": merchant.contact_email,
        "install_status": merchant.install_status,
        "plan": merchant.plan,
        "billing_active": merchant.billing_active,
        "onboarding_status": merchant.onboarding_status,
        "onboarding_error": merchant.onboarding_error,
        "onboarding_retry_count": merchant.onboarding_retry_count,
    }

    # 2. Journey state
    journey = db.query(MerchantJourneyState).filter(
        MerchantJourneyState.shop_domain == shop_domain
    ).first()
    journey_info = None
    if journey:
        from app.services.email_journey import _journey_to_dict
        journey_info = _journey_to_dict(journey)
        journey_info["email_suppressed"] = journey.email_suppressed
        journey_info["email_suppressed_at"] = _ts(journey.email_suppressed_at)

    # 3. Sent / suppressed emails (last 20)
    emails = (
        db.query(MerchantEmail)
        .filter(MerchantEmail.shop_domain == shop_domain)
        .order_by(MerchantEmail.created_at.desc())
        .limit(20)
        .all()
    )
    email_history = [
        {
            "id": e.id,
            "created_at": _ts(e.created_at),
            "email_type": e.email_type,
            "to_email": e.to_email,
            "status": e.status,
            "suppressed_by": e.suppressed_by,
            "resend_id": e.resend_id,
        }
        for e in emails
    ]

    # 4. Resend delivery events (last 20)
    events = (
        db.query(EmailEvent)
        .filter(EmailEvent.shop_domain == shop_domain)
        .order_by(EmailEvent.created_at.desc())
        .limit(20)
        .all()
    )
    delivery_events = [
        {
            "event_type": ev.event_type,
            "resend_email_id": ev.resend_email_id,
            "event_timestamp": _ts(ev.event_timestamp),
            "email_type": ev.email_type,
        }
        for ev in events
    ]

    # 5. Bounce/complaint suppression check (Redis)
    redis_suppressed = None
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            redis_suppressed = rc.get(f"hs:email_suppressed:{shop_domain}")
            if isinstance(redis_suppressed, bytes):
                redis_suppressed = redis_suppressed.decode()
    except Exception as exc:
        log.warning("ops: _ts failed: %s", exc)

    # 6. Inbound emails from this merchant (last 10)
    inbound = (
        db.query(InboundEmail)
        .filter(InboundEmail.shop_domain == shop_domain)
        .order_by(InboundEmail.created_at.desc())
        .limit(10)
        .all()
    )
    inbound_list = [
        {
            "id": ie.id,
            "created_at": _ts(ie.created_at),
            "subject": ie.subject,
            "classification": ie.classification,
            "routing_status": ie.routing_status,
        }
        for ie in inbound
    ]

    # 7. Diagnosis summary
    diagnosis = []
    if not merchant.contact_email:
        diagnosis.append("NO_CONTACT_EMAIL — merchant has no email address")
    if merchant.install_status != "active":
        diagnosis.append(f"UNINSTALLED — status={merchant.install_status}")
    if journey and journey.email_suppressed:
        diagnosis.append(f"EMAIL_SUPPRESSED — reason={journey.email_suppressed}")
    if redis_suppressed:
        diagnosis.append(f"REDIS_SUPPRESSED — {redis_suppressed}")
    if not emails:
        diagnosis.append("NO_EMAILS_EVER — no email attempts found")
    if not diagnosis:
        diagnosis.append("HEALTHY — no issues detected")

    return {
        "merchant": merchant_info,
        "journey": journey_info,
        "email_history": email_history,
        "delivery_events": delivery_events,
        "redis_suppression": redis_suppressed,
        "inbound_emails": inbound_list,
        "diagnosis": diagnosis,
    }


# ---------------------------------------------------------------------------
# Sentry incident triage visibility
# ---------------------------------------------------------------------------

@router.get("/incidents")
def ops_incidents(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    List Sentry incident families — grouped by fingerprint.
    Each entry is a family head with recurrence count.
    Filter by status: received, parsed, parse_error, triaged, linked, resolved, ignored.
    """
    from app.services.sentry_triage import get_incident_families
    return get_incident_families(db, status=status, limit=limit)


@router.get("/incidents/{incident_id}")
def ops_incident_detail(
    incident_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Full detail for a single incident, including parsed fields,
    raw email snapshot, and triage packet if generated.
    """
    from app.models.sentry_incident import SentryIncident

    inc = db.get(SentryIncident, incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    packet = None
    if inc.triage_packet:
        try:
            packet = json.loads(inc.triage_packet)
        except (json.JSONDecodeError, ValueError):
            packet = inc.triage_packet

    return {
        "id": inc.id,
        "created_at": inc.created_at.isoformat() + "Z" if inc.created_at else None,
        "source_message_id": inc.source_message_id,
        "source_type": inc.source_type,
        "status": inc.status,
        "parse_error": inc.parse_error,

        # Parsed
        "error_type": inc.error_type,
        "error_title": inc.error_title,
        "project": inc.project,
        "environment": inc.environment,
        "severity": inc.severity,
        "culprit": inc.culprit,
        "stack_trace": inc.stack_trace,
        "sentry_issue_url": inc.sentry_issue_url,

        # Fingerprint
        "fingerprint": inc.fingerprint,
        "fingerprint_input": inc.fingerprint_input,
        "family_head_id": inc.family_head_id,
        "recurrence_count": inc.recurrence_count,

        # Classification
        "subsystem_class": inc.subsystem_class,
        "merchant_impact": inc.merchant_impact,
        "affected_shop": inc.affected_shop,

        # AI triage
        "ai_triage_status": inc.ai_triage_status,
        "triage_packet": packet,

        # Integration
        "linked_ops_alert_id": inc.linked_ops_alert_id,
        "lesson_candidate_status": inc.lesson_candidate_status,

        # Raw (truncated for API response)
        "raw_subject": inc.raw_subject,
        "raw_from": inc.raw_from,
        "raw_body_length": len(inc.raw_body) if inc.raw_body else 0,
    }


@router.get("/incidents/{incident_id}/family")
def ops_incident_family(
    incident_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    List all incidents in the same family (same fingerprint).
    Shows recurrence timeline for a specific error pattern.
    """
    from app.models.sentry_incident import SentryIncident

    head = db.get(SentryIncident, incident_id)
    if not head:
        raise HTTPException(status_code=404, detail="Incident not found")

    fp = head.fingerprint
    if not fp:
        return {"family_head_id": incident_id, "members": [], "total": 0}

    members = (
        db.query(SentryIncident)
        .filter(SentryIncident.fingerprint == fp)
        .order_by(SentryIncident.created_at.desc())
        .limit(100)
        .all()
    )

    return {
        "family_head_id": incident_id,
        "fingerprint": fp,
        "fingerprint_input": head.fingerprint_input,
        "total": len(members),
        "members": [
            {
                "id": m.id,
                "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
                "status": m.status,
                "error_title": m.error_title,
                "source_message_id": m.source_message_id,
            }
            for m in members
        ],
    }


@router.get("/incidents/triage/queue")
def ops_triage_queue(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Incidents with generated triage packets ready for AI consumption.
    This is the handoff point for future Claude/OpenClaw integration.
    """
    from app.services.sentry_triage import get_triage_queue
    return get_triage_queue(db, limit=limit)


@router.get("/incidents/parse-errors")
def ops_parse_errors(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Incidents that failed parsing — for debugging the parser.
    """
    from app.models.sentry_incident import SentryIncident

    errors = (
        db.query(SentryIncident)
        .filter(SentryIncident.status == "parse_error")
        .order_by(SentryIncident.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat() + "Z" if e.created_at else None,
            "parse_error": e.parse_error,
            "raw_subject": e.raw_subject,
            "raw_from": e.raw_from,
            "raw_body_preview": (e.raw_body or "")[:500],
        }
        for e in errors
    ]


@router.get("/incidents/consumer/stats")
def ops_consumer_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Triage consumer pipeline statistics — how many incidents at each stage,
    how many candidates were created, how many were suppressed/deduped.
    """
    from app.models.sentry_incident import SentryIncident
    from sqlalchemy import func

    # Count by ai_triage_status
    status_counts = dict(
        db.query(SentryIncident.ai_triage_status, func.count(SentryIncident.id))
        .group_by(SentryIncident.ai_triage_status)
        .all()
    )

    # Count by incident status
    incident_status_counts = dict(
        db.query(SentryIncident.status, func.count(SentryIncident.id))
        .group_by(SentryIncident.status)
        .all()
    )

    # Total incidents
    total = db.query(func.count(SentryIncident.id)).scalar() or 0

    # Family count (distinct fingerprints)
    families = (
        db.query(func.count(func.distinct(SentryIncident.fingerprint)))
        .filter(SentryIncident.fingerprint.isnot(None))
        .scalar() or 0
    )

    # Count by source_type (email / sentry_webhook / manual)
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .group_by(SentryIncident.source_type)
        .all()
    )

    # Parse error count
    parse_errors = (
        db.query(func.count(SentryIncident.id))
        .filter(SentryIncident.status == "parse_error")
        .scalar() or 0
    )

    # F821 class fix (2026-05-19i): `linked` was referenced in the
    # return but NEVER computed → this /ops sentry-status summary
    # NameError-500'd (blinding the operator on the very endpoint that
    # reports incident health). Same query pattern as parse_errors;
    # "linked" is a documented SentryIncident.status value (connected
    # to a bugfix_candidate/ops_alert — see app/models/sentry_incident).
    linked = (
        db.query(func.count(SentryIncident.id))
        .filter(SentryIncident.status == "linked")
        .scalar() or 0
    )

    return {
        "total_incidents": total,
        "unique_families": families,
        "linked_to_candidates": linked,
        "by_triage_status": status_counts,
        "by_incident_status": incident_status_counts,
        "by_source_type": source_counts,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# Simulation — synthetic merchant observability
# ---------------------------------------------------------------------------

@router.post("/force-logout")
def force_logout(
    shop: str = Query(..., description="Shop domain to force-logout (session_version bump)"),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    from app.models.merchant import Merchant
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None:
        raise HTTPException(status_code=404, detail="merchant not found")
    previous_sv = m.session_version or 0
    m.session_version = previous_sv + 1
    db.commit()
    # Invalidate the auth-session cache so the next request re-reads
    # the new session_version (require_merchant_session caches for 30s).
    try:
        from app.core.redis_client import _client as _rc
        rc = _rc()
        if rc is not None:
            rc.delete(f"hs:auth:msv:v1:{shop}")
    except Exception:
        pass  # SILENT-EXCEPT-OK: cache invalidation best-effort; 30s stale window is the worst case
    log.warning(
        "ops.force_logout: shop=%s session_version %d → %d (all existing sessions invalidated)",
        shop, previous_sv, previous_sv + 1,
    )
    return {
        "shop_domain": shop,
        "previous_session_version": previous_sv,
        "new_session_version": previous_sv + 1,
        "effect": "all existing JWTs with sv < new_session_version will be rejected on next /merchant/me",
    }

