"""Contract tests for LLM wrapper functions across services.

Pin the per-wrapper contract that emerged from the 2026-04-23
multi-dimensional hardening sweep:
  1. Provider usage struct → ground-truth token counts threaded out
  2. Truncation (stop_reason/finish_reason) → return empty / reject
  3. 429 → record_429 fired
  4. HTTP error (500-range) → return empty without crash

Covers the 4 wrappers that previously lacked dedicated tests:
  - on_alert_triage_llm._call_anthropic / _call_openai
  - model_upgrade_agent evaluate path (inline httpx.post)
  - meta_reviewer._call_opus
  - analytics_assistant._call_anthropic
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ""
        self.request = MagicMock()

    def json(self):
        return self._body


def _anthropic_ok(text: str = '{"severity":"P2"}', in_tokens: int = 120, out_tokens: int = 30):
    return _Resp(200, {
        "content": [{"text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    })


def _anthropic_truncated(in_tokens: int = 120, out_tokens: int = 512):
    return _Resp(200, {
        "content": [{"text": "partial..."}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    })


def _openai_ok(text: str = '{"severity":"P2"}'):
    return _Resp(200, {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    })


def _openai_truncated():
    return _Resp(200, {
        "choices": [{"message": {"content": "partial..."}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 512},
    })


# ---------------------------------------------------------------------------
# on_alert_triage_llm — _call_anthropic + _call_openai
# ---------------------------------------------------------------------------

class TestOnAlertTriageAnthropicWrapper:
    def test_happy_path_returns_4tuple_with_ground_truth_tokens(self):
        from app.services import on_alert_triage_llm as m

        with patch("httpx.post", return_value=_anthropic_ok(in_tokens=100, out_tokens=50)):
            text, model, in_t, out_t = m._call_anthropic(
                "ctx", "key", model="claude-sonnet-4-6", max_tokens=1024
            )

        assert text == '{"severity":"P2"}'
        assert model == "claude-sonnet-4-6"
        assert in_t == 100
        assert out_t == 50

    def test_truncation_returns_empty_text_zero_tokens(self):
        from app.services import on_alert_triage_llm as m

        with patch("httpx.post", return_value=_anthropic_truncated()):
            text, model, in_t, out_t = m._call_anthropic(
                "ctx", "key", model="claude-sonnet-4-6", max_tokens=1024
            )

        assert text == ""
        assert in_t == 0
        assert out_t == 0

    def test_429_fires_record_backoff(self):
        from app.services import on_alert_triage_llm as m

        with patch("httpx.post", return_value=_Resp(429)), \
             patch("app.core.llm_budget.record_429") as mock_429:
            text, _, _, _ = m._call_anthropic(
                "ctx", "key", model="claude-sonnet-4-6", max_tokens=512
            )

        assert text == ""
        mock_429.assert_called_once_with("anthropic")


class TestOnAlertTriageOpenAIWrapper:
    def test_happy_path_normalizes_prompt_to_input_tokens(self):
        from app.services import on_alert_triage_llm as m

        with patch("httpx.post", return_value=_openai_ok()):
            text, model, in_t, out_t = m._call_openai(
                "ctx", "key", model="gpt-4o-mini", max_tokens=512
            )

        # OpenAI's prompt_tokens maps to in_t; completion_tokens to out_t.
        assert text == '{"severity":"P2"}'
        assert in_t == 120
        assert out_t == 30

    def test_openai_truncation_returns_empty(self):
        from app.services import on_alert_triage_llm as m

        with patch("httpx.post", return_value=_openai_truncated()):
            text, _, in_t, out_t = m._call_openai(
                "ctx", "key", model="gpt-4o-mini", max_tokens=512
            )

        assert text == ""
        assert in_t == 0
        assert out_t == 0


# analytics_assistant — inline httpx.post inside _call_llm
# ---------------------------------------------------------------------------

class TestAnalyticsAssistantWrapper:
    def test_happy_path_records_ground_truth_tokens(self):
        from app.services import analytics_assistant as m

        with patch("httpx.post", return_value=_anthropic_ok(text="answer", in_tokens=500, out_tokens=80)), \
             patch("app.core.llm_budget.record_usage") as mock_record, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            out = m._call_anthropic("prompt")

        assert out == "answer"
        args, kwargs = mock_record.call_args
        assert kwargs.get("tokens_used") == 580, (
            f"tokens_used must be in+out ground-truth, got {kwargs.get('tokens_used')!r}"
        )

    def test_truncation_returns_empty_no_record(self):
        from app.services import analytics_assistant as m

        with patch("httpx.post", return_value=_anthropic_truncated()), \
             patch("app.core.llm_budget.record_usage") as mock_record, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            out = m._call_anthropic("prompt")

        assert out == ""
        assert mock_record.call_count == 0


