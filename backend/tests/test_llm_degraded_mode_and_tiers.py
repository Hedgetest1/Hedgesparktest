"""Priority tiers, degraded-mode flag, /llm_mode override, both-providers-failed alert.

Covers the CTO directive hardening:
- TIER_CRITICAL survives budget pressure
- TIER_OPTIONAL blocked at <20% remaining
- TIER_IMPORTANT blocked at <10% remaining
- /llm_mode off|limited|full override honored
- is_llm_disabled() flag reflects reality
- alert_both_providers_failed dedups per hour per module
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core import llm_budget


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(llm_budget, "MONTHLY_EUR_CAP", 10.0)
    monkeypatch.setattr(llm_budget, "ANTHROPIC_MONTHLY_CAP", 10.0)
    monkeypatch.setattr(llm_budget, "OPENAI_MONTHLY_CAP", 10.0)

    monkeypatch.setattr(llm_budget, "_redis_get_float", lambda key: 0.0)
    monkeypatch.setattr(llm_budget, "_redis_get", lambda key: 0)
    monkeypatch.setattr(llm_budget, "_redis_incr", lambda key, ttl=86400: None)
    monkeypatch.setattr(
        llm_budget, "_redis_incrbyfloat",
        lambda key, amount, ttl=2678400: None,
    )

    # Force mode_override to "full" by returning None from Redis
    import app.core.redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: None)

    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()
    llm_budget._both_failed_alert_sent.clear()
    llm_budget._month_key = llm_budget._this_month()
    yield
    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()
    llm_budget._both_failed_alert_sent.clear()


# ---------------------------------------------------------------------------
# Priority-tier gating under budget pressure
# ---------------------------------------------------------------------------

def test_critical_tier_survives_when_optional_blocked():
    """At 85% spend (15% remaining), optional blocked, critical runs."""
    llm_budget._monthly_cost_eur = 8.5  # 15% remaining

    allowed_crit, _ = llm_budget.check_budget("orchestrator")
    allowed_opt, reason_opt = llm_budget.check_budget("nudge_composer")

    assert allowed_crit, "orchestrator (CRITICAL) must survive at 15% remaining"
    assert not allowed_opt, "nudge_composer (OPTIONAL) must be blocked at <20%"
    assert "tier_blocked" in reason_opt
    assert "optional" in reason_opt


def test_important_tier_blocked_below_10pct():
    """At 93% spend (7% remaining), important tier blocked."""
    llm_budget._monthly_cost_eur = 9.3  # 7% remaining
    llm_budget._last_call.pop("bugfix_proposal", None)

    allowed_crit, _ = llm_budget.check_budget("orchestrator")
    allowed_imp, reason_imp = llm_budget.check_budget("bugfix_proposal")

    assert allowed_crit, "critical must still run at 7%"
    assert not allowed_imp, "bugfix_proposal (IMPORTANT) must be blocked at <10%"
    assert "tier_blocked" in reason_imp
    assert "important" in reason_imp


def test_all_tiers_allowed_when_budget_fresh():
    """At 0% spend, all tiers pass the tier gate."""
    llm_budget._monthly_cost_eur = 0.0

    for module in ("orchestrator", "bugfix_proposal", "nudge_composer"):
        llm_budget._last_call.pop(module, None)
        allowed, reason = llm_budget.check_budget(module)
        assert allowed, f"{module} should be allowed at 0% spend, got {reason}"


# ---------------------------------------------------------------------------
# Operator /llm_mode override
# ---------------------------------------------------------------------------

def test_mode_off_blocks_non_critical(monkeypatch):
    """mode=off → optional + important blocked; critical runs."""
    monkeypatch.setattr(llm_budget, "_get_mode_override", lambda: "off")

    allowed_crit, _ = llm_budget.check_budget("orchestrator")
    allowed_imp, reason_imp = llm_budget.check_budget("bugfix_proposal")
    allowed_opt, reason_opt = llm_budget.check_budget("nudge_composer")

    assert allowed_crit
    assert not allowed_imp
    assert not allowed_opt
    assert "mode_off" in reason_imp
    assert "mode_off" in reason_opt


def test_mode_limited_blocks_only_optional(monkeypatch):
    """mode=limited → optional blocked; important + critical run."""
    monkeypatch.setattr(llm_budget, "_get_mode_override", lambda: "limited")
    llm_budget._last_call.pop("bugfix_proposal", None)

    allowed_crit, _ = llm_budget.check_budget("orchestrator")
    allowed_imp, _ = llm_budget.check_budget("bugfix_proposal")
    allowed_opt, reason_opt = llm_budget.check_budget("nudge_composer")

    assert allowed_crit
    assert allowed_imp
    assert not allowed_opt
    assert "mode_limited" in reason_opt


def test_is_llm_disabled_when_global_exhausted():
    llm_budget._monthly_cost_eur = 15.0
    assert llm_budget.is_llm_disabled() is True


def test_is_llm_disabled_when_mode_off(monkeypatch):
    monkeypatch.setattr(llm_budget, "_get_mode_override", lambda: "off")
    assert llm_budget.is_llm_disabled() is True


def test_is_llm_disabled_false_when_healthy():
    assert llm_budget.is_llm_disabled() is False


# ---------------------------------------------------------------------------
# get_llm_status reflects mode override
# ---------------------------------------------------------------------------

def test_llm_status_off_mode(monkeypatch):
    monkeypatch.setattr(llm_budget, "_get_mode_override", lambda: "off")
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🔴"
    assert "operator" in label.lower()


def test_llm_status_limited_mode(monkeypatch):
    monkeypatch.setattr(llm_budget, "_get_mode_override", lambda: "limited")
    emoji, label = llm_budget.get_llm_status()
    assert emoji == "🟡"
    assert "operator" in label.lower()


# ---------------------------------------------------------------------------
# alert_both_providers_failed — dedup per hour per module
# ---------------------------------------------------------------------------

def test_both_providers_failed_alert_fires_once_per_hour():
    sent = []

    def _capture(msg, *a, **kw):
        if "BOTH LLM PROVIDERS FAILED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture):
        for _ in range(5):
            llm_budget.alert_both_providers_failed(
                module="orchestrator",
                anthropic_error="timeout",
                openai_error="500",
            )

    assert len(sent) == 1, f"dedup broken: sent={len(sent)}"
    assert "orchestrator" in sent[0]
    assert "timeout" in sent[0]


def test_both_providers_failed_alerts_different_modules_independently():
    sent = []

    def _capture(msg, *a, **kw):
        if "BOTH LLM PROVIDERS FAILED" in msg:
            sent.append(msg)
        return True

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture):
        llm_budget.alert_both_providers_failed("orchestrator", "x", "y")
        llm_budget.alert_both_providers_failed("bugfix_proposal", "x", "y")

    assert len(sent) == 2


def test_both_providers_failed_telegram_error_is_safe():
    """Telegram failure MUST NOT raise from alert_both_providers_failed."""
    def boom(*a, **kw):
        raise RuntimeError("tg down")

    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=boom):
        # Should not raise
        llm_budget.alert_both_providers_failed("orchestrator", "x", "y")


# ---------------------------------------------------------------------------
# set_mode_override — operator command path (with mocked redis)
# ---------------------------------------------------------------------------

def test_set_mode_override_validation(monkeypatch):
    """Invalid mode rejected, valid modes accepted."""
    # Inject fake redis client
    class FakeRedis:
        def __init__(self):
            self.store = {}
        def set(self, k, v, ex=None):
            self.store[k] = v
            return True
        def get(self, k):
            return self.store.get(k)

    fake = FakeRedis()
    import app.core.redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: fake)

    assert llm_budget.set_mode_override("bogus") is False
    assert llm_budget.set_mode_override("off") is True
    assert llm_budget._get_mode_override() == "off"
    assert llm_budget.set_mode_override("limited") is True
    assert llm_budget._get_mode_override() == "limited"
    assert llm_budget.set_mode_override("full") is True
    assert llm_budget._get_mode_override() == "full"
