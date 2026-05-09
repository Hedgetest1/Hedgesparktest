"""
system_diagnostic.py — Unified operational diagnostic for AI agents and operators.

Single function that answers: "What is the state of EVERYTHING right now?"
Aggregates signals from all subsystems into one structured response.

This is the highest-leverage observability endpoint: an AI agent or operator
can call GET /ops/diagnostic ONCE and understand the full system state
without querying 6+ separate endpoints.

Public interface:
    build_system_diagnostic(db) -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("system_diagnostic")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_system_diagnostic(db: Session) -> dict:
    """
    Build a comprehensive system diagnostic. Each section is independently
    resilient — one failure doesn't block the others.
    """
    diag: dict = {
        "generated_at": _now().isoformat() + "Z",
        "overall_status": "ok",
        "issues": [],
    }

    issues = diag["issues"]

    # --- 1. System vitals ---
    try:
        from app.services.system_summary import build_system_summary
        s = build_system_summary(db)
        ram_pct = s.get("infra", {}).get("ram", {}).get("usage_pct", 0)
        error_rate = s.get("infra", {}).get("workers", {}).get("error_rate_pct", 0)
        diag["vitals"] = {
            "ram_pct": ram_pct,
            "worker_error_rate_pct": error_rate,
            "worker_cycles_24h": s.get("infra", {}).get("workers", {}).get("cycles_24h", 0),
            "warnings": s.get("warnings", []),
        }
        if error_rate > 20:
            issues.append({"severity": "critical", "area": "workers", "detail": f"Error rate {error_rate}%"})
        elif error_rate > 10:
            issues.append({"severity": "warning", "area": "workers", "detail": f"Error rate {error_rate}%"})
        if ram_pct > 85:
            issues.append({"severity": "warning", "area": "infrastructure", "detail": f"RAM at {ram_pct}%"})
    except Exception as exc:
        diag["vitals"] = {"error": str(exc)[:100]}
        issues.append({"severity": "warning", "area": "vitals", "detail": f"Unavailable: {exc}"})

    # --- 2. LLM budget ---
    try:
        from app.core.llm_budget import MONTHLY_EUR_CAP, get_usage_summary
        budget = get_usage_summary()
        diag["llm_budget"] = {
            "monthly_cost_eur": budget.get("monthly_cost_eur", 0),
            "monthly_cap_eur": budget.get("monthly_cap_eur", MONTHLY_EUR_CAP),
            "cap_reached": budget.get("monthly_cap_reached", False),
            "blocked_today": budget.get("blocked_today", 0),
            "provider_429s": {k: v.get("total_429s", 0) for k, v in budget.get("provider_429_state", {}).items()},
        }
        if budget.get("monthly_cap_reached"):
            issues.append({"severity": "warning", "area": "llm", "detail": "Monthly budget cap reached"})
    except Exception:
        diag["llm_budget"] = {"error": "unavailable"}

    # --- 3. Attribution pipeline ---
    try:
        orders_total = db.execute(text("SELECT COUNT(*) FROM shop_orders")).fetchone()[0]
        vps_total = db.execute(text("SELECT COUNT(*) FROM visitor_purchase_sessions")).fetchone()[0]
        vps_recent = db.execute(text(
            "SELECT COUNT(*) FROM visitor_purchase_sessions WHERE confirmed_at >= NOW() - INTERVAL '24 hours'"
        )).fetchone()[0]
        orders_recent = db.execute(text(
            "SELECT COUNT(*) FROM shop_orders WHERE created_at >= NOW() - INTERVAL '24 hours'"
        )).fetchone()[0]
        diag["attribution"] = {
            "orders_total": orders_total,
            "bridges_total": vps_total,
            "orders_24h": orders_recent,
            "bridges_24h": vps_recent,
            "bridge_rate": round(vps_total / max(orders_total, 1), 3),
            "status": "healthy" if vps_total > 0 else ("no_bridges" if orders_total > 0 else "no_data"),
        }
        if orders_total > 5 and vps_total == 0:
            issues.append({"severity": "warning", "area": "attribution", "detail": f"{orders_total} orders but 0 bridges"})
    except Exception:
        diag["attribution"] = {"error": "unavailable"}

    # --- 4. Alerts + incidents ---
    try:
        alerts = db.execute(text("SELECT COUNT(*) FROM ops_alerts WHERE resolved = false")).fetchone()[0]
        incidents = db.execute(text(
            "SELECT COUNT(*) FROM support_incidents WHERE status IN ('open', 'triaged', 'investigating')"
        )).fetchone()[0]
        diag["alerts_incidents"] = {"active_alerts": alerts, "active_incidents": incidents}
        if alerts > 10:
            issues.append({"severity": "warning", "area": "alerts", "detail": f"{alerts} unresolved alerts"})
    except Exception:
        diag["alerts_incidents"] = {"error": "unavailable"}

    # --- 5. Onboarding funnel ---
    try:
        funnel = db.execute(text("""
            SELECT onboarding_status, COUNT(*) FROM merchants
            WHERE install_status = 'active'
            GROUP BY onboarding_status
        """)).fetchall()
        diag["onboarding"] = {r[0]: r[1] for r in funnel}
        stuck = sum(r[1] for r in funnel if r[0] in ("pending", "failed"))
        if stuck > 0:
            issues.append({"severity": "info", "area": "onboarding", "detail": f"{stuck} merchants not fully onboarded"})
    except Exception:
        diag["onboarding"] = {"error": "unavailable"}

    # --- 6. Webhook fleet ---
    try:
        from app.services.webhook_monitor import get_fleet_webhook_summary
        wh = get_fleet_webhook_summary(db)
        diag["webhooks"] = wh.get("by_severity", {})
        broken = wh.get("by_severity", {}).get("broken", 0)
        unreachable = wh.get("by_severity", {}).get("unreachable", 0)
        if broken > 0:
            issues.append({"severity": "warning", "area": "webhooks", "detail": f"{broken} merchants with broken webhooks"})
        if unreachable > 0:
            issues.append({"severity": "info", "area": "webhooks", "detail": f"{unreachable} merchants unreachable"})
    except Exception:
        diag["webhooks"] = {"error": "unavailable"}

    # --- 7. Merchant data health ---
    try:
        merchants = db.execute(text("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE billing_active = true),
                   COUNT(*) FILTER (WHERE contact_email IS NOT NULL AND contact_email != '')
            FROM merchants WHERE install_status = 'active'
        """)).fetchone()
        events_24h = db.execute(text(
            "SELECT COUNT(*) FROM events WHERE timestamp > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1000"
        )).fetchone()[0]
        diag["merchants"] = {
            "active": merchants[0],
            "billing_active": merchants[1],
            "with_email": merchants[2],
            "events_24h": events_24h,
        }
        if merchants[0] > 0 and events_24h == 0:
            issues.append({"severity": "warning", "area": "tracking", "detail": "Active merchants but 0 events in 24h"})
    except Exception:
        diag["merchants"] = {"error": "unavailable"}

    # --- Classify overall status ---
    severities = [i["severity"] for i in issues]
    if "critical" in severities:
        diag["overall_status"] = "critical"
    elif "warning" in severities:
        diag["overall_status"] = "degraded"
    else:
        diag["overall_status"] = "ok"

    return diag
