"""
Tests for agent guardrails, auto-responder, feedback intelligence,
and merchant lifecycle orchestration.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Response guardrails
# ---------------------------------------------------------------------------

class TestResponseGuardrails:

    def test_blocks_dollar_amounts(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("Your plan costs $49/month")
        assert not result.safe
        assert any("dollar_amount" in v for v in result.violations)

    def test_blocks_refund_promises(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("We will give you a full refund immediately")
        assert not result.safe
        assert any("refund_promise" in v for v in result.violations)

    def test_blocks_timeline_guarantees(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("This will be fixed by tomorrow morning")
        assert not result.safe
        assert any("timeline_guarantee" in v for v in result.violations)

    def test_blocks_legal_claims(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("We are legally bound to process your data")
        assert not result.safe
        assert any("legal_obligation_claim" in v for v in result.violations)

    def test_blocks_compliance_claims(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("Our system is 100% GDPR-compliant")
        assert not result.safe
        assert any("compliance_claim" in v for v in result.violations)

    def test_blocks_condescending_tone(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("Obviously you should have read the docs first")
        assert not result.safe
        assert any("condescending_tone" in v for v in result.violations)

    def test_allows_safe_response(self):
        from app.services.response_guardrails import validate_response
        result = validate_response(
            "Thanks for reaching out! Open your dashboard to get started. "
            "If you need help, reply and a human will assist you."
        )
        assert result.safe
        assert result.violations == []

    def test_blocks_long_auto_response(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("x" * 501, context="auto_response")
        assert not result.safe
        assert "auto_response_too_long" in result.violations

    def test_escalation_flag_on_complaint(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("We're looking into this", classification="complaint")
        assert result.must_escalate

    def test_escalation_flag_on_billing(self):
        from app.services.response_guardrails import validate_response
        result = validate_response("Checking your account", classification="billing_or_legal")
        assert result.must_escalate

    def test_hedge_timeline(self):
        from app.services.response_guardrails import hedge_timeline
        result = hedge_timeline("First insights appear in about 10 minutes")
        assert "typically" in result.lower()

    def test_add_billing_disclaimer(self):
        from app.services.response_guardrails import add_disclaimer
        result = add_disclaimer("Here's your account info", "billing_or_legal")
        assert "Shopify admin panel" in result


# ---------------------------------------------------------------------------
# Auto-responder
# ---------------------------------------------------------------------------

class TestAutoResponder:

    def test_drafts_onboarding_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="test@test.com",
            classification="onboarding_confusion",
            routing_status="routed",
        )
        response = draft_response(email)
        assert response is not None
        assert "dashboard" in response.lower()
        assert len(response) <= 500

    def test_drafts_praise_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="happy@test.com",
            classification="praise",
            routing_status="routed",
        )
        response = draft_response(email)
        assert response is not None
        assert "thank" in response.lower()

    def test_refuses_complaint_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="angry@test.com",
            classification="complaint",
            routing_status="escalated",
        )
        response = draft_response(email)
        assert response is None

    def test_refuses_billing_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="billing@test.com",
            classification="billing_or_legal",
            routing_status="routed",
        )
        response = draft_response(email)
        assert response is None

    def test_refuses_bug_report_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="dev@test.com",
            classification="bug_report",
            routing_status="routed",
        )
        response = draft_response(email)
        assert response is None

    def test_no_duplicate_response(self):
        from app.models.inbound_email import InboundEmail
        from app.services.auto_responder import draft_response

        email = InboundEmail(
            from_email="test@test.com",
            classification="onboarding_confusion",
            routing_status="responded",
            agent_response_sent_at=_now(),
        )
        response = draft_response(email)
        assert response is None

    def test_all_templates_pass_guardrails(self):
        """Every auto-response template must pass guardrail validation."""
        from app.services.auto_responder import _TEMPLATES
        from app.services.response_guardrails import validate_response

        for classification, template in _TEMPLATES.items():
            result = validate_response(template, context="auto_response", classification=classification)
            assert result.safe, f"Template for {classification} failed: {result.violations}"


# ---------------------------------------------------------------------------
# Feedback intelligence
# ---------------------------------------------------------------------------

class TestFeedbackIntelligence:

    def test_themes_group_by_area(self, db):
        from app.models.inbound_email import InboundEmail
        from app.services.feedback_intelligence import compute_feedback_themes

        # Create feedback about nudges
        for i in range(3):
            db.add(InboundEmail(
                message_id=f"fb-nudge-{i}",
                from_email=f"m{i}@test.com",
                shop_domain=f"shop{i}.myshopify.com",
                subject="Can you add more nudge templates?",
                body_text="I want more popup notification options for my store",
                classification="feature_request",
                routing_status="executed",
            ))
        db.flush()

        themes = compute_feedback_themes(db)
        assert len(themes) >= 1
        nudge_theme = next((t for t in themes if t["area"] == "nudges"), None)
        assert nudge_theme is not None
        assert nudge_theme["count"] >= 3
        assert nudge_theme["unique_shops"] >= 3

    def test_summary_includes_breakdown(self, db):
        from app.models.inbound_email import InboundEmail
        from app.services.feedback_intelligence import get_feedback_summary

        db.add(InboundEmail(
            message_id="fb-sum-1",
            from_email="m@test.com",
            shop_domain="sumtest.myshopify.com",
            subject="Export feature request",
            body_text="Can you add CSV export for segments?",
            classification="feature_request",
            routing_status="executed",
        ))
        db.flush()

        summary = get_feedback_summary(db)
        assert summary["total_feedback_30d"] >= 1
        assert "themes" in summary
        assert "classification_breakdown" in summary

    def test_empty_feedback_returns_clean(self, db):
        from app.services.feedback_intelligence import get_feedback_summary
        summary = get_feedback_summary(db)
        assert summary["total_feedback_30d"] >= 0
        assert isinstance(summary["themes"], list)


# ---------------------------------------------------------------------------
# Lifecycle state — suppression stage
# ---------------------------------------------------------------------------

class TestLifecycleStage:

    def test_suppressed_stage_overrides_all(self):
        from app.models.merchant_journey_state import MerchantJourneyState
        from app.services.email_journey import _recompute_stage

        journey = MerchantJourneyState(
            shop_domain="test.myshopify.com",
            beta_invite_sent_at=_now(),
            beta_invite_opened_at=_now(),
            onboarding_completed_at=_now(),
            inbound_reply_received_at=_now(),
            email_suppressed="complained",
        )
        assert _recompute_stage(journey) == "suppressed"

    def test_normal_progression(self):
        from app.models.merchant_journey_state import MerchantJourneyState
        from app.services.email_journey import _recompute_stage

        journey = MerchantJourneyState(
            shop_domain="test.myshopify.com",
            beta_invite_sent_at=_now(),
            beta_invite_opened_at=_now(),
        )
        assert _recompute_stage(journey) == "opened"

    def test_active_after_onboarding(self):
        from app.models.merchant_journey_state import MerchantJourneyState
        from app.services.email_journey import _recompute_stage

        journey = MerchantJourneyState(
            shop_domain="test.myshopify.com",
            beta_invite_sent_at=_now(),
            onboarding_started_at=_now(),
            onboarding_completed_at=_now(),
        )
        assert _recompute_stage(journey) == "active"


# ---------------------------------------------------------------------------
# Ops endpoints
# ---------------------------------------------------------------------------

class TestOpsEndpoints:

    def test_feedback_themes_endpoint(self, client):
        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get("/ops/feedback/themes", headers={"X-API-Key": _key})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_feedback_30d" in data
        assert "themes" in data

    def test_merchant_profile_endpoint(self, client, db):
        from app.models.merchant import Merchant
        m = Merchant(shop_domain="profile-test.myshopify.com", contact_email="p@test.com")
        db.add(m)
        db.flush()

        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get(
            "/ops/merchant/profile-test.myshopify.com/profile",
            headers={"X-API-Key": _key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["merchant"]["shop_domain"] == "profile-test.myshopify.com"
        assert "risk_signals" in data
        assert "journey" in data
        assert "emails_sent" in data
        assert "inbound_emails" in data

    def test_merchant_profile_unknown(self, client):
        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get(
            "/ops/merchant/nonexistent.myshopify.com/profile",
            headers={"X-API-Key": _key},
        )
        # Pre-2026-05-08 returned 200 with `{"error": "merchant_not_found"}`
        # in the body — REST drift the audit flagged. Now correctly 404.
        assert resp.status_code == 404
        assert resp.json()["detail"] == "merchant_not_found"
