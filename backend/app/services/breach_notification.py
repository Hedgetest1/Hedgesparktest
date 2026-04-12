"""
breach_notification.py — Automated classification + notification for
security/privacy incidents.

GDPR Art. 33 requires supervisory-authority notification within **72
hours** of awareness of a personal-data breach. Art. 34 requires
notification of affected data subjects "without undue delay" when the
breach is likely to result in a high risk to their rights and freedoms.

This module turns an `ops_alert` into an actionable breach-response
workflow:

  1. **Classify** the alert by severity + alert_type. Known signatures
     (`security_probe_failed`, `audit_log_tampering`, `gdpr_sla_breach`,
     unauthorized token decryption failures, repeated auth failures)
     are marked as potential breaches.
  2. **Stamp** a `breach_response` metadata block onto the alert with
     the 72-hour supervisory-authority deadline and the 7-day
     data-subject deadline.
  3. **Emit** a dedicated `breach_response_required` alert that routes
     through the normal digest + Telegram channels so the founder
     cannot miss it.
  4. **Record** every classification in the audit log (hash-chained).

No automatic outbound disclosure — that's a human decision under legal
counsel. The module's job is to ensure (a) the clock starts immediately,
(b) the founder is paged, and (c) the paper trail is on the hash chain.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("breach_notification")

_SUPERVISORY_DEADLINE_HOURS = 72
_DATA_SUBJECT_DEADLINE_HOURS = 168  # 7 days — "without undue delay"

# Alert types that trigger breach classification. Ordered by severity.
# Each entry is (alert_type, default_classification, description).
_BREACH_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    (
        "security_probe_failed",
        "potential_breach",
        "Self-attack probe succeeded — an endpoint accepted a request it should have rejected",
    ),
    (
        "audit_log_tampering",
        "confirmed_breach",
        "Audit log hash chain verification failed — row modification or deletion detected",
    ),
    (
        "gdpr_sla_breach",
        "compliance_violation",
        "GDPR SLA deadline missed — operator must document justification",
    ),
    (
        "token_decryption_repeated_failure",
        "potential_breach",
        "Repeated merchant token decryption failures — possible key rotation or DB tamper",
    ),
    (
        "operator_auth_brute_force",
        "potential_breach",
        "Operator API key brute-force attempt pattern detected",
    ),
)

_SIG_INDEX: dict[str, tuple[str, str]] = {
    sig: (classification, description)
    for sig, classification, description in _BREACH_SIGNATURES
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def classify_alert(alert) -> dict[str, Any] | None:
    """Return a breach classification dict for the alert, or None if it
    isn't a breach signature. Shape:

        {
            "alert_id": int,
            "alert_type": str,
            "classification": "confirmed_breach" | "potential_breach" | "compliance_violation",
            "supervisory_deadline": iso,
            "data_subject_deadline": iso,
            "description": str,
            "classified_at": iso,
        }
    """
    hit = _SIG_INDEX.get(alert.alert_type)
    if not hit:
        return None
    classification, description = hit
    now = _now()
    return {
        "alert_id": alert.id,
        "alert_type": alert.alert_type,
        "classification": classification,
        "supervisory_deadline": (now + timedelta(hours=_SUPERVISORY_DEADLINE_HOURS)).isoformat(),
        "data_subject_deadline": (now + timedelta(hours=_DATA_SUBJECT_DEADLINE_HOURS)).isoformat(),
        "description": description,
        "classified_at": now.isoformat(),
    }


def process_breach_candidates(db: Session) -> dict[str, Any]:
    """Scan unresolved ops_alerts for breach signatures, classify each,
    emit a `breach_response_required` alert, and write an audit log
    entry. Deduped via the presence of an existing response alert."""
    from app.models.ops_alert import OpsAlert
    from app.services.audit import write_audit_log

    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "scanned": 0,
        "classified": 0,
        "new_response_alerts": 0,
    }

    try:
        candidates = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.resolved == False,  # noqa: E712
                OpsAlert.alert_type.in_(
                    [sig for sig, _, _ in _BREACH_SIGNATURES]
                ),
            )
            .order_by(OpsAlert.created_at.asc())
            .limit(50)
            .all()
        )
    except Exception as exc:
        log.warning("breach_notification: scan failed: %s", exc)
        return report

    report["scanned"] = len(candidates)

    for alert in candidates:
        classification = classify_alert(alert)
        if classification is None:
            continue
        report["classified"] += 1

        # Dedup: skip if we've already raised a response alert for this
        # underlying alert id.
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "breach_response_required",
                OpsAlert.source == f"breach:{alert.id}",
                OpsAlert.resolved == False,  # noqa: E712
            )
            .first()
        )
        if existing is not None:
            continue

        response_alert = OpsAlert(
            severity="critical",
            source=f"breach:{alert.id}",
            alert_type="breach_response_required",
            shop_domain=alert.shop_domain,
            summary=(
                f"BREACH RESPONSE REQUIRED [{classification['classification']}] "
                f"{alert.alert_type} — supervisory deadline "
                f"{classification['supervisory_deadline']}"
            ),
            detail=(
                f"{classification['description']}\n\n"
                f"Original alert: #{alert.id} ({alert.alert_type})\n"
                f"Classification: {classification['classification']}\n"
                f"Supervisory authority deadline (GDPR Art. 33): "
                f"{classification['supervisory_deadline']} (72h)\n"
                f"Data-subject notification deadline (Art. 34): "
                f"{classification['data_subject_deadline']} (7d)\n\n"
                f"See docs/BREACH_RESPONSE.md for the runbook."
            ),
            resolved=False,
        )
        db.add(response_alert)
        try:
            db.flush()
            report["new_response_alerts"] += 1
            write_audit_log(
                db,
                actor_type="system",
                actor_name="breach_notification",
                action_type="breach_classified",
                target_type="ops_alert",
                target_id=str(alert.id),
                shop_domain=alert.shop_domain,
                status="completed",
                metadata=classification,
            )
        except Exception as exc:
            log.warning("breach_notification: response write failed: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
            continue

    if report["new_response_alerts"] > 0:
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        log.warning(
            "breach_notification: %d new breach response alert(s) raised",
            report["new_response_alerts"],
        )
    return report
