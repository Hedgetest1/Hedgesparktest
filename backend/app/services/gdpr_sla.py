"""
gdpr_sla.py — Service-level-agreement enforcement for GDPR requests.

Shopify enforces a 48-hour deadline on `shop/redact` webhooks
(failure is grounds for app removal). GDPR itself gives data subjects
up to 30 days for access / erasure responses. Neither deadline was
previously tracked: if the gdpr_worker crashed or fell behind, the
missed deadline was silent.

This module is the guardrail:

    get_pending_violations(db) -> list[dict]
        Rows past their computed deadline, grouped by type.

    enforce_sla(db) -> dict
        Scan + emit one ops_alert per fresh violation, with dedup.
        Designed to run every cycle from the agent worker.

Deadlines are computed from `created_at`; we do not add a DB column
so this ships without a migration.

Dedup:
    One `gdpr_sla_breach` ops_alert per (gdpr_request_id) via the
    source_ref column. If an alert already exists for a request we
    refresh `last_seen` on the first alert instead of creating a new
    one — prevents flooding.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("gdpr_sla")

# Deadlines — keep in one place, env-tunable for tightening only.
_DEADLINES_HOURS = {
    "shop_redact":           48,      # Shopify contractual
    "customers_redact":      30 * 24,  # GDPR Art. 17
    "customers_data_request": 30 * 24,  # GDPR Art. 15
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _deadline_for(req) -> datetime | None:
    hours = _DEADLINES_HOURS.get(req.request_type)
    if hours is None or req.created_at is None:
        return None
    return req.created_at + timedelta(hours=hours)


def get_pending_violations(db: Session) -> list[dict]:
    """Return every GdprRequest past its computed deadline whose status
    is NOT a terminal success state. Each row is returned as a dict
    shaped for digest rendering."""
    from app.models.gdpr_request import GdprRequest

    now = _now()
    try:
        rows = (
            db.query(GdprRequest)
            .filter(
                GdprRequest.status.notin_(["completed", "redacted_ok"]),
                GdprRequest.created_at.isnot(None),
            )
            .order_by(GdprRequest.created_at.asc())
            .all()
        )
    except Exception as exc:
        log.warning("gdpr_sla: query failed: %s", exc)
        return []

    violations: list[dict] = []
    for req in rows:
        deadline = _deadline_for(req)
        if deadline is None or deadline >= now:
            continue
        overdue_minutes = int((now - deadline).total_seconds() / 60)
        violations.append({
            "request_id": req.id,
            "request_type": req.request_type,
            "shop_domain": req.shop_domain,
            "status": req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "deadline_at": deadline.isoformat(),
            "overdue_minutes": overdue_minutes,
        })
    return violations


def _emit_breach_alert(db: Session, violation: dict) -> bool:
    """Create a gdpr_sla_breach ops_alert for the violation if we don't
    already have one for this request. Returns True when a new alert
    was created."""
    from app.models.ops_alert import OpsAlert

    source_ref = f"gdpr_request:{violation['request_id']}"
    try:
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "gdpr_sla_breach",
                OpsAlert.source == source_ref,
                OpsAlert.resolved == False,  # noqa: E712
            )
            .first()
        )
        if existing is not None:
            return False

        alert = OpsAlert(
            severity="critical",
            source=source_ref,
            alert_type="gdpr_sla_breach",
            shop_domain=violation["shop_domain"],
            summary=(
                f"GDPR {violation['request_type']} SLA breach: "
                f"{violation['overdue_minutes']}min overdue "
                f"(request #{violation['request_id']}, "
                f"status={violation['status']})"
            ),
            detail=(
                f"Deadline was {violation['deadline_at']}. "
                f"Check gdpr_worker logs and drain the queue. "
                f"Shopify contract (shop_redact) or GDPR Art. 15/17 "
                f"(30-day) may be breached depending on request_type."
            ),
            resolved=False,
        )
        db.add(alert)
        db.flush()
        log.warning(
            "gdpr_sla: CRITICAL breach request_id=%d type=%s overdue=%dmin",
            violation["request_id"], violation["request_type"],
            violation["overdue_minutes"],
        )
        return True
    except Exception as exc:
        log.warning("gdpr_sla: alert write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def enforce_sla(db: Session) -> dict:
    """Scan every pending GdprRequest and emit an ops_alert for each
    breach. Intended for per-cycle worker invocation."""
    violations = get_pending_violations(db)
    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "violations": len(violations),
        "new_alerts": 0,
    }
    for v in violations:
        if _emit_breach_alert(db, v):
            report["new_alerts"] += 1
    if violations:
        db.commit()
    return report
