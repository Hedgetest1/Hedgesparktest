"""Tests for breach_notification — GDPR Art. 33 clock automation."""
from __future__ import annotations

import uuid

from app.models.audit_log import AuditLog
from app.models.ops_alert import OpsAlert
from app.services.breach_notification import (
    _BREACH_SIGNATURES,
    classify_alert,
    process_breach_candidates,
)


def _make_alert(db, alert_type: str, severity: str = "critical") -> OpsAlert:
    a = OpsAlert(
        severity=severity,
        source=f"test-{uuid.uuid4().hex[:8]}",
        alert_type=alert_type,
        shop_domain=None,
        summary=f"synthetic {alert_type}",
        resolved=False,
    )
    db.add(a)
    db.flush()
    return a


def test_every_signature_classifies():
    for alert_type, expected_classification, _ in _BREACH_SIGNATURES:
        class _Fake:
            pass
        fake = _Fake()
        fake.id = 1
        fake.alert_type = alert_type
        result = classify_alert(fake)
        assert result is not None
        assert result["classification"] == expected_classification
        assert "supervisory_deadline" in result
        assert "data_subject_deadline" in result


def test_unknown_alert_type_returns_none():
    class _Fake:
        id = 1
        alert_type = "harmless_info_alert"
    assert classify_alert(_Fake()) is None


def test_process_creates_response_alert(db):
    a = _make_alert(db, "security_probe_failed")
    before = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "breach_response_required",
    ).count()
    report = process_breach_candidates(db)
    after = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "breach_response_required",
    ).count()
    assert report["new_response_alerts"] >= 1
    assert after >= before + 1

    # The response alert references the source alert
    response = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "breach_response_required",
            OpsAlert.source == f"breach:{a.id}",
        )
        .first()
    )
    assert response is not None
    assert response.severity == "critical"
    assert "supervisory deadline" in (response.summary or "").lower()


def test_process_writes_audit_log(db):
    a = _make_alert(db, "audit_log_tampering")
    before = db.query(AuditLog).filter(
        AuditLog.action_type == "breach_classified",
    ).count()
    process_breach_candidates(db)
    after = db.query(AuditLog).filter(
        AuditLog.action_type == "breach_classified",
    ).count()
    assert after >= before + 1


def test_process_is_idempotent(db):
    _make_alert(db, "security_probe_failed")
    first = process_breach_candidates(db)
    second = process_breach_candidates(db)
    # Second run must not create a duplicate response alert
    assert second["new_response_alerts"] == 0


def test_process_ignores_resolved_source_alerts(db):
    a = _make_alert(db, "security_probe_failed")
    a.resolved = True
    db.flush()
    # No response alert should be created for this specific resolved source
    before = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "breach_response_required",
        OpsAlert.source == f"breach:{a.id}",
    ).count()
    process_breach_candidates(db)
    after = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "breach_response_required",
        OpsAlert.source == f"breach:{a.id}",
    ).count()
    assert after == before


def test_compliance_violation_for_sla_breach():
    class _Fake:
        id = 99
        alert_type = "gdpr_sla_breach"
    result = classify_alert(_Fake())
    assert result["classification"] == "compliance_violation"
