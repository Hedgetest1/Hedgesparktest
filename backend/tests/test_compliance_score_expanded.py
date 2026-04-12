"""Tests for the expanded compliance score with 11 components."""
import pytest
from unittest.mock import patch, MagicMock


def test_compliance_score_has_11_components():
    """Compliance score must have 11 weighted components summing to 100."""
    from app.services.compliance_score import _WEIGHTS
    assert len(_WEIGHTS) == 11
    assert sum(_WEIGHTS.values()) == 100


def test_compliance_score_new_component_names():
    """New components must be present in the weight map."""
    from app.services.compliance_score import _WEIGHTS
    new_components = [
        "audit_log_integrity",
        "breach_response_latency",
        "llm_pii_guard_health",
        "telegram_webhook_security",
    ]
    for name in new_components:
        assert name in _WEIGHTS, f"Missing component: {name}"


def test_audit_log_integrity_full_score():
    """audit_log_integrity gives full score when chain verified and no tampering."""
    from app.services.compliance_score import _score_audit_log_integrity, _WEIGHTS
    mock_rc = MagicMock()
    mock_rc.get.side_effect = lambda key: (
        b"1" if "audit_log_check" in key else None
    )
    with patch("app.services.compliance_score._redis", return_value=mock_rc):
        result = _score_audit_log_integrity()
    assert result["score"] == _WEIGHTS["audit_log_integrity"]
    assert "verified" in result["detail"]


def test_audit_log_integrity_zero_when_no_recent_check():
    """audit_log_integrity gives zero when no check ran in 48h."""
    from app.services.compliance_score import _score_audit_log_integrity
    mock_rc = MagicMock()
    mock_rc.get.return_value = None
    with patch("app.services.compliance_score._redis", return_value=mock_rc):
        result = _score_audit_log_integrity()
    assert result["score"] == 0


def test_audit_log_integrity_zero_on_tampering():
    """audit_log_integrity gives zero when tampering is detected."""
    from app.services.compliance_score import _score_audit_log_integrity, _WEIGHTS
    mock_rc = MagicMock()
    def _get(key):
        if "audit_log_check" in key:
            return b"1"
        if "tampering" in key:
            return b"1"
        return None
    mock_rc.get.side_effect = _get
    with patch("app.services.compliance_score._redis", return_value=mock_rc):
        result = _score_audit_log_integrity()
    assert result["score"] == 0
    assert "tampering" in result["detail"].lower()


def test_breach_response_latency_full_when_no_alerts(db):
    """breach_response_latency gives full score when no open alerts."""
    from app.services.compliance_score import _score_breach_response_latency, _WEIGHTS
    result = _score_breach_response_latency(db)
    assert result["score"] == _WEIGHTS["breach_response_latency"]


def test_breach_response_latency_zero_when_overdue(db):
    """breach_response_latency gives zero when breach alert > 72h old."""
    from app.services.compliance_score import _score_breach_response_latency
    from app.models.ops_alert import OpsAlert
    from datetime import datetime, timedelta, timezone
    old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=80)
    alert = OpsAlert(
        severity="critical",
        source="breach:test",
        alert_type="breach_response_required",
        summary="test breach",
        resolved=False,
        created_at=old_time,
    )
    db.add(alert)
    db.flush()
    result = _score_breach_response_latency(db)
    assert result["score"] == 0


def test_llm_pii_guard_health_passes():
    """LLM PII guard health check should pass — module is importable and
    correctly distinguishes clean from dirty text."""
    from app.services.compliance_score import _score_llm_pii_guard_health, _WEIGHTS
    result = _score_llm_pii_guard_health()
    assert result["score"] == _WEIGHTS["llm_pii_guard_health"], f"PII guard failed: {result['detail']}"
    assert "operational" in result["detail"]


def test_telegram_webhook_security_present():
    """Telegram webhook security should at least give partial score."""
    from app.services.compliance_score import _score_telegram_webhook_security
    result = _score_telegram_webhook_security()
    # Should give at least partial credit (module exists even if secret not set)
    assert result["score"] > 0


def test_compute_compliance_score_includes_new_components(db):
    """Full score computation must include all 11 components."""
    from app.services.compliance_score import compute_compliance_score
    with patch("app.services.compliance_score._redis", return_value=None):
        result = compute_compliance_score(db)
    assert "audit_log_integrity" in result["components"]
    assert "breach_response_latency" in result["components"]
    assert "llm_pii_guard_health" in result["components"]
    assert "telegram_webhook_security" in result["components"]
    # Score should still be between 0 and 100
    assert 0 <= result["score"] <= 100
    assert result["grade"] in ("A+", "A", "B", "C", "D", "F")
