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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ops_alert import OpsAlert

log = logging.getLogger(__name__)


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

    DB persist happens FIRST — the alert exists regardless of delivery outcome.
    External delivery is attempted SECOND — failure is logged but never raised.
    """
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
        except Exception:
            pass
        log.debug("alert: external delivery error (non-fatal): %s", exc)

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
    alert = db.query(OpsAlert).get(alert_id)
    if alert and not alert.resolved:
        alert.resolved = True
        alert.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()
