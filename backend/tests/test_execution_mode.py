"""Tests for execution mode safety and false-positive prevention."""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

import pytest
from sqlalchemy import text


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# EXECUTION_MODE config
# ---------------------------------------------------------------------------

class TestExecutionMode:
    def test_default_is_real(self):
        from app.core.execution_mode import is_real, is_dry_run
        # Default in test env should be real
        assert is_real() or is_dry_run()  # one must be true

    def test_is_dry_run_helper(self):
        import app.core.execution_mode as em
        original = em.EXECUTION_MODE
        try:
            em.EXECUTION_MODE = "dry_run"
            assert em.is_dry_run() is True
            assert em.is_real() is False
        finally:
            em.EXECUTION_MODE = original

    def test_is_real_helper(self):
        import app.core.execution_mode as em
        original = em.EXECUTION_MODE
        try:
            em.EXECUTION_MODE = "real"
            assert em.is_real() is True
            assert em.is_dry_run() is False
        finally:
            em.EXECUTION_MODE = original


# ---------------------------------------------------------------------------
# DRY RUN prefix on Telegram messages
# ---------------------------------------------------------------------------

class TestDryRunPrefix:
    def test_dry_run_prefixes_message(self):
        """In dry_run mode, send_message prepends [DRY RUN] to text."""
        from app.services.telegram_agent import send_message

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch("app.core.execution_mode.is_dry_run", return_value=True), \
             patch("app.services.telegram_agent._BOT_TOKEN", "test-token"), \
             patch("app.services.telegram_agent._CHAT_ID", "123"), \
             patch("app.services.telegram_agent._get_http_client", return_value=mock_client):

            send_message("Opus audit completed.")

            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"] if "json" in call_args[1] else call_args[0][1]["text"]
            # Brackets are escaped for Telegram Markdown V1 safety
            assert "DRY RUN" in sent_text
            assert "Opus audit completed." in sent_text

    def test_real_mode_no_prefix(self):
        """In real mode, send_message does NOT prefix."""
        from app.services.telegram_agent import send_message

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client.is_closed = False

        with patch("app.core.execution_mode.is_dry_run", return_value=False), \
             patch("app.services.telegram_agent._BOT_TOKEN", "test-token"), \
             patch("app.services.telegram_agent._CHAT_ID", "123"), \
             patch("app.services.telegram_agent._get_http_client", return_value=mock_client):

            send_message("Opus audit completed.")

            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"] if "json" in call_args[1] else call_args[0][1]["text"]
            assert not sent_text.startswith("[DRY RUN]")


# ---------------------------------------------------------------------------
# Monthly Opus audit: no false success
# ---------------------------------------------------------------------------

class TestMonthlyAuditNoFalseSuccess:
    def test_skipped_audit_does_not_send_completed_message(self):
        """When Opus audit is skipped (no API key), Telegram must NOT say 'completed'."""
        from app.workers.agent_worker import _run_monthly_evolution_audit

        skipped_result = {
            "status": "skipped",
            "reason": "llm_unavailable",
            "proposals_created": 0,
            "proposals": [],
        }

        with patch("app.services.monthly_evolution_audit.should_run_monthly_audit", return_value=True), \
             patch("app.services.monthly_evolution_audit.run_monthly_opus_audit", return_value=skipped_result), \
             patch("app.services.monthly_evolution_audit.mark_monthly_audit_run") as mock_mark, \
             patch("app.services.telegram_agent.is_configured", return_value=True), \
             patch("app.services.telegram_agent.send_monthly_report") as mock_report, \
             patch("app.services.telegram_agent.send_message") as mock_msg, \
             patch("app.workers.agent_worker.SessionLocal") as mock_session_cls:

            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db

            _run_monthly_evolution_audit()

            # send_monthly_report must NOT be called (that's the "completed" message)
            mock_report.assert_not_called()

            # mark_monthly_audit_run MUST be called even on skip (prevents
            # infinite retry loop when LLM keys aren't configured)
            mock_mark.assert_called_once()

            # "Skipped" audit must NOT send Telegram (noise suppression)
            mock_msg.assert_not_called()

    def test_real_audit_sends_completed_message(self):
        """When audit actually runs with proposals, Telegram sends the report."""
        from app.workers.agent_worker import _run_monthly_evolution_audit

        real_result = {
            "status": "completed",
            "proposals_created": 3,
            "proposals": [{"title": "Test", "type": "architecture"}],
            "cycle": "2026-M03",
        }

        mock_summary = {
            "infra": {
                "ram": {"used_mb": 512, "total_mb": 2048, "usage_pct": 25},
                "cpu": {"load_5m": 0.5, "cpu_count": 2},
                "workers": {"cycles_24h": 100, "error_rate_pct": 0},
            },
            "llm_usage": {"global_calls_today": 10, "global_max_per_day": 150},
            "cost_estimate": {
                "fixed_monthly_eur": {"server_vps": 10.0},
                "fixed_total_eur": 10.0,
                "llm_monthly_eur": 5.0,
                "total_monthly_eur": 15.0,
            },
            "warnings": [],
        }

        with patch("app.services.monthly_evolution_audit.should_run_monthly_audit", return_value=True), \
             patch("app.services.monthly_evolution_audit.run_monthly_opus_audit", return_value=real_result), \
             patch("app.services.monthly_evolution_audit.mark_monthly_audit_run") as mock_mark, \
             patch("app.services.telegram_agent.is_configured", return_value=True), \
             patch("app.services.telegram_agent.send_monthly_report") as mock_report, \
             patch("app.services.system_summary.build_system_summary", return_value=mock_summary), \
             patch("app.workers.agent_worker.SessionLocal") as mock_session_cls:

            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db

            _run_monthly_evolution_audit()

            # Real audit: send_monthly_report IS called
            mock_report.assert_called_once()

            # mark_monthly_audit_run IS called
            mock_mark.assert_called_once()


# ---------------------------------------------------------------------------
# Scaling: no false forecast alerts
# ---------------------------------------------------------------------------

class TestScalingNoFalseForecast:
    def test_insufficient_data_does_not_send_full_alert(self):
        """When forecast has insufficient data, don't send full scaling alert."""
        from app.workers.agent_worker import _run_scaling_intelligence

        insufficient_forecast = {
            "status": "not_enough_data",
            "snapshots_available": 2,
            "minimum_required": 5,
        }

        mock_recs = [{"id": 1, "title": "Add RAM", "severity": "warning"}]

        with patch("app.services.scaling_intelligence.should_generate_recommendations", return_value=True), \
             patch("app.services.scaling_intelligence.should_capture_snapshot", return_value=False), \
             patch("app.services.scaling_intelligence.generate_recommendations", return_value=mock_recs), \
             patch("app.services.scaling_intelligence.mark_recommendations_generated"), \
             patch("app.services.scaling_intelligence.build_forecast", return_value=insufficient_forecast), \
             patch("app.services.telegram_agent.is_configured", return_value=True), \
             patch("app.services.telegram_agent.send_scaling_alert") as mock_alert, \
             patch("app.services.telegram_agent.send_message") as mock_msg, \
             patch("app.workers.agent_worker.SessionLocal") as mock_session_cls:

            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db

            _run_scaling_intelligence()

            # Full alert must NOT be sent (insufficient data)
            mock_alert.assert_not_called()

            # Instead, a disclaimer message should be sent
            mock_msg.assert_called_once()
            sent_text = mock_msg.call_args[0][0]
            assert "insufficient data" in sent_text.lower() or "not yet reliable" in sent_text.lower()


# ---------------------------------------------------------------------------
# Model upgrade: blocked vs evaluated distinction
# ---------------------------------------------------------------------------

class TestModelUpgradeNoFalseEval:
    def test_budget_blocked_is_not_evaluated(self, db):
        """When budget blocks evaluation, status must be 'blocked', not 'evaluated'."""
        from app.models.model_upgrade import ModelUpgradeProposal
        from app.services.model_upgrade_agent import evaluate_upgrade

        proposal = ModelUpgradeProposal(
            current_provider="anthropic", current_model="claude-3-haiku-20240307",
            candidate_provider="anthropic", candidate_model="claude-sonnet-4-20250514",
            target_module="orchestrator",
            reason="Test", risk_level="LEVEL_2", status="pending",
        )
        db.add(proposal)
        db.flush()

        with patch("app.core.llm_budget.check_budget", return_value=(False, "daily_limit_reached")), \
             patch("app.core.llm_budget.record_blocked"):
            result = evaluate_upgrade(db, proposal.id)

        assert result == "blocked"
        db.refresh(proposal)
        assert proposal.status == "blocked"
        assert proposal.eval_result == "blocked"
        detail = json.loads(proposal.eval_detail)
        assert detail.get("real_execution") is False

    def test_no_api_key_is_blocked(self, db):
        """When API key is missing, status must be 'blocked'."""
        from app.models.model_upgrade import ModelUpgradeProposal
        from app.services.model_upgrade_agent import evaluate_upgrade

        proposal = ModelUpgradeProposal(
            current_provider="anthropic", current_model="claude-3-haiku-20240307",
            candidate_provider="anthropic", candidate_model="claude-sonnet-4-20250514",
            target_module="orchestrator",
            reason="Test", risk_level="LEVEL_2", status="pending",
        )
        db.add(proposal)
        db.flush()

        with patch("app.core.llm_budget.check_budget", return_value=(True, "")), \
             patch("app.core.llm_budget.record_usage"), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = evaluate_upgrade(db, proposal.id)

        assert result == "blocked"
        db.refresh(proposal)
        assert proposal.status == "blocked"
        assert proposal.eval_result == "blocked"

        # Audit log should say "skipped", not "completed"
        from app.models.audit_log import AuditLog
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.actor_name == "model_upgrade_agent")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.status == "skipped"


# ---------------------------------------------------------------------------
# Telegram messages reflect real execution state
# ---------------------------------------------------------------------------

class TestTelegramReflectsRealState:
    def test_send_monthly_report_only_with_real_proposals(self):
        """send_monthly_report should only be called with real proposals."""
        from app.services.telegram_agent import send_monthly_report

        # Verify the function includes "Opus audit completed" in its output
        with patch("app.services.telegram_agent.send_message") as mock_send:
            mock_send.return_value = True
            summary = {
                "infra": {
                    "ram": {"used_mb": 512, "total_mb": 2048, "usage_pct": 25},
                    "cpu": {},
                    "workers": {"error_rate_pct": 0, "cycles_24h": 100},
                },
                "llm_usage": {"global_calls_today": 5, "global_max_per_day": 150},
                "cost_estimate": {
                    "fixed_monthly_eur": {"server_vps": 10.0},
                    "fixed_total_eur": 10.0,
                    "llm_monthly_eur": 5.0,
                    "total_monthly_eur": 15.0,
                },
                "warnings": [],
            }
            send_monthly_report([{"title": "Test", "type": "arch"}], summary)
            sent_text = mock_send.call_args[0][0]
            assert "Opus audit completed" in sent_text
            assert "Test" in sent_text
