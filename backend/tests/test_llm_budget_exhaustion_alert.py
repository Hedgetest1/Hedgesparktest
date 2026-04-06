"""LLM budget exhaustion alert — critical Telegram notification at 100%.

Verifies:
- Alert fires when global / per-provider cap is reached
- Dedup: exactly one alert per (scope, month)
- Block logs are explicit
- Telegram failure does not break budget enforcement
- get_llm_status returns correct tier (ACTIVE/LIMITED/DISABLED)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core import llm_budget


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate each test: clear in-process counters and neutralize Redis."""
    # Make caps deterministic for tests
    monkeypatch.setattr(llm_budget, "MONTHLY_EUR_CAP", 5.0)
    monkeypatch.setattr(llm_budget, "ANTHROPIC_MONTHLY_CAP", 3.0)
    monkeypatch.setattr(llm_budget, "OPENAI_MONTHLY_CAP", 2.0)

    # Force Redis helpers to return "empty" so in-process counters dominate
    monkeypatch.setattr(llm_budget, "_redis_get_float", lambda key: 0.0)
    monkeypatch.setattr(llm_budget, "_redis_get", lambda key: 0)
    monkeypatch.setattr(llm_budget, "_redis_incr", lambda key, ttl=86400: None)
    monkeypatch.setattr(
        llm_budget, "_redis_incrbyfloat",
        lambda key, amount, ttl=2678400: None,
    )

    # Pretend Redis is unavailable for cross-worker dedup (force in-process path)
    import app.core.redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: None)

    # Reset all in-process state + pin month
    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()
    llm_budget._month_key = llm_budget._this_month()
    yield
    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()


# ---------------------------------------------------------------------------
# 1) Global exhaustion → alert
# ---------------------------------------------------------------------------

def test_global_budget_exhaustion_triggers_alert():
    """Crossing global cap fires exactly one CRITICAL alert."""
    sent = []
    def _capture(msg, *a, **kw):
        if "LLM BUDGET EXHAUSTED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture), \
         patch("app.services.telegram_agent.send_message_with_buttons",
               side_effect=_capture):
        # Spend €4.99 — below cap
        llm_budget.record_usage(
            "orchestrator", tokens_used=1, provider="openai", model="gpt-4o-mini",
        )
        # Force spend over cap
        llm_budget._monthly_cost_eur = 5.5
        llm_budget._provider_cost_eur["openai"] = 1.0  # under provider cap
        llm_budget._last_call.pop("orchestrator", None)  # bypass cooldown
        # Next record triggers exhaustion check
        llm_budget.record_usage(
            "orchestrator", tokens_used=1, provider="openai", model="gpt-4o-mini",
        )

    assert len(sent) == 1, f"expected exactly 1 alert, got {len(sent)}"
    assert "LLM BUDGET EXHAUSTED" in sent[0]
    assert "SYSTEM DEGRADED" in sent[0]
    assert "LLM CALLS ARE NOW BLOCKED" in sent[0]
    assert "DEGRADED MODE" in sent[0]


# ---------------------------------------------------------------------------
# 2) Per-provider exhaustion → alert
# ---------------------------------------------------------------------------

def test_provider_budget_exhaustion_triggers_alert():
    """Anthropic cap reached fires provider-scoped alert."""
    sent = []
    def _capture(msg, *a, **kw):
        if "LLM BUDGET EXHAUSTED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture), \
         patch("app.services.telegram_agent.send_message_with_buttons",
               side_effect=_capture):
        # Push anthropic over its 3.0 cap while global stays under
        llm_budget._provider_cost_eur["anthropic"] = 3.5
        llm_budget._monthly_cost_eur = 3.5
        llm_budget.record_usage(
            "orchestrator", tokens_used=1, provider="anthropic",
            model="claude-sonnet-4-20250514",
        )

    assert len(sent) == 1
    assert "ANTHROPIC cap reached" in sent[0]
    assert "Anthropic: €3.5" in sent[0] or "Anthropic: \u20ac3.5" in sent[0]


# ---------------------------------------------------------------------------
# 3) Dedup — exactly one alert per month per scope
# ---------------------------------------------------------------------------

def test_alert_sent_only_once_per_month():
    """Repeat calls after exhaustion do NOT re-send the alert."""
    sent = []
    def _capture(msg, *a, **kw):
        if "LLM BUDGET EXHAUSTED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture), \
         patch("app.services.telegram_agent.send_message_with_buttons",
               side_effect=_capture):
        llm_budget._monthly_cost_eur = 6.0
        for _ in range(5):
            llm_budget._last_call.pop("orchestrator", None)
            llm_budget.record_usage(
                "orchestrator", tokens_used=1, provider="openai",
                model="gpt-4o-mini",
            )
            # Also trigger via check_budget
            llm_budget.check_budget("orchestrator")

    assert len(sent) == 1, f"dedup broken: sent={len(sent)}"


def test_no_duplicate_alerts_on_multiple_calls_via_check_budget():
    """check_budget() blocking path dedups alerts too."""
    sent = []
    def _capture(msg, *a, **kw):
        if "LLM BUDGET EXHAUSTED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture), \
         patch("app.services.telegram_agent.send_message_with_buttons",
               side_effect=_capture):
        llm_budget._monthly_cost_eur = 10.0
        for _ in range(10):
            allowed, reason = llm_budget.check_budget("bugfix_proposal")
            assert not allowed
            assert "monthly_eur_cap_reached" in reason

    assert len(sent) == 1


def test_global_and_provider_alerts_are_independent():
    """Global exhaustion and per-provider exhaustion dedup independently."""
    sent = []
    def _capture(msg, *a, **kw):
        if "LLM BUDGET EXHAUSTED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture), \
         patch("app.services.telegram_agent.send_message_with_buttons",
               side_effect=_capture):
        # First: anthropic provider cap hit (spend €3.5 of €3 cap, global still under)
        llm_budget._provider_cost_eur["anthropic"] = 3.5
        llm_budget._monthly_cost_eur = 3.5
        llm_budget.record_usage(
            "orchestrator", tokens_used=1, provider="anthropic",
            model="claude-sonnet-4-20250514",
        )
        # Then: global cap hit
        llm_budget._monthly_cost_eur = 6.0
        llm_budget._last_call.pop("orchestrator", None)
        llm_budget.record_usage(
            "orchestrator", tokens_used=1, provider="openai",
            model="gpt-4o-mini",
        )

    assert len(sent) == 2, f"expected 2 (one per scope), got {len(sent)}"
    assert any("ANTHROPIC cap reached" in m for m in sent)
    assert any("GLOBAL cap reached" in m for m in sent)


# ---------------------------------------------------------------------------
# 4) Block logs are explicit
# ---------------------------------------------------------------------------

def test_block_logs_are_correct(caplog):
    """check_budget emits explicit BLOCKED log with scope + amounts."""
    with patch("app.services.telegram_agent.is_configured", return_value=False):
        llm_budget._monthly_cost_eur = 10.0
        with caplog.at_level("WARNING", logger="llm_budget"):
            allowed, _ = llm_budget.check_budget("orchestrator")
        assert not allowed
        msgs = [r.message for r in caplog.records if r.name == "llm_budget"]
        assert any("BLOCKED — global budget exceeded" in m for m in msgs), msgs


def test_block_logs_provider_scope(caplog):
    """Per-provider block log names the provider."""
    with patch("app.services.telegram_agent.is_configured", return_value=False):
        llm_budget._provider_cost_eur["openai"] = 5.0  # over 2.0 cap
        with caplog.at_level("WARNING", logger="llm_budget"):
            allowed, reason = llm_budget.check_budget("orchestrator")
        assert not allowed
        assert "openai" in reason
        msgs = [r.message for r in caplog.records if r.name == "llm_budget"]
        assert any("BLOCKED — openai budget exceeded" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# 5) Fail-safe: telegram failure does not break budget enforcement
# ---------------------------------------------------------------------------

def test_telegram_failure_does_not_break_budget_enforcement():
    """send_message raising does NOT prevent block from returning False."""
    def boom(*a, **kw):
        raise RuntimeError("telegram API down")

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=boom):
        llm_budget._monthly_cost_eur = 10.0
        allowed, reason = llm_budget.check_budget("orchestrator")

    assert not allowed
    assert "monthly_eur_cap_reached" in reason


def test_alert_noop_when_telegram_not_configured():
    """No crash when telegram is not configured — alert is silently skipped."""
    with patch("app.services.telegram_agent.is_configured", return_value=False):
        llm_budget._monthly_cost_eur = 10.0
        allowed, _ = llm_budget.check_budget("orchestrator")
        assert not allowed
        # Should not raise


# ---------------------------------------------------------------------------
# 6) get_llm_status tiering
# ---------------------------------------------------------------------------

def test_llm_status_active():
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🟢"
    assert label == "ACTIVE"


def test_llm_status_limited_at_90pct():
    llm_budget._monthly_cost_eur = 4.6  # 92% of 5.0
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🟡"
    assert label == "LIMITED"


def test_llm_status_disabled_when_global_exhausted():
    llm_budget._monthly_cost_eur = 5.5
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🔴"
    assert "DISABLED" in label


def test_llm_status_disabled_when_provider_exhausted():
    """Provider cap reached → LLM status = DISABLED even if global is fine."""
    llm_budget._provider_cost_eur["anthropic"] = 3.5  # over 3.0 anthropic cap
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🔴"
    assert "DISABLED" in label
