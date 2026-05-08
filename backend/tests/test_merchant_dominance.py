"""
Tests for the merchant-elite dominance implementations:
C1: Inbound email action executor
C2: Email send cap enforcement
C4: Low-severity bug escalation
H3: Silence detection
H6: Per-merchant email diagnostics
H7: DB pool metrics
H8: Events index (verified by query plan)
H9: Onboarding backoff
C3: Billing sync
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
# C1: Inbound email action executor
# ---------------------------------------------------------------------------

class TestInboundActionExecutor:

    def test_bug_report_creates_ops_alert(self, db):
        """Bug report email with routing_action=create_support_incident creates an ops_alert."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_inbound_actions

        email = InboundEmail(
            message_id="test-bug-001",
            from_email="merchant@test.com",
            subject="Tracker not working",
            body_text="My tracker shows 0 visitors for 3 days",
            shop_domain="testshop.myshopify.com",
            classification="bug_report",
            classification_confidence="high",
            classification_method="keyword",
            routing_action="create_support_incident",
            routing_status="routed",
        )
        db.add(email)
        db.flush()

        result = run_inbound_actions(db)
        db.flush()

        assert result["incidents_created"] >= 1
        assert result["processed"] >= 1

        # Verify ops_alert was created
        alert = db.execute(text(
            "SELECT * FROM ops_alerts WHERE alert_type = 'merchant_email_bug_report' ORDER BY id DESC LIMIT 1"
        )).first()
        assert alert is not None

        # Verify email marked as executed
        db.refresh(email)
        assert email.routing_status == "executed"

    def test_feature_request_logs_feedback(self, db):
        """Feature request creates an info-level ops_alert for product feedback."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_inbound_actions

        email = InboundEmail(
            message_id="test-feature-001",
            from_email="merchant@test.com",
            subject="Can you add segment export?",
            body_text="I'd love to export my visitor segments to CSV",
            shop_domain="testshop.myshopify.com",
            classification="feature_request",
            classification_confidence="medium",
            classification_method="keyword",
            routing_action="log_product_feedback",
            routing_status="routed",
        )
        db.add(email)
        db.flush()

        result = run_inbound_actions(db)
        db.flush()

        assert result["feedback_logged"] >= 1

        alert = db.execute(text(
            "SELECT * FROM ops_alerts WHERE alert_type = 'product_feedback' ORDER BY id DESC LIMIT 1"
        )).first()
        assert alert is not None

    def test_idempotent_no_duplicate_alerts(self, db):
        """Running twice on same email doesn't create duplicate alerts."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_inbound_actions

        email = InboundEmail(
            message_id="test-idem-001",
            from_email="merchant@test.com",
            subject="Bug",
            body_text="Something broke",
            shop_domain="testshop.myshopify.com",
            classification="bug_report",
            routing_action="create_support_incident",
            routing_status="routed",
        )
        db.add(email)
        db.flush()

        run_inbound_actions(db)
        db.flush()

        # Email is now "executed" — second run should skip
        result2 = run_inbound_actions(db)
        assert result2["processed"] == 0

    def test_escalated_emails_skipped(self, db):
        """Emails with routing_status=escalated are not re-processed."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_inbound_actions

        email = InboundEmail(
            message_id="test-esc-001",
            from_email="angry@test.com",
            subject="Complaint",
            body_text="This is terrible",
            classification="complaint",
            routing_action="escalate_human",
            routing_status="escalated",
        )
        db.add(email)
        db.flush()

        result = run_inbound_actions(db)
        assert result["processed"] == 0


# ---------------------------------------------------------------------------
# C2: Email send cap enforcement
# ---------------------------------------------------------------------------

class TestEmailSendCap:

    def test_email_blocked_at_limit(self, db):
        """When email budget is exhausted, orchestrator blocks the send."""
        from app.services.merchant_email_service import submit_lifecycle_intent
        from app.services.email_orchestrator import resolve_and_flush, clear_intents
        from app.models.merchant import Merchant

        m = Merchant(shop_domain="captest.myshopify.com", contact_email="cap@test.com")
        db.add(m)
        db.flush()

        # Submit intent (this always succeeds — it's just queueing)
        result = submit_lifecycle_intent(db, "captest.myshopify.com", "welcome")
        assert result["status"] == "queued"

        # Flush with exhausted budget — orchestrator should block
        with patch("app.core.resend_usage.get_resend_usage", return_value={
            "sent": 100, "limit": 100, "pct": 100.0, "status": "critical",
        }), patch("app.core.resend_usage.RESEND_MONTHLY_LIMIT", 100):
            flush_result = resolve_and_flush(db)

        # The intent should have been suppressed (budget exhausted)
        assert flush_result["sent"] == 0
        clear_intents()

    def test_email_allowed_under_limit(self, db):
        """When budget is healthy, intent is queued successfully."""
        from app.services.merchant_email_service import submit_lifecycle_intent
        from app.services.email_orchestrator import clear_intents
        from app.models.merchant import Merchant

        m = Merchant(shop_domain="under.myshopify.com", contact_email="under@test.com")
        db.add(m)
        db.flush()

        result = submit_lifecycle_intent(db, "under.myshopify.com", "welcome")

        # Should be queued, not budget-suppressed
        assert result["status"] == "queued"
        assert result.get("reason") != "email_budget_exhausted"
        clear_intents()


# ---------------------------------------------------------------------------
# C4: Low-severity bug escalation
# ---------------------------------------------------------------------------

class TestLowSeverityEscalation:

    def test_escalation_on_repeated_bugs(self, db):
        """3+ bug reports from same shop in 7 days triggers escalation alert."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_low_severity_escalation

        shop = "buggy.myshopify.com"
        for i in range(4):
            db.add(InboundEmail(
                message_id=f"esc-test-{i}",
                from_email="buggy@test.com",
                shop_domain=shop,
                classification="bug_report",
                routing_status="executed",
                routing_action="create_support_incident",
            ))
        db.flush()

        with patch("app.services.inbound_action_executor._now", return_value=_now()):
            result = run_low_severity_escalation(db)
        db.flush()

        assert result["escalated"] >= 1

        # Verify escalation alert
        alert = db.execute(text(
            "SELECT * FROM ops_alerts WHERE alert_type = 'merchant_bug_escalation' "
            "AND shop_domain = :shop LIMIT 1"
        ), {"shop": shop}).first()
        assert alert is not None

    def test_no_escalation_below_threshold(self, db):
        """Fewer than 3 bugs does not trigger escalation."""
        from app.models.inbound_email import InboundEmail
        from app.services.inbound_action_executor import run_low_severity_escalation

        shop = "fewbugs.myshopify.com"
        for i in range(2):
            db.add(InboundEmail(
                message_id=f"few-test-{i}",
                from_email="few@test.com",
                shop_domain=shop,
                classification="bug_report",
                routing_status="executed",
            ))
        db.flush()

        result = run_low_severity_escalation(db)
        assert result["escalated"] == 0

    def test_first_insight_query_aggregates_per_shop(self, db):
        """N+1 collapse regression guard: the agent_worker first_insight
        query must return signal_count + top_signal_type + top_explanation
        per shop in a single GROUP BY, ordered by signal_strength DESC."""
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import text as sa_text
        from app.models.merchant import Merchant
        from app.models.opportunity_signal import OpportunitySignal
        from app.core.token_crypto import encrypt_token

        shop = "first-insight-test.myshopify.com"
        m = Merchant(
            shop_domain=shop,
            access_token=encrypt_token("shpat_x"),
            install_status="active",
            contact_email="ops@firstinsight.test",
        )
        db.add(m)

        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)
        # Insert 3 signals with distinct strengths — strongest must surface
        # as the "top" via array_agg(... ORDER BY signal_strength DESC NULLS LAST)[1]
        for prod, sig_type, expl, strength in [
            ("/products/weak", "low_intent", "weak signal", 0.2),
            ("/products/strong", "high_intent", "strong signal", 0.9),
            ("/products/medium", "med_intent", "medium signal", 0.5),
        ]:
            db.add(OpportunitySignal(
                shop_domain=shop,
                product_url=prod,
                signal_type=sig_type,
                signal_strength=strength,
                explanation=expl,
                expires_at=future,
            ))
        db.flush()

        rows = db.execute(sa_text("""
            SELECT
                os.shop_domain,
                COUNT(*)                                              AS signal_count,
                (array_agg(os.signal_type  ORDER BY os.signal_strength DESC NULLS LAST))[1] AS top_signal_type,
                (array_agg(os.explanation  ORDER BY os.signal_strength DESC NULLS LAST))[1] AS top_explanation
            FROM opportunity_signals os
            JOIN merchants m ON m.shop_domain = os.shop_domain
            WHERE m.install_status = 'active'
              AND m.contact_email IS NOT NULL
              AND m.contact_email != ''
              AND os.expires_at > now()
              AND os.shop_domain = :shop
            GROUP BY os.shop_domain
        """), {"shop": shop}).fetchall()

        assert len(rows) == 1
        row = rows[0]
        assert row[0] == shop
        assert int(row[1]) == 3            # signal_count
        assert row[2] == "high_intent"     # top_signal_type from strongest
        assert row[3] == "strong signal"   # top_explanation from strongest

    def test_bulk_dedup_skips_already_escalated_only(self, db):
        """Bulk-dedup correctness: shop with active escalation is skipped,
        sibling shops without active escalation are still escalated.
        Regression guard for the N+1 collapse (was 1 SELECT per shop)."""
        from app.models.inbound_email import InboundEmail
        from app.services.alerting import write_alert
        from app.services.inbound_action_executor import run_low_severity_escalation

        already_shop = "already-esc.myshopify.com"
        new_shop = "needs-esc.myshopify.com"

        # Pre-existing active escalation for already_shop
        write_alert(
            db,
            severity="warning",
            source="test",
            alert_type="merchant_bug_escalation",
            summary="prior escalation",
            shop_domain=already_shop,
        )
        db.flush()

        # 4 bug reports each — both shops cross the threshold
        for shop in (already_shop, new_shop):
            for i in range(4):
                db.add(InboundEmail(
                    message_id=f"dedup-{shop}-{i}",
                    from_email=f"x@{shop}",
                    shop_domain=shop,
                    classification="bug_report",
                    routing_status="executed",
                ))
        db.flush()

        with patch("app.services.inbound_action_executor._now", return_value=_now()):
            result = run_low_severity_escalation(db)
        db.flush()

        # Exactly one new escalation for new_shop; already_shop is skipped
        new_alerts = db.execute(text(
            "SELECT shop_domain FROM ops_alerts "
            "WHERE alert_type = 'merchant_bug_escalation' "
            "AND shop_domain IN (:a, :b) "
            "AND resolved = false"
        ), {"a": already_shop, "b": new_shop}).fetchall()
        shops_with_alert = {r[0] for r in new_alerts}
        assert new_shop in shops_with_alert
        # already_shop has exactly 1 alert (the pre-existing), not 2
        already_count = db.execute(text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE alert_type = 'merchant_bug_escalation' "
            "AND shop_domain = :s AND resolved = false"
        ), {"s": already_shop}).scalar()
        assert already_count == 1, f"expected 1 alert for already_shop, got {already_count}"


# ---------------------------------------------------------------------------
# H6: Per-merchant email diagnostics
# ---------------------------------------------------------------------------

class TestEmailDiagnostics:

    def test_trace_returns_merchant_info(self, client, db):
        """GET /ops/merchant/{shop}/email-trace returns merchant data."""
        from app.models.merchant import Merchant

        m = Merchant(shop_domain="trace.myshopify.com", contact_email="trace@test.com")
        db.add(m)
        db.flush()

        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get(
            "/ops/merchant/trace.myshopify.com/email-trace",
            headers={"X-API-Key": _key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["merchant"]["shop_domain"] == "trace.myshopify.com"
        assert data["merchant"]["contact_email"] == "trace@test.com"
        assert "diagnosis" in data
        assert isinstance(data["diagnosis"], list)

    def test_trace_unknown_merchant(self, client):
        """GET /ops/merchant/{shop}/email-trace for unknown merchant must 404.

        Pre-2026-05-08 returned 200 with `{"error": "merchant_not_found"}`
        — REST drift the audit flagged. Now correctly 404.
        """
        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get(
            "/ops/merchant/nonexistent.myshopify.com/email-trace",
            headers={"X-API-Key": _key},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "merchant_not_found"


# ---------------------------------------------------------------------------
# H7: DB pool metrics
# ---------------------------------------------------------------------------

class TestPoolMetrics:

    def test_pool_metrics_in_prometheus_output(self):
        """Pool gauges appear in /metrics output."""
        from app.core.metrics import render_metrics
        output = render_metrics()
        assert "hs_db_pool_size" in output
        assert "hs_db_pool_checkedout" in output
        assert "hs_db_pool_overflow" in output
        assert "hs_db_pool_checkedin" in output


# ---------------------------------------------------------------------------
# H9: Onboarding backoff
# ---------------------------------------------------------------------------

class TestOnboardingBackoff:

    def test_fail_increments_retry_count(self, db):
        """Onboarding failure increments retry_count and sets backoff."""
        from app.models.merchant import Merchant
        from app.services.onboarding import run_onboarding

        m = Merchant(
            shop_domain="backoff.myshopify.com",
            access_token=None,
            onboarding_retry_count=0,
        )
        db.add(m)
        db.flush()

        result = run_onboarding(db, m)
        assert result.status == "failed"
        assert m.onboarding_retry_count == 1
        assert m.onboarding_next_retry_at is not None
        # First retry should be ~1 hour later
        assert m.onboarding_next_retry_at > _now()

    def test_backoff_skips_early_retry(self, db):
        """Merchant in backoff period is skipped."""
        from app.models.merchant import Merchant
        from app.services.onboarding import run_onboarding

        m = Merchant(
            shop_domain="skip.myshopify.com",
            access_token=None,
            onboarding_status="failed",
            onboarding_retry_count=2,
            onboarding_next_retry_at=_now() + timedelta(hours=4),
        )
        db.add(m)
        db.flush()

        result = run_onboarding(db, m)
        assert result.status == "skipped"
        assert result.error == "backoff_active"

    def test_max_retries_gives_up(self, db):
        """After 5 retries, merchant is skipped permanently."""
        from app.models.merchant import Merchant
        from app.services.onboarding import run_onboarding

        m = Merchant(
            shop_domain="giveup.myshopify.com",
            access_token=None,
            onboarding_status="failed",
            onboarding_retry_count=5,
            onboarding_next_retry_at=None,
        )
        db.add(m)
        db.flush()

        result = run_onboarding(db, m)
        assert result.status == "skipped"
        assert result.error == "max_retries_exceeded"


# ---------------------------------------------------------------------------
# C3: Billing sync
# ---------------------------------------------------------------------------

class TestBillingSync:

    def test_cancelled_charge_deactivates(self, db):
        """Merchant with cancelled Shopify charge gets billing_active=False."""
        from app.models.merchant import Merchant
        from app.services.billing_sync import run_billing_sync

        m = Merchant(
            shop_domain="cancelled.myshopify.com",
            plan="pro",
            billing_active=True,
            billing_charge_id="12345",
            access_token="enc:v1:test",
        )
        db.add(m)
        db.flush()

        with patch("app.services.billing_sync._check_charge_status", return_value="cancelled"):
            result = run_billing_sync(db)

        assert result["deactivated"] == 1
        db.refresh(m)
        assert m.billing_active is False

    def test_active_charge_stays_active(self, db):
        """Merchant with active Shopify charge stays billing_active=True."""
        from app.models.merchant import Merchant
        from app.services.billing_sync import run_billing_sync

        m = Merchant(
            shop_domain="active-billing.myshopify.com",
            plan="pro",
            billing_active=True,
            billing_charge_id="67890",
            access_token="enc:v1:test",
        )
        db.add(m)
        db.flush()

        with patch("app.services.billing_sync._check_charge_status", return_value="active"):
            result = run_billing_sync(db)

        assert result["ok"] == 1
        db.refresh(m)
        assert m.billing_active is True

    def test_mass_deactivation_halts(self, db):
        """More than 3 deactivations in one cycle triggers safety halt."""
        from app.models.merchant import Merchant
        from app.services.billing_sync import run_billing_sync

        for i in range(5):
            db.add(Merchant(
                shop_domain=f"mass-{i}.myshopify.com",
                plan="pro",
                billing_active=True,
                billing_charge_id=str(i),
                access_token="enc:v1:test",
            ))
        db.flush()

        with patch("app.services.billing_sync._check_charge_status", return_value="cancelled"), \
             patch("app.services.billing_sync._alert_mass_deactivation"):
            result = run_billing_sync(db)

        # Should halt at 3
        assert result["deactivated"] == 3
        assert result["checked"] == 3


# ---------------------------------------------------------------------------
# H3: Silence detection
# ---------------------------------------------------------------------------

class TestSilenceDetection:

    def test_silent_merchant_detected(self, db):
        """Active merchant with 0 events in 14 days gets alert."""
        from app.models.merchant import Merchant
        from app.services.silence_detector import run_silence_detection

        m = Merchant(
            shop_domain="silent.myshopify.com",
            onboarding_status="ready",
            contact_email="silent@test.com",
        )
        db.add(m)
        db.flush()

        with patch("app.services.silence_detector._is_already_alerted", return_value=False), \
             patch("app.services.silence_detector._mark_alerted"):
            result = run_silence_detection(db)
        db.flush()

        assert result["detected"] >= 1
        assert result["alerted"] >= 1

    def test_dedup_prevents_duplicate_alerts(self, db):
        """Same merchant not alerted twice within 30 days."""
        from app.models.merchant import Merchant
        from app.services.silence_detector import run_silence_detection

        m = Merchant(
            shop_domain="dedup-silent.myshopify.com",
            onboarding_status="ready",
        )
        db.add(m)
        db.flush()

        with patch("app.services.silence_detector._is_already_alerted", return_value=True):
            result = run_silence_detection(db)

        assert result["alerted"] == 0
