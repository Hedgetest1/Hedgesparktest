"""Tests for Telegram operator control center commands."""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text

from app.models.action_approval import ActionApproval
from app.models.audit_log import AuditLog
from app.models.bugfix_candidate import BugFixCandidate
from app.models.reviewer_assessment import ReviewerAssessment


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


AUTHORIZED_CHAT = "123456789"
UNAUTHORIZED_CHAT = "999999999"


@pytest.fixture(autouse=True)
def _set_telegram_env(monkeypatch):
    """Set TELEGRAM_CHAT_ID for auth tests and reset rate limits."""
    monkeypatch.setattr("app.services.telegram_agent._CHAT_ID", AUTHORIZED_CHAT)
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "test-token")
    # Reset rate limiter between tests
    from app.services.telegram_agent import reset_rate_limits
    reset_rate_limits()


@pytest.fixture(autouse=True)
def _isolate(db):
    """Neutralize existing data."""
    db.execute(text("UPDATE action_approvals SET status = 'expired' WHERE status = 'pending'"))
    db.execute(text("UPDATE bugfix_candidates SET status = 'rejected' WHERE status IN ('open', 'patch_proposed', 'approved')"))
    db.flush()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_unauthorized_chat_blocked(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approvals", db=db, chat_id=UNAUTHORIZED_CHAT)
        assert "Unauthorized" in result

    def test_unauthorized_chat_blocked_for_write(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approve 1", db=db, chat_id=UNAUTHORIZED_CHAT)
        assert "Unauthorized" in result

    def test_authorized_chat_allowed(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approvals", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Unauthorized" not in result

    def test_is_authorized_chat(self):
        from app.services.telegram_agent import is_authorized_chat
        assert is_authorized_chat(AUTHORIZED_CHAT) is True
        assert is_authorized_chat(UNAUTHORIZED_CHAT) is False
        assert is_authorized_chat("") is False


# ---------------------------------------------------------------------------
# /approvals
# ---------------------------------------------------------------------------

class TestApprovals:
    def test_approvals_lists_pending(self, db):
        from app.services.telegram_agent import handle_command
        from app.models.audit_log import AuditLog

        audit = AuditLog(
            actor_type="agent", actor_name="orchestrator",
            action_type="orch_restart_worker", status="completed",
        )
        db.add(audit)
        db.flush()

        approval = ActionApproval(
            audit_log_id=audit.id,
            action_type="restart_worker",
            target_id="wishspark-worker",
            status="pending",
            reason="Worker unhealthy",
            expires_at=_now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()

        result = handle_command("/approvals", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Pending approvals" in result
        assert "restart_worker" in result
        assert str(approval.id) in result

    def test_approvals_empty(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approvals", db=db, chat_id=AUTHORIZED_CHAT)
        assert "No pending approvals" in result


# ---------------------------------------------------------------------------
# /approve
# ---------------------------------------------------------------------------

class TestApprove:
    def _create_pending_approval(self, db, action_type="resolve_alert"):
        audit = AuditLog(
            actor_type="agent", actor_name="orchestrator",
            action_type=f"orch_{action_type}", status="completed",
        )
        db.add(audit)
        db.flush()

        approval = ActionApproval(
            audit_log_id=audit.id,
            action_type=action_type,
            target_id="test-target",
            status="pending",
            expires_at=_now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()
        return approval

    def test_approve_executes(self, db):
        from app.services.telegram_agent import handle_command

        approval = self._create_pending_approval(db)

        mock_fn = MagicMock(return_value="resolved")
        mock_registry = {
            "resolve_alert": (mock_fn, "Resolve alert", 0),
        }

        with patch("app.services.orchestrator.ACTION_REGISTRY", mock_registry), \
             patch("app.services.orchestrator._is_on_cooldown", return_value=False), \
             patch("app.services.orchestrator._set_cooldown"), \
             patch("app.services.outcome_evaluator.record_pending_outcome"):
            result = handle_command(f"/approve {approval.id}", db=db, chat_id=AUTHORIZED_CHAT)

        assert "Approved and executed" in result
        db.refresh(approval)
        assert approval.status == "approved"
        assert approval.decided_by == "telegram_operator"

    def test_approve_writes_audit_log(self, db):
        from app.services.telegram_agent import handle_command

        approval = self._create_pending_approval(db)

        mock_fn = MagicMock(return_value="resolved")
        mock_registry = {
            "resolve_alert": (mock_fn, "Resolve alert", 0),
        }

        with patch("app.services.orchestrator.ACTION_REGISTRY", mock_registry), \
             patch("app.services.orchestrator._is_on_cooldown", return_value=False), \
             patch("app.services.orchestrator._set_cooldown"), \
             patch("app.services.outcome_evaluator.record_pending_outcome"):
            handle_command(f"/approve {approval.id}", db=db, chat_id=AUTHORIZED_CHAT)

        audit = (
            db.query(AuditLog)
            .filter(AuditLog.actor_name == "telegram_operator")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.actor_type == "human"
        assert audit.actor_name == "telegram_operator"
        assert "approved_resolve_alert" in audit.action_type
        assert audit.approval_mode == "human_approved"
        meta = json.loads(audit.metadata_json) if audit.metadata_json else {}
        assert meta.get("channel") == "telegram"

    def test_approve_not_found(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approve 99999", db=db, chat_id=AUTHORIZED_CHAT)
        assert "not found" in result

    def test_approve_already_decided(self, db):
        from app.services.telegram_agent import handle_command

        approval = self._create_pending_approval(db)
        approval.status = "approved"
        db.flush()

        result = handle_command(f"/approve {approval.id}", db=db, chat_id=AUTHORIZED_CHAT)
        assert "already approved" in result

    def test_approve_expired(self, db):
        from app.services.telegram_agent import handle_command

        approval = self._create_pending_approval(db)
        approval.expires_at = _now() - timedelta(hours=1)
        db.flush()

        result = handle_command(f"/approve {approval.id}", db=db, chat_id=AUTHORIZED_CHAT)
        assert "expired" in result

    def test_approve_no_id(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approve", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Usage" in result

    def test_approve_invalid_id(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approve abc", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Invalid" in result


# ---------------------------------------------------------------------------
# /reject
# ---------------------------------------------------------------------------

class TestReject:
    def test_reject_with_reason(self, db):
        from app.services.telegram_agent import handle_command

        audit = AuditLog(
            actor_type="agent", actor_name="orchestrator",
            action_type="orch_restart_worker", status="completed",
        )
        db.add(audit)
        db.flush()

        approval = ActionApproval(
            audit_log_id=audit.id,
            action_type="restart_worker",
            target_id="test-worker",
            status="pending",
            expires_at=_now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()

        result = handle_command(
            f"/reject {approval.id} needs investigation first",
            db=db, chat_id=AUTHORIZED_CHAT,
        )

        assert "Rejected" in result
        db.refresh(approval)
        assert approval.status == "rejected"
        assert approval.decided_by == "telegram_operator"
        assert approval.reason == "needs investigation first"

        # Check audit log
        audit_entry = (
            db.query(AuditLog)
            .filter(AuditLog.actor_name == "telegram_operator")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit_entry is not None
        assert "rejected_restart_worker" in audit_entry.action_type

    def test_reject_not_found(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/reject 99999", db=db, chat_id=AUTHORIZED_CHAT)
        assert "not found" in result


# ---------------------------------------------------------------------------
# /bugfixes
# ---------------------------------------------------------------------------

class TestBugfixes:
    def test_bugfixes_lists_actionable(self, db):
        from app.services.telegram_agent import handle_command

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_list_1",
            title="Fix webhook timeout", status="patch_proposed",
            patch_risk_tier=1,
        )
        db.add(c)
        db.flush()

        result = handle_command("/bugfixes", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Bugfix candidates" in result
        assert "Fix webhook timeout" in result
        assert "bugfix_approve" in result or "bugfix\\_approve" in result

    def test_bugfixes_empty(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/bugfixes", db=db, chat_id=AUTHORIZED_CHAT)
        assert "No bugfix candidates" in result


# ---------------------------------------------------------------------------
# /bugfix_approve
# ---------------------------------------------------------------------------

class TestBugfixApprove:
    def test_bugfix_approve_success(self, db):
        from app.services.telegram_agent import handle_command

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_approve_1",
            title="Fix signal parser", status="patch_proposed",
            patch_risk_tier=1,
            patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        )
        db.add(c)
        db.flush()

        result = handle_command(f"/bugfix_approve {c.id}", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Bugfix approved" in result

        db.refresh(c)
        assert c.status == "approved"
        assert c.decided_by == "telegram_operator"

        # Check audit
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.actor_name == "telegram_operator", AuditLog.action_type == "bugfix_approved")
            .first()
        )
        assert audit is not None

    def test_bugfix_approve_wrong_status(self, db):
        from app.services.telegram_agent import handle_command

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_wrong_1",
            title="Already applied", status="applied",
        )
        db.add(c)
        db.flush()

        result = handle_command(f"/bugfix_approve {c.id}", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Cannot approve" in result

    def test_bugfix_approve_not_found(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/bugfix_approve 99999", db=db, chat_id=AUTHORIZED_CHAT)
        assert "not found" in result


# ---------------------------------------------------------------------------
# /bugfix_apply
# ---------------------------------------------------------------------------

class TestBugfixApply:
    def test_bugfix_apply_uses_guarded_path(self, db):
        from app.services.telegram_agent import handle_command
        from app.services.bugfix_pipeline import ApplyResult

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_apply_1",
            title="Fix safe file", status="approved",
            patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
            patch_files=json.dumps(["tests/test_placeholder.py"]),
            patch_risk_tier=0,
        )
        db.add(c)
        db.flush()

        mock_result = ApplyResult(
            status="applied", test_passed=True,
            test_output="ok", health_ok=True, failure_reason=None,
        )

        with patch("app.services.bugfix_pipeline.apply_bugfix_candidate", return_value=mock_result):
            result = handle_command(f"/bugfix_apply {c.id}", db=db, chat_id=AUTHORIZED_CHAT)

        assert "Bugfix applied" in result
        assert "Tests: passed" in result

        # Check audit
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.actor_name == "telegram_operator",
                AuditLog.action_type == "bugfix_apply_triggered",
            )
            .first()
        )
        assert audit is not None
        assert audit.status == "completed"

    def test_bugfix_apply_not_approved(self, db):
        from app.services.telegram_agent import handle_command

        c = BugFixCandidate(
            source_type="ops_alert", source_ref="test_not_approved",
            title="Not ready", status="patch_proposed",
        )
        db.add(c)
        db.flush()

        result = handle_command(f"/bugfix_apply {c.id}", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Must be approved first" in result


# ---------------------------------------------------------------------------
# /merge
# ---------------------------------------------------------------------------

class TestMerge:
    def test_merge_respects_gating(self, db):
        from app.services.telegram_agent import handle_command

        from app.models.autofix_promotion import AutoFixPromotion
        p = AutoFixPromotion(
            bugfix_candidate_id=1, git_commit_sha="abc123",
            status="pushed",
        )
        db.add(p)
        db.flush()

        # Mock merge recommendation to refuse
        mock_rec = MagicMock()
        mock_rec.recommend = False
        mock_rec.reasons = ["Remote CI not passed", "PR not created"]

        with patch("app.services.merge_intelligence.compute_merge_recommendation", return_value=mock_rec):
            result = handle_command(f"/merge {p.id}", db=db, chat_id=AUTHORIZED_CHAT)

        assert "Cannot merge" in result
        assert "Remote CI not passed" in result

    def test_merge_not_found(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/merge 99999", db=db, chat_id=AUTHORIZED_CHAT)
        assert "not found" in result


# ---------------------------------------------------------------------------
# /review
# ---------------------------------------------------------------------------

class TestReview:
    def test_review_shows_assessment(self, db):
        from app.services.telegram_agent import handle_command

        assessment = ReviewerAssessment(
            entity_type="bugfix_candidate", entity_id=42,
            verdict="approve_with_notes", risk_level="medium",
            strategic_alignment="strong", confidence="high",
            auto_approvable=False,
            summary="Coherent fix, review retry logic",
            notes_json=json.dumps(["Large patch — review carefully"]),
            blocking_concerns_json=None,
            reviewer_mode="deterministic",
        )
        db.add(assessment)
        db.flush()

        result = handle_command("/review bugfix 42", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Proceed with caution" in result
        assert "Large patch" in result

    def test_review_no_assessment(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/review bugfix 99999", db=db, chat_id=AUTHORIZED_CHAT)
        assert "No reviewer assessment" in result

    def test_review_unknown_type(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/review foobar 1", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Unknown entity type" in result

    def test_review_missing_args(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/review", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Usage" in result


# ---------------------------------------------------------------------------
# /promotions
# ---------------------------------------------------------------------------

class TestPromotions:
    def test_promotions_lists_active(self, db):
        from app.services.telegram_agent import handle_command
        from app.models.autofix_promotion import AutoFixPromotion

        p = AutoFixPromotion(
            bugfix_candidate_id=1, git_commit_sha="abc123",
            status="pushed", remote_ci_status="passed",
            pr_url="https://github.com/test/repo/pull/5",
            pr_number=5,
        )
        db.add(p)
        db.flush()

        result = handle_command("/promotions", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Active promotions" in result
        assert "pushed" in result
        assert "CI: passed" in result

    def test_promotions_empty(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/promotions", db=db, chat_id=AUTHORIZED_CHAT)
        assert "No active promotions" in result


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

class TestHelp:
    def test_help_shows_all_commands(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/help", db=db, chat_id=AUTHORIZED_CHAT)
        assert "/approvals" in result
        assert "/approve" in result
        assert "/reject" in result
        assert "/bugfixes" in result
        assert "/bugfix_approve" in result or "bugfix\\_approve" in result
        assert "/bugfix_apply" in result or "bugfix\\_apply" in result
        assert "/promotions" in result
        assert "/merge" in result
        assert "/review" in result
        assert "/status" in result


# ---------------------------------------------------------------------------
# Reviewer summary formatting in responses
# ---------------------------------------------------------------------------

class TestReviewerFormatting:
    def test_reviewer_context_in_approval_list(self, db):
        from app.services.telegram_agent import handle_command

        audit = AuditLog(
            actor_type="agent", actor_name="orchestrator",
            action_type="orch_restart_worker", status="completed",
        )
        db.add(audit)
        db.flush()

        approval = ActionApproval(
            audit_log_id=audit.id,
            action_type="restart_worker",
            target_id="wishspark-worker",
            status="pending",
            expires_at=_now() + timedelta(hours=1),
        )
        db.add(approval)
        db.flush()

        # Add a reviewer assessment for this approval
        assessment = ReviewerAssessment(
            entity_type="action_approval", entity_id=approval.id,
            verdict="approve", risk_level="low",
            strategic_alignment="strong", confidence="high",
            auto_approvable=True,
            summary="Safe action",
            reviewer_mode="deterministic",
        )
        db.add(assessment)
        db.flush()

        result = handle_command("/approvals", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Safe to proceed" in result
        assert f"/approve {approval.id}" in result

    def test_decision_first_reviewer_format(self):
        from app.services.telegram_agent import _format_reviewer_decision

        assessment = MagicMock()
        assessment.verdict = "approve_with_notes"
        assessment.risk_level = "medium"
        assessment.strategic_alignment = "strong"
        assessment.blocking_concerns_json = None
        assessment.notes_json = json.dumps(["Missing retry guard"])
        assessment.affected_domains_json = None

        result = _format_reviewer_decision(assessment)
        assert "Proceed with caution" in result
        assert "Missing retry guard" in result


# ---------------------------------------------------------------------------
# Invalid/edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_command(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/foobar", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Unknown command" in result

    def test_command_with_bot_suffix(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/help@HedgeSparkBot", db=db, chat_id=AUTHORIZED_CHAT)
        assert "/approvals" in result

    def test_approve_missing_args(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/approve", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Usage" in result

    def test_merge_missing_args(self, db):
        from app.services.telegram_agent import handle_command
        result = handle_command("/merge", db=db, chat_id=AUTHORIZED_CHAT)
        assert "Usage" in result
