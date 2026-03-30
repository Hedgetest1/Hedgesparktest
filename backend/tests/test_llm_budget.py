"""Tests for LLM budget guard."""
import os
import time

from app.core.llm_budget import (
    check_budget,
    record_usage,
    record_blocked,
    get_usage_summary,
    get_max_tokens,
    reset_daily_counters,
    BUDGET_LIMITS,
    GLOBAL_MAX_CALLS_PER_DAY,
    _last_call,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def setup_function():
    reset_daily_counters()


# ---------------------------------------------------------------------------
# Basic budget checks
# ---------------------------------------------------------------------------

def test_first_call_allowed():
    """First call to any module is allowed."""
    allowed, reason = check_budget("orchestrator")
    assert allowed is True
    assert reason == "allowed"


def test_daily_limit_blocks():
    """Exceeding daily limit blocks the call."""
    reset_daily_counters()
    limit = BUDGET_LIMITS["orchestrator"]["max_calls_per_day"]
    for _ in range(limit):
        record_usage("orchestrator", tokens_used=100)
    _last_call.clear()  # clear cooldown so we test daily limit specifically
    allowed, reason = check_budget("orchestrator")
    assert allowed is False
    assert "daily_limit" in reason


def test_cooldown_blocks():
    """Call within cooldown window is blocked."""
    reset_daily_counters()
    record_usage("orchestrator")
    # _last_call is set by record_usage, so cooldown is active
    allowed, reason = check_budget("orchestrator")
    assert allowed is False
    assert "cooldown" in reason


def test_different_modules_independent():
    """Limits are per-module, not shared."""
    reset_daily_counters()
    record_usage("orchestrator")
    _last_call.clear()
    allowed, reason = check_budget("bugfix_proposal")
    assert allowed is True


def test_global_limit_blocks():
    """Global daily cap blocks all modules."""
    reset_daily_counters()
    # Use a module with high per-module limit to reach global first
    for i in range(GLOBAL_MAX_CALLS_PER_DAY):
        record_usage(f"test_mod_{i % 10}", tokens_used=10)
    _last_call.clear()
    allowed, reason = check_budget("orchestrator")
    assert allowed is False
    assert "global" in reason or "daily_limit" in reason


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def test_record_usage_increments():
    """record_usage increments daily counter."""
    reset_daily_counters()
    record_usage("orchestrator", tokens_used=200, provider="openai", model="gpt-4o-mini")
    summary = get_usage_summary()
    assert summary["modules"]["orchestrator"]["calls_today"] == 1
    assert summary["modules"]["orchestrator"]["tokens_today"] == 200


def test_blocked_count_increments():
    """record_blocked increments blocked counter."""
    reset_daily_counters()
    initial = get_usage_summary()["blocked_today"]
    record_blocked("test", "test_reason")
    after = get_usage_summary()["blocked_today"]
    assert after == initial + 1


def test_max_tokens_returns_correct_value():
    """get_max_tokens returns per-module limit."""
    assert get_max_tokens("orchestrator") == 512
    assert get_max_tokens("bugfix_proposal") == 2048
    assert get_max_tokens("unknown_module") == 1024  # default


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_budget_endpoint(client):
    """GET /ops/llm-budget returns summary."""
    reset_daily_counters()
    record_usage("orchestrator", tokens_used=50)
    resp = client.get("/ops/llm-budget", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "global_calls_today" in data
    assert "modules" in data
    assert "orchestrator" in data["modules"]


def test_budget_endpoint_requires_auth(client):
    """Budget endpoint requires operator auth."""
    resp = client.get("/ops/llm-budget")
    assert resp.status_code == 401
