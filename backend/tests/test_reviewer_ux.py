"""Tests for decision-first reviewer UX formatting in Telegram."""
import json
from unittest.mock import MagicMock

import pytest

from app.services.telegram_agent import (
    _classify_decision,
    _format_reviewer_decision,
    _format_reviewer_inline,
    _build_explanation,
    _DECISION_GREEN,
    _DECISION_YELLOW_CAUTION,
    _DECISION_YELLOW_IMPROVE,
    _DECISION_RED,
)


def _make_assessment(
    verdict="approve",
    risk="low",
    alignment="strong",
    blocking=None,
    notes=None,
    domains=None,
):
    a = MagicMock()
    a.verdict = verdict
    a.risk_level = risk
    a.strategic_alignment = alignment
    a.blocking_concerns_json = json.dumps(blocking) if blocking else None
    a.notes_json = json.dumps(notes) if notes else None
    a.affected_domains_json = json.dumps(domains) if domains else None
    a.entity_type = "bugfix_candidate"
    a.entity_id = 42
    return a


# ---------------------------------------------------------------------------
# Decision mapping
# ---------------------------------------------------------------------------

class TestDecisionMapping:
    def test_approve_low_strong_is_green(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="strong")
        assert _classify_decision(a) == _DECISION_GREEN

    def test_approve_with_notes_is_yellow_caution(self):
        a = _make_assessment(verdict="approve_with_notes", risk="low", alignment="strong")
        assert _classify_decision(a) == _DECISION_YELLOW_CAUTION

    def test_medium_risk_is_yellow_caution(self):
        a = _make_assessment(verdict="approve", risk="medium", alignment="strong")
        assert _classify_decision(a) == _DECISION_YELLOW_CAUTION

    def test_refine_is_yellow_improve(self):
        a = _make_assessment(verdict="refine", risk="low", alignment="strong")
        assert _classify_decision(a) == _DECISION_YELLOW_IMPROVE

    def test_weak_alignment_is_yellow_improve(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="weak")
        assert _classify_decision(a) == _DECISION_YELLOW_IMPROVE

    def test_reject_is_red(self):
        a = _make_assessment(verdict="reject", risk="low", alignment="strong")
        assert _classify_decision(a) == _DECISION_RED

    def test_high_risk_is_red(self):
        a = _make_assessment(verdict="approve", risk="high", alignment="strong")
        assert _classify_decision(a) == _DECISION_RED

    def test_critical_risk_is_red(self):
        a = _make_assessment(verdict="approve", risk="critical", alignment="strong")
        assert _classify_decision(a) == _DECISION_RED

    def test_reject_with_critical_is_red(self):
        a = _make_assessment(verdict="reject", risk="critical", alignment="weak")
        assert _classify_decision(a) == _DECISION_RED


# ---------------------------------------------------------------------------
# Explanation bullets
# ---------------------------------------------------------------------------

class TestExplanation:
    def test_blocking_concerns_first(self):
        a = _make_assessment(
            blocking=["Touches billing module"],
            notes=["Some note"],
        )
        bullets = _build_explanation(a)
        assert len(bullets) >= 1
        assert "billing" in bullets[0].lower()

    def test_notes_fill_remaining(self):
        a = _make_assessment(notes=["Note 1", "Note 2", "Note 3", "Note 4"])
        bullets = _build_explanation(a)
        assert len(bullets) == 3  # max 3

    def test_domains_as_fallback(self):
        a = _make_assessment(domains=["billing", "webhooks"])
        bullets = _build_explanation(a)
        assert len(bullets) == 1
        assert "billing" in bullets[0]

    def test_sensitive_domains_highlighted(self):
        a = _make_assessment(domains=["core", "infra"])
        bullets = _build_explanation(a)
        assert any("critical area" in b.lower() for b in bullets)

    def test_empty_assessment_no_bullets(self):
        a = _make_assessment()
        bullets = _build_explanation(a)
        assert len(bullets) == 0

    def test_max_three_bullets(self):
        a = _make_assessment(
            blocking=["Block 1", "Block 2"],
            notes=["Note 1", "Note 2"],
            domains=["core"],
        )
        bullets = _build_explanation(a)
        assert len(bullets) <= 3


# ---------------------------------------------------------------------------
# Full decision format
# ---------------------------------------------------------------------------

class TestDecisionFormat:
    def test_green_format(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="strong")
        result = _format_reviewer_decision(a)
        assert "You can proceed" in result

    def test_red_format_with_explanation(self):
        a = _make_assessment(
            verdict="reject", risk="critical",
            blocking=["Modifies encryption keys"],
            domains=["auth"],
        )
        result = _format_reviewer_decision(a)
        assert "Do NOT proceed" in result
        assert "encryption" in result.lower()

    def test_action_hint_included(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="strong")
        result = _format_reviewer_decision(a, action_hint="/approve 42")
        assert "/approve 42" in result

    def test_action_hint_has_pointer_emoji(self):
        a = _make_assessment()
        result = _format_reviewer_decision(a, action_hint="/approve 42")
        assert "\U0001f449" in result  # pointing right emoji

    def test_no_action_hint_when_not_provided(self):
        a = _make_assessment()
        result = _format_reviewer_decision(a)
        assert "\U0001f449" not in result

    def test_yellow_caution_format(self):
        a = _make_assessment(
            verdict="approve_with_notes", risk="medium",
            notes=["Large patch — review carefully"],
        )
        result = _format_reviewer_decision(a)
        assert "Proceed with caution" in result
        assert "Large patch" in result

    def test_yellow_improve_format(self):
        a = _make_assessment(verdict="refine", risk="low", notes=["Needs retry logic"])
        result = _format_reviewer_decision(a)
        assert "Needs improvement" in result
        assert "retry logic" in result.lower()


# ---------------------------------------------------------------------------
# Inline format (for list items)
# ---------------------------------------------------------------------------

class TestInlineFormat:
    def test_green_inline(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="strong")
        result = _format_reviewer_inline(a)
        assert "Safe to proceed" in result
        assert "\U0001f7e2" in result  # green circle

    def test_red_inline_with_reason(self):
        a = _make_assessment(
            verdict="reject", risk="critical",
            blocking=["Touches billing"],
        )
        result = _format_reviewer_inline(a)
        assert "Blocked" in result
        assert "Touches billing" in result
        assert "\U0001f534" in result  # red circle

    def test_yellow_caution_inline(self):
        a = _make_assessment(verdict="approve_with_notes", risk="medium")
        result = _format_reviewer_inline(a)
        assert "Caution" in result
        assert "\U0001f7e1" in result  # yellow circle

    def test_yellow_improve_inline(self):
        a = _make_assessment(verdict="refine", risk="low")
        result = _format_reviewer_inline(a)
        assert "Needs improvement" in result

    def test_high_risk_inline(self):
        a = _make_assessment(verdict="approve", risk="high")
        result = _format_reviewer_inline(a)
        assert "Blocked" in result


# ---------------------------------------------------------------------------
# No technical noise in output
# ---------------------------------------------------------------------------

class TestNoTechnicalNoise:
    def test_no_alignment_label(self):
        a = _make_assessment(verdict="approve", risk="low", alignment="strong")
        result = _format_reviewer_decision(a)
        assert "alignment" not in result.lower()
        assert "strategic" not in result.lower()

    def test_no_auto_approvable_label(self):
        a = _make_assessment()
        a.auto_approvable = True
        result = _format_reviewer_decision(a)
        assert "auto_approvable" not in result.lower()
        assert "auto-approvable" not in result.lower()

    def test_no_verdict_label(self):
        """Should NOT say 'approve_with_notes' — translate to human language."""
        a = _make_assessment(verdict="approve_with_notes", risk="medium")
        result = _format_reviewer_decision(a)
        assert "approve_with_notes" not in result
        assert "APPROVE WITH NOTES" not in result

    def test_no_risk_level_label(self):
        """Should NOT expose 'Risk: medium' — translate to decision."""
        a = _make_assessment(verdict="approve", risk="medium")
        result = _format_reviewer_decision(a)
        assert "Risk:" not in result

    def test_no_confidence_label(self):
        a = _make_assessment()
        a.confidence = "high"
        result = _format_reviewer_decision(a)
        assert "confidence" not in result.lower()


# ---------------------------------------------------------------------------
# Action suggestion always present (in list commands)
# ---------------------------------------------------------------------------

class TestActionSuggestionPresent:
    def test_approval_list_has_action(self, db):
        from app.services.telegram_agent import handle_command
        from app.models.action_approval import ActionApproval
        from app.models.audit_log import AuditLog
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import text

        db.execute(text("UPDATE action_approvals SET status = 'expired' WHERE status = 'pending'"))
        db.flush()

        audit = AuditLog(
            actor_type="agent", actor_name="orchestrator",
            action_type="orch_restart_worker", status="completed",
        )
        db.add(audit)
        db.flush()

        approval = ActionApproval(
            audit_log_id=audit.id,
            action_type="restart_worker",
            target_id="worker-1",
            status="pending",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()

        # Monkeypatch chat ID for auth
        import app.services.telegram_agent as ta
        old_chat = ta._CHAT_ID
        ta._CHAT_ID = "123"
        try:
            result = handle_command("/approvals", db=db, chat_id="123")
        finally:
            ta._CHAT_ID = old_chat

        assert f"/approve {approval.id}" in result
        assert f"/reject {approval.id}" in result

    def test_bugfix_list_has_action(self, db):
        from app.services.telegram_agent import handle_command
        from app.models.bugfix_candidate import BugFixCandidate
        from sqlalchemy import text

        db.execute(text("UPDATE bugfix_candidates SET status = 'rejected' WHERE status IN ('open', 'patch_proposed', 'approved')"))
        db.flush()

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_action_hint",
            title="Fix something", status="patch_proposed",
            patch_risk_tier=1,
        )
        db.add(c)
        db.flush()

        import app.services.telegram_agent as ta
        old_chat = ta._CHAT_ID
        ta._CHAT_ID = "123"
        try:
            result = handle_command("/bugfixes", db=db, chat_id="123")
        finally:
            ta._CHAT_ID = old_chat

        assert f"bugfix_approve {c.id}" in result or f"bugfix\\_approve {c.id}" in result

    def test_send_reviewer_verdict_has_action(self):
        """Push notification includes action suggestion."""
        from app.services.telegram_agent import send_reviewer_verdict

        a = _make_assessment(
            verdict="approve_with_notes", risk="medium",
            notes=["Review carefully"],
        )
        a.entity_type = "bugfix_candidate"
        a.entity_id = 42
        a.id = 999

        with MagicMock() as mock_send:
            import app.services.telegram_agent as ta
            orig = ta.send_message
            ta.send_message = mock_send
            mock_send.return_value = True
            try:
                send_reviewer_verdict(a, entity_title="Fix parser")
            finally:
                ta.send_message = orig

            sent_text = mock_send.call_args[0][0]
            assert "bugfix_approve 42" in sent_text or "bugfix\\_approve 42" in sent_text

    def test_reject_verdict_suggests_do_not_apply(self):
        """Reject push notification tells operator not to apply."""
        from app.services.telegram_agent import send_reviewer_verdict

        a = _make_assessment(
            verdict="reject", risk="critical",
            blocking=["Touches crypto module"],
        )
        a.entity_type = "bugfix_candidate"
        a.entity_id = 8
        a.id = 100

        with MagicMock() as mock_send:
            import app.services.telegram_agent as ta
            orig = ta.send_message
            ta.send_message = mock_send
            mock_send.return_value = True
            try:
                send_reviewer_verdict(a, entity_title="Dangerous change")
            finally:
                ta.send_message = orig

            sent_text = mock_send.call_args[0][0]
            assert "Do NOT proceed" in sent_text
            assert "Do not apply" in sent_text
