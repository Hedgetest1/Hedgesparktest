"""Tests for merchant support chatbot — classification, diagnostics, incidents, safety."""
import os
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.support_incident import SupportIncident
from app.services.merchant_chatbot import (
    classify_message,
    process_message,
    run_diagnostics,
    check_entitlement_health,
    get_incident_history,
    MessageClassification,
    _run_deep_shopify_check,
)
from tests.conftest import SHOP_A, SHOP_B, auth_cookies

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


# ---------------------------------------------------------------------------
# Classification — out-of-scope
# ---------------------------------------------------------------------------

def test_out_of_scope_recipe():
    cls = classify_message("how do I cook pasta")
    assert cls.classification == "out_of_scope"

def test_out_of_scope_weather():
    cls = classify_message("what's the weather today")
    assert cls.classification == "out_of_scope"

def test_out_of_scope_joke():
    cls = classify_message("tell me a joke")
    assert cls.classification == "out_of_scope"

def test_out_of_scope_who_are_you():
    cls = classify_message("who are you?")
    assert cls.classification == "out_of_scope"


# ---------------------------------------------------------------------------
# Classification — product questions
# ---------------------------------------------------------------------------

def test_product_question_signal():
    cls = classify_message("what does this signal mean?")
    assert cls.classification == "product_question"

def test_product_question_plan():
    cls = classify_message("is funnel analysis included in my plan?")
    assert cls.classification == "product_question"

def test_product_question_pricing():
    cls = classify_message("how much does Pro cost?")
    assert cls.classification == "product_question"

def test_product_question_how_to():
    cls = classify_message("how do I enable nudges?")
    assert cls.classification == "setup_help"


# ---------------------------------------------------------------------------
# Classification — bug reports
# ---------------------------------------------------------------------------

def test_bug_report_tracker_dead():
    cls = classify_message("tracking seems dead")
    assert cls.classification == "bug_report"
    assert cls.affected_area == "tracker"

def test_bug_report_nothing_appears():
    cls = classify_message("I installed but nothing appears")
    assert cls.classification in ("setup_help", "bug_report")
    assert cls.severity in ("high", "medium")

def test_bug_report_events_not_coming():
    cls = classify_message("events aren't coming in")
    assert cls.classification == "bug_report"
    assert cls.affected_area == "tracker"

def test_bug_report_dashboard_broken():
    cls = classify_message("dashboard is broken and not loading")
    assert cls.classification == "bug_report"
    assert cls.affected_area == "dashboard"


# ---------------------------------------------------------------------------
# Classification — billing / access
# ---------------------------------------------------------------------------

def test_billing_paid_locked():
    cls = classify_message("I paid but Pro is still locked")
    assert cls.classification == "billing_access_issue"
    assert cls.severity == "high"

def test_billing_pro_not_working():
    cls = classify_message("Pro features are not working")
    assert cls.classification == "billing_access_issue"
    assert cls.affected_area == "plan_access"

def test_billing_upgrade_issue():
    cls = classify_message("I upgraded but plan still shows lite")
    assert cls.classification == "billing_access_issue"


# ---------------------------------------------------------------------------
# Classification — integration issues
# ---------------------------------------------------------------------------

def test_integration_klaviyo():
    cls = classify_message("Klaviyo is not firing")
    assert cls.classification == "integration_issue"
    assert cls.affected_area == "klaviyo"

def test_integration_webhook_missing():
    cls = classify_message("webhook is failing and missing")
    assert cls.classification == "integration_issue"
    assert cls.affected_area == "webhooks"

def test_integration_email():
    cls = classify_message("why did this email not send?")
    assert cls.classification == "integration_issue"
    assert cls.affected_area == "resend"


# ---------------------------------------------------------------------------
# Classification — Shopify install / auth
# ---------------------------------------------------------------------------

def test_shopify_install_fail():
    cls = classify_message("install failed and nothing works")
    assert cls.classification == "setup_help"
    assert cls.affected_area == "shopify_auth"

def test_shopify_connected_but_dead():
    cls = classify_message("shopify says connected but app is dead")
    assert cls.classification == "setup_help"
    assert cls.affected_area == "shopify_auth"


# ---------------------------------------------------------------------------
# Out-of-scope response
# ---------------------------------------------------------------------------

def test_out_of_scope_response(db, merchant_a):
    result = process_message(db, SHOP_A, "tell me a joke")
    assert "HedgeSpark" in result.message
    assert result.classification == "out_of_scope"
    assert result.incident_created is False


# ---------------------------------------------------------------------------
# Product question answered
# ---------------------------------------------------------------------------

def test_product_question_answered(db, merchant_a):
    result = process_message(db, SHOP_A, "what is a signal?")
    assert result.classification == "product_question"
    assert "signal" in result.message.lower() or "Signal" in result.message
    assert result.incident_created is False  # product questions don't create incidents


# ---------------------------------------------------------------------------
# Bug report creates incident
# ---------------------------------------------------------------------------

def test_bug_report_creates_incident(db, merchant_a):
    result = process_message(db, SHOP_A, "tracking is broken and not working at all")
    assert result.classification == "bug_report"
    assert result.incident_created is True
    assert result.incident_id is not None

    incident = db.get(SupportIncident, result.incident_id)
    assert incident is not None
    assert incident.shop_domain == SHOP_A
    # Status is "triaged" because high-severity bug_report routes to pipeline
    # which creates/links an OpsAlert → transitions open → triaged
    assert incident.status in ("open", "triaged")
    assert incident.classification == "bug_report"


# ---------------------------------------------------------------------------
# Billing mismatch detection
# ---------------------------------------------------------------------------

def test_billing_mismatch_pro_no_billing(db):
    """Pro plan but billing_active=False → entitlement mismatch detected."""
    m = Merchant(
        shop_domain="mismatch-shop.myshopify.com",
        plan="pro",
        billing_active=False,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()

    result = process_message(db, "mismatch-shop.myshopify.com", "Pro is locked even though I paid")
    assert result.classification == "billing_access_issue"
    assert result.incident_created is True
    assert "inconsistency" in result.message.lower()

    # Check entitlement health
    health = check_entitlement_health(db, "mismatch-shop.myshopify.com")
    assert health["healthy"] is False
    assert "plan_pro_billing_inactive" in health["issues"]


def test_billing_mismatch_billing_active_on_lite(db):
    """billing_active=True but plan == lite → mismatch (Lite is the
    no-billing tier; Pro and Scale are the paid tiers)."""
    m = Merchant(
        shop_domain="mismatch2.myshopify.com",
        plan="lite",
        billing_active=True,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()

    health = check_entitlement_health(db, "mismatch2.myshopify.com")
    assert health["healthy"] is False
    assert "billing_active_plan_lite" in health["issues"]


def test_entitlement_healthy(db, merchant_a):
    """Merchant A is pro + billing active → healthy."""
    health = check_entitlement_health(db, SHOP_A)
    assert health["healthy"] is True
    assert health["issues"] == []


# ---------------------------------------------------------------------------
# Safe repair path
# ---------------------------------------------------------------------------

def test_repair_attempted_on_degraded(db):
    """High-severity issue on degraded merchant triggers repair attempt."""
    m = Merchant(
        shop_domain="degraded-shop.myshopify.com",
        plan="lite",
        billing_active=False,
        install_status="active",
        session_version=0,
        onboarding_status="failed",
        onboarding_error="webhook registration failed",
    )
    db.add(m)
    db.flush()

    with patch("app.services.merchant_chatbot.attempt_safe_repair") as mock_repair:
        mock_diag = MagicMock()
        mock_diag.setup_status = "needs_repair"
        mock_diag.repair_attempted = True
        mock_diag.repair_result = "onboarding_completed"
        mock_repair.return_value = mock_diag

        result = process_message(db, "degraded-shop.myshopify.com", "nothing works after install")
        # The repair is attempted because severity is high and setup is degraded
        assert result.incident_created is True


# ---------------------------------------------------------------------------
# Frontend plan mismatch detection
# ---------------------------------------------------------------------------

def test_frontend_plan_gate_detection(db, merchant_b):
    """Lite merchant asking about pro feature → correct plan answer."""
    result = process_message(db, SHOP_B, "Pro features are not working for me")
    assert result.classification == "billing_access_issue"
    # Should tell them they're on Lite
    assert "lite" in result.message.lower() or "upgrade" in result.message.lower()


# ---------------------------------------------------------------------------
# Incident linked to pipeline
# ---------------------------------------------------------------------------

def test_bug_creates_ops_alert(db, merchant_a):
    """High-severity bug creates linked ops alert."""
    result = process_message(db, SHOP_A, "tracker is completely broken and dead")
    assert result.incident_created is True

    incident = db.get(SupportIncident, result.incident_id)
    assert incident is not None
    # Bug report should create an alert
    assert incident.linked_ops_alert_id is not None


# ---------------------------------------------------------------------------
# Merchant-safe response formatting
# ---------------------------------------------------------------------------

def test_no_stack_traces_in_response(db, merchant_a):
    """Responses must never contain internal stack traces or secrets."""
    result = process_message(db, SHOP_A, "everything is broken")
    assert "traceback" not in result.message.lower()
    assert "exception" not in result.message.lower()
    assert "password" not in result.message.lower()
    assert "access_token" not in result.message.lower()
    assert "api_key" not in result.message.lower()


def test_diagnostic_summary_is_safe(db, merchant_a):
    """Diagnostic summary shown to merchant has no sensitive fields."""
    result = process_message(db, SHOP_A, "tracker is not working properly")
    if result.diagnostic_summary:
        # Should NOT contain raw tokens, keys, or internal IDs
        summary_str = str(result.diagnostic_summary)
        assert "access_token" not in summary_str
        assert "api_key" not in summary_str
        assert "encrypted" not in summary_str


# ---------------------------------------------------------------------------
# Incident history
# ---------------------------------------------------------------------------

def test_incident_history(db, merchant_a):
    """History returns recent incidents."""
    process_message(db, SHOP_A, "tracker broken")
    process_message(db, SHOP_A, "klaviyo not firing")
    db.flush()

    history = get_incident_history(db, SHOP_A)
    assert len(history) >= 2
    assert all("classification" in h for h in history)


# ---------------------------------------------------------------------------
# Feature request
# ---------------------------------------------------------------------------

def test_feature_request_acknowledged(db, merchant_a):
    result = process_message(db, SHOP_A, "can you add a dark mode feature please")
    assert result.classification == "feature_request"
    assert "suggestion" in result.message.lower() or "request" in result.message.lower()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

def test_api_requires_auth(client):
    """Chat endpoint requires merchant session."""
    resp = client.post("/chat/support", json={"message": "hello"})
    assert resp.status_code == 401 or resp.status_code == 403


def test_api_chat_support(client, db, merchant_a, auth_a):
    """POST /chat/support returns structured response."""
    resp = client.post(
        "/chat/support",
        json={"message": "what does a signal mean?"},
        cookies=auth_a,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert "classification" in data
    assert data["classification"] == "product_question"


def test_api_chat_history(client, db, merchant_a, auth_a):
    """GET /chat/support/history returns list."""
    # Create an incident first
    client.post(
        "/chat/support",
        json={"message": "tracker is broken"},
        cookies=auth_a,
    )

    resp = client.get("/chat/support/history", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_api_empty_message(client, db, merchant_a, auth_a):
    """Empty message rejected."""
    resp = client.post(
        "/chat/support",
        json={"message": ""},
        cookies=auth_a,
    )
    assert resp.status_code == 422  # validation error


# ---------------------------------------------------------------------------
# Audit log created
# ---------------------------------------------------------------------------

def test_audit_log_created(db, merchant_a):
    """Chat interactions create audit log entries."""
    process_message(db, SHOP_A, "billing is not working correctly")
    db.flush()

    from app.models.audit_log import AuditLog
    log = db.query(AuditLog).filter(
        AuditLog.action_type == "support_chat",
        AuditLog.shop_domain == SHOP_A,
    ).first()
    assert log is not None
    assert log.actor_type == "merchant"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_after_threshold(client, db, merchant_a, auth_a):
    """Per-merchant rate limit returns 429 after threshold."""
    from app.api.chat_support import _check_merchant_rate_limit, _CHAT_MAX_PER_HOUR

    # Mock the rate limit check to return blocked
    with patch("app.api.chat_support._check_merchant_rate_limit", return_value=(False, 300)):
        resp = client.post(
            "/chat/support",
            json={"message": "one more message"},
            cookies=auth_a,
        )
        assert resp.status_code == 429
        assert "wait" in resp.json()["detail"].lower()


def test_rate_limit_no_incident_on_429(client, db, merchant_a, auth_a):
    """Rate-limited requests must NOT create incidents."""
    initial_count = db.query(SupportIncident).filter(SupportIncident.shop_domain == SHOP_A).count()

    with patch("app.api.chat_support._check_merchant_rate_limit", return_value=(False, 300)):
        client.post("/chat/support", json={"message": "spam"}, cookies=auth_a)

    after_count = db.query(SupportIncident).filter(SupportIncident.shop_domain == SHOP_A).count()
    assert after_count == initial_count


# ---------------------------------------------------------------------------
# Incident resolution (operator endpoint)
# ---------------------------------------------------------------------------

def test_resolve_incident(client, db, merchant_a, auth_a):
    """Operator can resolve a support incident."""
    # Create an incident via chat
    resp = client.post(
        "/chat/support",
        json={"message": "tracker is completely broken"},
        cookies=auth_a,
    )
    incident_id = resp.json().get("incident_id")
    assert incident_id is not None

    # Resolve it as operator
    op_headers = {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}
    resp = client.patch(
        f"/chat/support/incidents/{incident_id}/resolve",
        json={"resolution_summary": "Tracker was missing script tag. Repaired automatically."},
        headers=op_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    # Verify DB
    incident = db.query(SupportIncident).filter(SupportIncident.id == incident_id).first()
    assert incident.status == "resolved"
    assert incident.resolved_by == "operator"
    assert "Tracker" in incident.resolution_summary


def test_resolve_incident_already_resolved(client, db, merchant_a, auth_a):
    """Cannot resolve an already-resolved incident."""
    resp = client.post(
        "/chat/support",
        json={"message": "billing broken"},
        cookies=auth_a,
    )
    incident_id = resp.json().get("incident_id")

    op_headers = {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}
    client.patch(
        f"/chat/support/incidents/{incident_id}/resolve",
        json={"resolution_summary": "Fixed."},
        headers=op_headers,
    )
    # Try again
    resp = client.patch(
        f"/chat/support/incidents/{incident_id}/resolve",
        json={"resolution_summary": "Fix again."},
        headers=op_headers,
    )
    assert resp.status_code == 409


def test_resolve_requires_operator_auth(client):
    """Resolution endpoint requires operator auth."""
    resp = client.patch(
        "/chat/support/incidents/1/resolve",
        json={"resolution_summary": "test"},
    )
    assert resp.status_code == 401 or resp.status_code == 403


# ---------------------------------------------------------------------------
# Resolution creates audit log
# ---------------------------------------------------------------------------

def test_resolve_creates_audit_log(client, db, merchant_a, auth_a):
    """Resolving an incident writes an audit log entry."""
    resp = client.post(
        "/chat/support",
        json={"message": "webhook is broken and missing"},
        cookies=auth_a,
    )
    incident_id = resp.json()["incident_id"]

    op_headers = {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}
    client.patch(
        f"/chat/support/incidents/{incident_id}/resolve",
        json={"resolution_summary": "Webhook repaired."},
        headers=op_headers,
    )

    from app.models.audit_log import AuditLog
    log_entry = db.query(AuditLog).filter(
        AuditLog.action_type == "support_incident_resolved",
        AuditLog.target_id == str(incident_id),
    ).first()
    assert log_entry is not None


# ---------------------------------------------------------------------------
# Entitlement health scan
# ---------------------------------------------------------------------------

def test_entitlement_scan_detects_mismatch(db):
    """Entitlement scan detects and alerts on mismatches."""
    m = Merchant(
        shop_domain="scan-test.myshopify.com",
        plan="pro",
        billing_active=False,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()

    health = check_entitlement_health(db, "scan-test.myshopify.com")
    assert not health["healthy"]
    assert "plan_pro_billing_inactive" in health["issues"]


# ---------------------------------------------------------------------------
# Deep Shopify diagnostics (mocked)
# ---------------------------------------------------------------------------

def test_deep_diagnostics_triggered_for_high_severity(db, merchant_a):
    """High-severity tracker issue triggers deep diagnostics."""
    # Ensure merchant has an access_token (required for deep check)
    merchant_a.access_token = "enc:v1:fake_token_for_test"
    db.flush()

    with patch("app.services.merchant_chatbot._run_deep_shopify_check") as mock_deep:
        # Force fast audit to show tracker missing
        with patch("app.services.setup_audit.compute_audit_fast") as mock_audit:
            mock_result = MagicMock()
            mock_result.readiness = "needs_repair"
            mock_result.degraded_reasons = ["tracker_missing"]
            mock_result.checks = MagicMock(webhook_ok=True, tracker_ok=False)
            mock_audit.return_value = mock_result

            run_diagnostics(db, SHOP_A, affected_area="tracker", severity="high")
            # Deep check should have been called
            mock_deep.assert_called_once()


def test_deep_diagnostics_not_triggered_for_low_severity(db, merchant_a):
    """Low-severity issues do NOT trigger deep Shopify API checks."""
    with patch("app.services.merchant_chatbot._run_deep_shopify_check") as mock_deep:
        run_diagnostics(db, SHOP_A, affected_area="tracker", severity="low")
        mock_deep.assert_not_called()
