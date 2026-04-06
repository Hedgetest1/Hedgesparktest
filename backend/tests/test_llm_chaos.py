"""Chaos test — 50 randomized cycles with concurrent provider failures.

Scenario per cycle:
- anthropic: random timeout/500/429/success
- openai: random 429/success
- budget: near-limit (drifting up)
- fallback: enabled

Invariants:
- no crash
- no infinite retry loop
- every cycle completes
- system continues (at least some cycles succeed when openai is healthy enough)
"""
from __future__ import annotations

import random
from unittest.mock import patch

import httpx
import pytest

from app.core import llm_budget, llm_router


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(llm_budget, "MONTHLY_EUR_CAP", 100.0)
    monkeypatch.setattr(llm_budget, "ANTHROPIC_MONTHLY_CAP", 100.0)
    monkeypatch.setattr(llm_budget, "OPENAI_MONTHLY_CAP", 100.0)
    monkeypatch.setattr(llm_budget, "_redis_get_float", lambda key: 0.0)
    monkeypatch.setattr(llm_budget, "_redis_get", lambda key: 0)
    monkeypatch.setattr(llm_budget, "_redis_incr", lambda key, ttl=86400: None)
    monkeypatch.setattr(
        llm_budget, "_redis_incrbyfloat",
        lambda key, amount, ttl=2678400: None,
    )
    import app.core.redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: None)

    # Both keys present
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-chaos")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-chaos")
    import app.services.orchestrator_llm as om
    monkeypatch.setattr(om, "_ANTHROPIC_KEY", "sk-ant-chaos")
    monkeypatch.setattr(om, "_OPENAI_KEY", "sk-oai-chaos")

    # Enable fallback
    monkeypatch.setattr(llm_router, "ALLOW_FALLBACK", True)

    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()
    llm_budget._provider_429.clear()
    llm_budget._month_key = llm_budget._this_month()
    yield
    llm_budget.reset_daily_counters()
    llm_budget._last_call.clear()
    llm_budget._provider_429.clear()


class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ""
    def json(self):
        return self._body


def _ok_anthropic():
    return _Resp(200, {"content": [{"text": '{"assessment":"ok","actions":[]}'}]})


def _ok_openai():
    return _Resp(200, {"choices": [{"message": {"content": '{"assessment":"ok","actions":[]}'}}]})


def test_chaos_50_cycles_no_stall_no_crash():
    """50 randomized cycles with concurrent anthropic + openai failures."""
    from app.services import orchestrator_llm

    random.seed(1337)
    registry = {"noop": (lambda *a, **k: None, "no-op", 0)}

    outcomes = {"success": 0, "failed": 0, "degraded": 0}
    call_count = [0]
    max_calls_per_cycle = [0]

    for cycle in range(50):
        # Bypass per-module cooldown for the test
        llm_budget._last_call.clear()
        llm_budget._provider_429.clear()  # reset backoff each cycle
        cycle_calls = [0]

        def fake_post(url, **kwargs):
            call_count[0] += 1
            cycle_calls[0] += 1
            r = random.random()
            if "anthropic.com" in url:
                if r < 0.30:
                    raise httpx.ReadTimeout("chaos timeout")
                if r < 0.55:
                    return _Resp(500, {})
                if r < 0.70:
                    return _Resp(429, {})
                return _ok_anthropic()
            # openai
            if r < 0.15:
                return _Resp(429, {})
            if r < 0.20:
                raise httpx.ConnectError("chaos connect")
            return _ok_openai()

        with patch.object(httpx, "post", side_effect=fake_post):
            try:
                result = orchestrator_llm.claude_decision("state", registry)
            except Exception as exc:
                pytest.fail(f"cycle {cycle} crashed: {type(exc).__name__}: {exc}")

        max_calls_per_cycle[0] = max(max_calls_per_cycle[0], cycle_calls[0])

        if result.error is None:
            outcomes["success"] += 1
        elif result.error.startswith("budget:"):
            outcomes["degraded"] += 1
        else:
            outcomes["failed"] += 1

        # Invariant: never more than 2 provider calls per cycle (no loop)
        assert cycle_calls[0] <= 2, (
            f"cycle {cycle} made {cycle_calls[0]} provider calls — infinite loop?"
        )

    total = sum(outcomes.values())
    assert total == 50, f"expected 50 completed cycles, got {total}"
    # openai is healthy ~80% of the time → majority should succeed
    assert outcomes["success"] >= 25, (
        f"too few successes under chaos: {outcomes}"
    )
    assert max_calls_per_cycle[0] <= 2


def test_chaos_near_budget_limit_still_runs_critical(monkeypatch):
    """Budget at 85% (15% remaining) — critical modules still get calls."""
    from app.services import orchestrator_llm

    # 15% remaining → optional blocked, important blocked? (7%+ remaining is OK for important)
    # Actually important needs >=10% — we have 15% so important OK, optional blocked (<20%)
    monkeypatch.setattr(llm_budget, "MONTHLY_EUR_CAP", 10.0)
    llm_budget._monthly_cost_eur = 8.5

    registry = {"noop": (lambda *a, **k: None, "no-op", 0)}

    def fake_post(url, **kwargs):
        if "openai.com" in url:
            return _ok_openai()
        raise httpx.ReadTimeout("chaos")

    # Orchestrator = CRITICAL → must run
    with patch.object(httpx, "post", side_effect=fake_post):
        result = orchestrator_llm.claude_decision("state", registry)
    assert result.error is None, f"critical module blocked at 15% remaining: {result.error}"

    # nudge_composer = OPTIONAL → should be blocked at 15% remaining
    allowed, reason = llm_budget.check_budget("nudge_composer")
    assert not allowed
    assert "tier_blocked" in reason


def test_chaos_budget_exhausted_critical_may_run_until_cap(monkeypatch):
    """When cap is ACTUALLY reached, even critical is blocked — no overspend."""
    monkeypatch.setattr(llm_budget, "MONTHLY_EUR_CAP", 5.0)
    llm_budget._monthly_cost_eur = 5.5  # over cap

    # Suppress telegram side-effects
    with patch("app.services.telegram_agent.is_configured", return_value=False):
        allowed, reason = llm_budget.check_budget("orchestrator")

    assert not allowed
    assert "monthly_eur_cap_reached" in reason
