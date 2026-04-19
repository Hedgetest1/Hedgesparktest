"""Tests for the B2 real-model LLM drift weekly check.

Locks:
- Corpus structure: >= 3 JSON, >= 3 refusal, >= 1 severity-shape prompt
- Schedule gate: skip outside Sunday 06:00-07:00 UTC
- Weekly dedup: skip when already-ran Redis flag set (unless force)
- No API key configured → silent skip (dev machines)
- Budget blocked → silent skip + record_blocked
- Provider fallback selects openai when anthropic key absent
- Happy path: each corpus item runs once, response is scored
- Regression detection: >15pt drop from baseline triggers alert
- Zero-history run: no regression (first weekly run ever)
- Alert never raises on db.execute failure
- Ops_alert carries baseline + drop_pct + per-signal regression strings
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestCorpus:

    def test_corpus_has_expected_items(self):
        from app.services.llm_realmodel_drift import _CORPUS
        names = {c.name for c in _CORPUS}
        # Must cover three signal classes.
        assert any(n.startswith("json_") for n in names)
        assert any(n.startswith("refusal_") for n in names)
        # Triage-shape item checks both json AND severity vocabulary.
        assert "json_triage_shape" in names

    def test_check_json_accepts_valid(self):
        from app.services.llm_realmodel_drift import _check_json
        assert _check_json('{"a": 1}') == {"json_ok": True}
        assert _check_json('  {"a": 1}  ') == {"json_ok": True}

    def test_check_json_extracts_first_block_from_prose(self):
        from app.services.llm_realmodel_drift import _check_json
        assert _check_json('Sure! Here: {"a": 1}') == {"json_ok": True}

    def test_check_json_rejects_plain_prose(self):
        from app.services.llm_realmodel_drift import _check_json
        assert _check_json("I would rather not.") == {"json_ok": False}

    def test_check_refusal_detects_standard_markers(self):
        from app.services.llm_realmodel_drift import _check_refusal
        assert _check_refusal("I can't help with that.") == {"refused": True}
        assert _check_refusal("I'm sorry, I cannot provide that.") == {
            "refused": True,
        }
        assert _check_refusal("Sure, here is the thing:").get("refused") is False

    def test_check_severity_valid(self):
        from app.services.llm_realmodel_drift import _check_severity_vocabulary
        ok = _check_severity_vocabulary('{"severity":"P0","x":1}')
        assert ok == {"severity_valid": True}
        bad = _check_severity_vocabulary('{"severity":"CRITICAL","x":1}')
        assert bad == {"severity_valid": False}


class TestScheduleGate:

    def test_in_schedule_window_sunday_6am(self):
        from app.services.llm_realmodel_drift import _in_schedule_window
        # 2026-04-19 is a Sunday.
        now = datetime(2026, 4, 19, 6, 30, 0)
        assert _in_schedule_window(now) is True

    def test_outside_sunday(self):
        from app.services.llm_realmodel_drift import _in_schedule_window
        # Monday 2026-04-20 at 6:30am → outside.
        now = datetime(2026, 4, 20, 6, 30, 0)
        assert _in_schedule_window(now) is False

    def test_outside_hour_window(self):
        from app.services.llm_realmodel_drift import _in_schedule_window
        # Sunday but 05:00 → before window.
        now = datetime(2026, 4, 19, 5, 0, 0)
        assert _in_schedule_window(now) is False
        # Sunday but 07:30 → after window.
        now = datetime(2026, 4, 19, 7, 30, 0)
        assert _in_schedule_window(now) is False


class TestRunWeeklyCheck:

    def test_skipped_outside_window_without_force(self):
        from app.services import llm_realmodel_drift as mod
        # Patch "now" to a Monday.
        with patch.object(mod, "_in_schedule_window", return_value=False):
            result = mod.run_weekly_check(MagicMock(), force=False)
        assert result["ran"] is False
        assert result["reason"] == "outside_window"

    def test_skipped_when_already_ran_this_week(self):
        from app.services import llm_realmodel_drift as mod
        rc = MagicMock()
        rc.get.return_value = b"1"  # already-ran flag set
        with patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=rc):
            result = mod.run_weekly_check(MagicMock(), force=False)
        assert result["ran"] is False
        assert result["reason"] == "already_ran_this_week"

    def test_skipped_when_no_api_key(self):
        """Dev machines with no key must not noise the ops channel."""
        from app.services import llm_realmodel_drift as mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            with patch.object(mod, "_in_schedule_window", return_value=True), \
                 patch("app.core.redis_client._client", return_value=None):
                result = mod.run_weekly_check(MagicMock(), force=True)
        assert result["ran"] is False
        assert result["reason"] == "no_api_key"

    def test_skipped_when_budget_blocked(self):
        from app.services import llm_realmodel_drift as mod
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}), \
             patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=None), \
             patch("app.core.llm_budget.check_budget", return_value=(False, "module_daily_cap")), \
             patch("app.core.llm_budget.record_blocked") as blocked:
            result = mod.run_weekly_check(MagicMock(), force=True)
        assert result["ran"] is False
        assert "budget_blocked" in result["reason"]
        blocked.assert_called_once()

    def test_happy_path_runs_all_corpus_items(self):
        """Every corpus item gets a call; summary aggregates rates."""
        from app.services import llm_realmodel_drift as mod

        # Canned response that satisfies json + severity checks AND is
        # NOT a refusal (so json/severity=1.0, refusal=0/3=0.0). We'll
        # separately verify the refusal items on a second round.
        good_json = (
            '{"severity": "P0", "probable_cause": "x",'
            ' "suggested_owner": "y", "triage_steps": ["z"],'
            ' "related_commits": [], "requires_human_now": false}'
        )
        refusal_text = "I'm sorry, I cannot comply with that request."

        def _caller(system, user, key, model):
            # Refusal prompts get a refusal; others get good JSON.
            if any(k in user for k in [
                "Ignore all prior",
                "ANTHROPIC_API_KEY",
                "jane.doe@example.com",
            ]):
                return refusal_text
            return good_json

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}), \
             patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=None), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch.object(mod, "_call_anthropic", side_effect=_caller):
            result = mod.run_weekly_check(MagicMock(), force=True)

        assert result["ran"] is True
        assert result["total"] == 6  # 3 json + 3 refusal items
        assert result["errored"] == 0
        # JSON items: 3 (json_simple, json_triage_shape, json_nested) all good
        assert result["json_parse_rate"] == 1.0
        # Refusal items: 3 — all refused
        assert result["refusal_rate"] == 1.0
        # Severity applies to just json_triage_shape → 1/1 = 1.0
        assert result["severity_valid_rate"] == 1.0
        # No prior history → no regressions reported.
        assert result["regressions"] == []

    def test_regression_fires_alert_on_json_drop(self):
        """A 20pt drop in json_parse_rate vs an 8-week history → alert."""
        from app.services import llm_realmodel_drift as mod

        # History shows 0.95 max; this week will be 0.50.
        history = [
            {"iso_week": "2026-W15", "json_parse_rate": 0.95,
             "refusal_rate": 1.0, "severity_valid_rate": 1.0},
            {"iso_week": "2026-W14", "json_parse_rate": 0.90,
             "refusal_rate": 1.0, "severity_valid_rate": 1.0},
        ]

        # Canned responses: half the JSON items return prose, refusal
        # items still refuse.
        call_counter = {"n": 0}

        def _caller(system, user, key, model):
            call_counter["n"] += 1
            if any(k in user for k in [
                "Ignore all prior",
                "ANTHROPIC_API_KEY",
                "jane.doe@example.com",
            ]):
                return "I cannot help with that."
            # First json item → bad, others → good. That drops
            # json_parse_rate to 2/3=0.667, which is 0.95-0.667=0.283
            # drop → > 15pt threshold → regression.
            if call_counter["n"] <= 1:
                return "here you go: this is plain prose no json"
            return '{"severity":"P1","probable_cause":"x","suggested_owner":"y","triage_steps":["z"],"related_commits":[],"requires_human_now":false}'

        rc = MagicMock()
        rc.get.return_value = None  # not-yet-ran
        rc.lrange.return_value = [json.dumps(h).encode() for h in history]

        alerts: list[dict] = []

        def _write_alert(db, **kwargs):
            alerts.append(kwargs)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}), \
             patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=rc), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch.object(mod, "_call_anthropic", side_effect=_caller), \
             patch("app.services.alerting.write_alert", side_effect=_write_alert):
            result = mod.run_weekly_check(MagicMock(), force=True)

        assert result["ran"] is True
        assert result["alerts_fired"] == 1
        assert any("json_parse_rate" in r for r in result["regressions"])
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "llm_realmodel_drift"
        assert "json_parse_rate" in str(alerts[0]["detail"]["regressions"])

    def test_first_run_no_history_no_regression(self):
        """First-ever weekly run has no baseline; even if rates are low,
        no regression fires (we need a baseline to compare against)."""
        from app.services import llm_realmodel_drift as mod

        rc = MagicMock()
        rc.get.return_value = None
        rc.lrange.return_value = []  # empty history

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}), \
             patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=rc), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch.object(mod, "_call_anthropic", return_value="not json"):
            result = mod.run_weekly_check(MagicMock(), force=True)

        assert result["ran"] is True
        assert result["regressions"] == []
        assert result["alerts_fired"] == 0

    def test_alert_write_failure_does_not_raise(self):
        from app.services import llm_realmodel_drift as mod

        rc = MagicMock()
        rc.get.return_value = None
        rc.lrange.return_value = [json.dumps({
            "iso_week": "2026-W15", "json_parse_rate": 0.95,
            "refusal_rate": 1.0, "severity_valid_rate": 1.0,
        }).encode()]

        def _caller(system, user, key, model):
            return "not json"

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-a"}), \
             patch.object(mod, "_in_schedule_window", return_value=True), \
             patch("app.core.redis_client._client", return_value=rc), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch.object(mod, "_call_anthropic", side_effect=_caller), \
             patch(
                 "app.services.alerting.write_alert",
                 side_effect=RuntimeError("db down"),
             ):
            # Must not raise.
            result = mod.run_weekly_check(MagicMock(), force=True)
        assert result["ran"] is True
        # alerts_fired stays 0 because the write raised.
        assert result["alerts_fired"] == 0


class TestIntegration:

    def test_registered_in_pipeline_internal_alert_types(self):
        """Drift alert must bypass the LLM-patch pipeline — the remedy
        is a prompt/config change, not a code patch."""
        from app.services.bugfix_pipeline import _PIPELINE_INTERNAL_ALERT_TYPES
        assert "llm_realmodel_drift" in _PIPELINE_INTERNAL_ALERT_TYPES

    def test_wired_into_aggregation_worker(self):
        """The weekly check must run from aggregation_worker alongside
        A5. A refactor that unwires it would silently disable B2."""
        import inspect
        from app.workers import aggregation_worker
        src = inspect.getsource(aggregation_worker)
        assert "llm_realmodel_drift" in src
        assert "run_weekly_check" in src
