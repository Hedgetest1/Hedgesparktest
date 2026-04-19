"""Tests for the B1 triage-LLM call site.

Locks the contract that:
- Missing API keys → None (no raise)
- Budget blocked → None + record_blocked invoked
- PII in context → None (fail-closed)
- Successful Anthropic call → TriageVerdict parsed from JSON
- Fenced JSON (```json ... ```) still parses
- Prose-wrapped JSON still parses (first {...} block)
- Invalid severity in response → None (strict validation)
- Response parse failure → None
- Anthropic 429 → openai fallback attempted
- Never raises
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def _fake_packet():
    return {
        "alert": {
            "id": 101,
            "created_at": "2026-04-19T09:00:00",
            "severity": "critical",
            "alert_type": "track_endpoint_500",
            "shop_domain": None,
            "summary": "tracker POST /track 500 rate 12% last 10m",
            "detail": {"p95_ms": 1200, "error_rate": 0.12},
        },
        "related_alerts_48h": [
            {"id": 100, "created_at": "2026-04-18T20:00", "severity": "warning",
             "resolved": True, "summary": "p95 slow on /track"},
        ],
        "recent_commits_48h": [
            "abc123 feat(track): rewrite batch logic",
            "def456 fix(track): revert batch rewrite",
        ],
        "worker_errors_6h": [],
    }


def _good_anthropic_response(severity="P1", requires_human_now=False):
    return {
        "status_code": 200,
        "json": {
            "content": [{
                "text": (
                    '{"severity": "' + severity + '",'
                    '"probable_cause": "batch logic regression",'
                    '"suggested_owner": "track_pipeline",'
                    '"triage_steps": ["check abc123 diff", "rollback batch rewrite"],'
                    '"related_commits": ["abc123", "def456"],'
                    '"requires_human_now": ' + ("true" if requires_human_now else "false") + '}'
                ),
            }],
        },
    }


def _fake_httpx_response(spec: dict):
    r = MagicMock()
    r.status_code = spec["status_code"]
    r.json.return_value = spec.get("json", {})
    return r


class TestTriage:

    def test_no_api_key_returns_none(self):
        from app.services.on_alert_triage_llm import triage
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            v = triage(_fake_packet())
        assert v is None

    def test_budget_blocked_returns_none(self):
        from app.services import on_alert_triage_llm as mod
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(False, "module_daily_cap")), \
             patch("app.core.llm_budget.record_blocked") as blocked:
            v = mod.triage(_fake_packet())
        assert v is None
        blocked.assert_called_once()
        args, _ = blocked.call_args
        assert args[0] == "on_alert_responder"

    def test_pii_in_context_fails_closed(self):
        """If the PII guard flags a violation, triage returns None.
        The packet is read-only context so this should be extremely
        rare — but the guard exists to keep merchant PII from ever
        reaching the model."""
        from app.services import on_alert_triage_llm as mod
        from app.core.llm_pii_guard import LLMPayloadViolation

        def _raise_pii(*a, **k):
            raise LLMPayloadViolation("email_detected")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_pii_guard.assert_clean", side_effect=_raise_pii):
            v = mod.triage(_fake_packet())
        assert v is None

    def test_happy_path_parses_verdict(self):
        from app.services import on_alert_triage_llm as mod
        response = _fake_httpx_response(_good_anthropic_response("P0", True))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("httpx.post", return_value=response):
            v = mod.triage(_fake_packet())

        assert v is not None
        assert v.severity == "P0"
        assert v.probable_cause == "batch logic regression"
        assert v.suggested_owner == "track_pipeline"
        assert "check abc123 diff" in v.triage_steps
        assert "abc123" in v.related_commits
        assert v.requires_human_now is True
        assert "claude" in v.model_used.lower()

    def test_fenced_json_response_still_parses(self):
        """Models sometimes wrap JSON in ```json ... ``` fences.
        The parser must tolerate that shape."""
        from app.services import on_alert_triage_llm as mod
        fenced = (
            "```json\n"
            '{"severity": "P2", "probable_cause": "minor",'
            '"suggested_owner": "ops", "triage_steps": ["wait"],'
            '"related_commits": [], "requires_human_now": false}\n'
            "```"
        )
        response = _fake_httpx_response({
            "status_code": 200,
            "json": {"content": [{"text": fenced}]},
        })
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("httpx.post", return_value=response):
            v = mod.triage(_fake_packet())
        assert v is not None
        assert v.severity == "P2"

    def test_prose_wrapped_json_still_parses(self):
        """If the model prepends prose ('Sure, here is ...') the parser
        extracts the first {...} block. Defensive against future
        prompt-following drift."""
        from app.services import on_alert_triage_llm as mod
        wrapped = (
            "Here is my assessment:\n\n"
            '{"severity": "P1", "probable_cause": "x",'
            '"suggested_owner": "y", "triage_steps": ["z"],'
            '"related_commits": [], "requires_human_now": false}'
        )
        response = _fake_httpx_response({
            "status_code": 200,
            "json": {"content": [{"text": wrapped}]},
        })
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("httpx.post", return_value=response):
            v = mod.triage(_fake_packet())
        assert v is not None
        assert v.severity == "P1"

    def test_invalid_severity_in_response_returns_none(self):
        """Model returning severity='critical' or 'severe' must be
        rejected — we only accept the strict P0/P1/P2 vocabulary so
        downstream consumers (Telegram ping decision) don't drift."""
        from app.services import on_alert_triage_llm as mod
        response = _fake_httpx_response({
            "status_code": 200,
            "json": {"content": [{"text": (
                '{"severity": "CRITICAL", "probable_cause": "x",'
                '"suggested_owner": "y", "triage_steps": ["z"],'
                '"related_commits": [], "requires_human_now": false}'
            )}]},
        })
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("httpx.post", return_value=response):
            v = mod.triage(_fake_packet())
        assert v is None

    def test_anthropic_429_falls_back_to_openai(self):
        """On 429 from anthropic, the call site must try openai next."""
        from app.services import on_alert_triage_llm as mod
        responses = [
            _fake_httpx_response({"status_code": 429, "json": {}}),  # anthropic
            _fake_httpx_response({  # openai success
                "status_code": 200,
                "json": {
                    "choices": [{"message": {"content": (
                        '{"severity": "P2", "probable_cause": "minor",'
                        '"suggested_owner": "ops", "triage_steps": ["check"],'
                        '"related_commits": [], "requires_human_now": false}'
                    )}}],
                },
            }),
        ]
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-a",
            "OPENAI_API_KEY": "sk-o",
        }), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("app.core.llm_budget.record_429"), \
             patch("httpx.post", side_effect=responses):
            v = mod.triage(_fake_packet())
        assert v is not None
        assert v.severity == "P2"

    def test_unparseable_response_returns_none(self):
        """Non-JSON garbage from the model returns None, not a crash."""
        from app.services import on_alert_triage_llm as mod
        response = _fake_httpx_response({
            "status_code": 200,
            "json": {"content": [{"text": "this is not json at all"}]},
        })
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("app.core.llm_budget.record_usage"), \
             patch("httpx.post", return_value=response):
            v = mod.triage(_fake_packet())
        assert v is None

    def test_httpx_raise_returns_none(self):
        """Network failure returns None, never raises."""
        from app.services import on_alert_triage_llm as mod
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
             patch("app.core.llm_budget.is_provider_backed_off", return_value=False), \
             patch("httpx.post", side_effect=Exception("network down")):
            v = mod.triage(_fake_packet())
        assert v is None


class TestContextFormat:

    def test_format_context_includes_alert_and_commits(self):
        from app.services.on_alert_triage_llm import _format_context
        body = _format_context(_fake_packet())
        assert "track_endpoint_500" in body
        assert "abc123 feat(track)" in body
        assert "Related alerts" in body

    def test_format_context_truncates_long_body(self):
        """Budget-safe: context never exceeds _MAX_CTX_CHARS by much."""
        from app.services.on_alert_triage_llm import _format_context
        huge = {"alert": {
            "id": 1, "created_at": "x", "severity": "critical",
            "alert_type": "t", "shop_domain": None,
            "summary": "a" * 50000, "detail": "b" * 50000,
        }}
        body = _format_context(huge)
        # The truncation keeps body within MAX_CTX_CHARS plus a marker.
        assert "context truncated" in body
        assert len(body) < 7000
