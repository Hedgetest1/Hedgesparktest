"""
Tests for growth vs conservatism balance in the email system.

Verifies:
- New merchants get aggressive treatment (first 7 days)
- Recovery attempts fire after 14-day cooldown
- Complaints are still permanently blocked
- Silence detector sends re-engagement emails
- Follow-up eligibility includes opened-but-not-clicked merchants
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TestAdaptiveEmailGrowth:

    def test_new_merchant_gets_aggressive_mode(self, db):
        """Merchants installed < 7 days ago are not throttled (up to 7 sends)."""
        from app.services.email_performance import should_send_email

        shop = "new-merchant.myshopify.com"
        # Create merchant installed 2 days ago
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=2)})

        # Create stats: 3 sends, 0 opens — would normally be blocked
        db.execute(text(
            "INSERT INTO merchant_email_stats (shop_domain, email_type, sent_count, opened_count, updated_at) "
            "VALUES (:shop, 'setup_incomplete', 3, 0, :now)"
        ), {"shop": shop, "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "setup_incomplete")
        assert ok is True
        assert reason == "new_merchant_aggressive"

    def test_new_merchant_capped_at_7(self, db):
        """Even aggressive mode caps at 7 sends in first week."""
        from app.services.email_performance import should_send_email

        shop = "capped-new.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=1)})

        db.execute(text(
            "INSERT INTO merchant_email_stats (shop_domain, email_type, sent_count, opened_count, updated_at) "
            "VALUES (:shop, 'setup_incomplete', 7, 0, :now)"
        ), {"shop": shop, "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "setup_incomplete")
        assert ok is False
        assert reason == "new_merchant_weekly_cap"

    def test_recovery_attempt_after_14_days(self, db):
        """Blocked merchants get one retry after 14-day cooldown."""
        from app.services.email_performance import should_send_email

        shop = "recovery.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=30)})

        # 3 sends, 0 opens, last sent 15 days ago
        db.execute(text(
            "INSERT INTO merchant_email_stats "
            "(shop_domain, email_type, sent_count, opened_count, last_sent_at, updated_at) "
            "VALUES (:shop, 'setup_incomplete', 3, 0, :last_sent, :now)"
        ), {"shop": shop, "last_sent": _now() - timedelta(days=15), "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "setup_incomplete")
        assert ok is True
        assert reason == "recovery_attempt"

    def test_no_recovery_if_already_tried_5(self, db):
        """After 5 total sends with 0 opens, no more recovery attempts."""
        from app.services.email_performance import should_send_email

        shop = "exhausted.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=60)})

        db.execute(text(
            "INSERT INTO merchant_email_stats "
            "(shop_domain, email_type, sent_count, opened_count, last_sent_at, updated_at) "
            "VALUES (:shop, 'setup_incomplete', 5, 0, :last_sent, :now)"
        ), {"shop": shop, "last_sent": _now() - timedelta(days=20), "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "setup_incomplete")
        assert ok is False
        assert reason == "never_opened"

    def test_complaint_always_blocked(self, db):
        """Complaints are permanently blocked even for new merchants."""
        from app.services.email_performance import should_send_email

        shop = "complained-new.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=1)})

        db.execute(text(
            "INSERT INTO merchant_email_stats "
            "(shop_domain, email_type, sent_count, opened_count, complained_count, updated_at) "
            "VALUES (:shop, 'welcome', 1, 0, 1, :now)"
        ), {"shop": shop, "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "welcome")
        assert ok is False
        assert reason == "complained"

    def test_established_merchant_low_open_rate_threshold(self, db):
        """Established merchants blocked at <15% open rate after 7+ sends."""
        from app.services.email_performance import should_send_email

        shop = "established.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, installed_at, install_status) "
            "VALUES (:shop, :installed, 'active')"
        ), {"shop": shop, "installed": _now() - timedelta(days=30)})

        # 10 sends, 1 open = 10% open rate
        db.execute(text(
            "INSERT INTO merchant_email_stats "
            "(shop_domain, email_type, sent_count, opened_count, updated_at) "
            "VALUES (:shop, 'setup_incomplete', 10, 1, :now)"
        ), {"shop": shop, "now": _now()})
        db.flush()

        ok, reason = should_send_email(db, shop, "setup_incomplete")
        assert ok is False
        assert "low_open_rate" in reason


class TestFollowupEligibility:

    def test_opened_but_not_clicked_is_eligible(self, db):
        """Merchants who opened the invite but didn't click ARE eligible for follow-up."""
        from app.models.merchant_journey_state import MerchantJourneyState
        from app.services.email_journey import get_followup_eligible

        j = MerchantJourneyState(
            shop_domain="opened-no-click.myshopify.com",
            beta_invite_sent_at=_now() - timedelta(hours=72),
            beta_invite_opened_at=_now() - timedelta(hours=60),
            # clicked_at is NULL — they opened but didn't click
            current_stage="opened",
        )
        db.add(j)
        db.flush()

        eligible = get_followup_eligible(db)
        shops = [e.shop_domain for e in eligible]
        assert "opened-no-click.myshopify.com" in shops

    def test_completed_onboarding_not_eligible(self, db):
        """Merchants who completed onboarding are NOT eligible for follow-up."""
        from app.models.merchant_journey_state import MerchantJourneyState
        from app.services.email_journey import get_followup_eligible

        j = MerchantJourneyState(
            shop_domain="completed.myshopify.com",
            beta_invite_sent_at=_now() - timedelta(hours=72),
            beta_invite_opened_at=_now() - timedelta(hours=60),
            onboarding_completed_at=_now() - timedelta(hours=10),
            current_stage="active",
        )
        db.add(j)
        db.flush()

        eligible = get_followup_eligible(db)
        shops = [e.shop_domain for e in eligible]
        assert "completed.myshopify.com" not in shops


class TestSilenceReengagement:

    def test_silence_detector_sends_email(self, db):
        """Silent merchant gets a re-engagement intent submitted to orchestrator."""
        from app.models.merchant import Merchant
        from app.services.silence_detector import run_silence_detection

        m = Merchant(
            shop_domain="silent-email.myshopify.com",
            onboarding_status="ready",
            contact_email="silent@test.com",
        )
        db.add(m)
        db.flush()

        with patch("app.services.silence_detector._is_already_alerted", return_value=False), \
             patch("app.services.silence_detector._mark_alerted"), \
             patch("app.services.email_orchestrator.submit_intent", return_value="intent_123") as mock_submit:
            result = run_silence_detection(db)
        db.flush()

        assert result["alerted"] >= 1
        # Verify intent was submitted to orchestrator (not direct send)
        assert mock_submit.called

    def test_silence_dedup_is_14_days(self):
        """Silence dedup TTL is 14 days, not 30."""
        from app.services.silence_detector import _REDIS_SILENCE_TTL
        assert _REDIS_SILENCE_TTL == 86400 * 14
