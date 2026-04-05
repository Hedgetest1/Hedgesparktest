"""
Tests for protection_state.py — unified degradation signal.

Verifies that the system reports correct protection levels under
simulated pressure, and that convenience helpers return the right
skip/reduce decisions.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core import protection_state as ps


@pytest.fixture(autouse=True)
def _reset_cache():
    ps.invalidate_cache()
    yield
    ps.invalidate_cache()


def test_level_ok_when_all_subsystems_healthy():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "OK"
    assert state["degraded_subsystems"] == []
    assert state["protective_actions"] == []


def test_level_degraded_when_llm_at_80pct():
    with patch.object(ps, "_llm_pressure", return_value=("degraded", {"ratio": 0.82})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "DEGRADED"
    assert "llm" in state["degraded_subsystems"]
    assert "skip_optional_llm_calls" in state["protective_actions"]


def test_level_critical_when_llm_exhausted():
    with patch.object(ps, "_llm_pressure", return_value=("critical", {"ratio": 1.0})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "CRITICAL"
    assert "skip_all_optional_llm_calls" in state["protective_actions"]


def test_redis_down_marks_degraded_with_fallback_action():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("degraded", {"reason": "ping_failed"})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "DEGRADED"
    assert "redis" in state["degraded_subsystems"]
    assert "use_db_fallback_for_caches" in state["protective_actions"]


def test_db_pool_saturation_escalates_to_critical_at_90pct():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("critical", {"ratio": 0.92})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "CRITICAL"
    assert "skip_non_critical_db_queries" in state["protective_actions"]


def test_db_pool_70pct_is_degraded_not_critical():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("degraded", {"ratio": 0.72})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert state["level"] == "DEGRADED"
    assert "reduce_batch_sizes" in state["protective_actions"]


def test_two_stale_workers_escalates_to_critical():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("critical", {"stale_workers": ["agent_worker(5000s)", "aggregation_worker(1200s)"]})):
        state = ps.protection_state()
    assert state["level"] == "CRITICAL"
    assert "skip_non_critical_jobs" in state["protective_actions"]


def test_cache_prevents_repeated_computation():
    calls = {"n": 0}
    def fake_llm():
        calls["n"] += 1
        return ("ok", {})
    with patch.object(ps, "_llm_pressure", side_effect=fake_llm), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        ps.protection_state()
        ps.protection_state()
        ps.protection_state()
    assert calls["n"] == 1  # cached after first call


def test_invalidate_cache_forces_recompute():
    calls = {"n": 0}
    def fake_llm():
        calls["n"] += 1
        return ("ok", {})
    with patch.object(ps, "_llm_pressure", side_effect=fake_llm), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        ps.protection_state()
        ps.invalidate_cache()
        ps.protection_state()
    assert calls["n"] == 2


def test_should_skip_optional_llm_true_when_budget_pressured():
    with patch.object(ps, "_llm_pressure", return_value=("degraded", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        assert ps.should_skip_optional_llm() is True


def test_should_skip_optional_llm_false_when_ok():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        assert ps.should_skip_optional_llm() is False


def test_should_reduce_batch_true_when_any_degraded():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("degraded", {})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        assert ps.should_reduce_batch() is True


def test_protection_state_includes_timestamp_and_subsystem_breakdown():
    with patch.object(ps, "_llm_pressure", return_value=("ok", {"spent_eur": 1.2})), \
         patch.object(ps, "_redis_pressure", return_value=("ok", {})), \
         patch.object(ps, "_db_pool_pressure", return_value=("ok", {"in_use": 3})), \
         patch.object(ps, "_worker_pressure", return_value=("ok", {})):
        state = ps.protection_state()
    assert "checked_at" in state
    assert state["subsystems"]["llm"]["level"] == "ok"
    assert state["subsystems"]["llm"]["spent_eur"] == 1.2
    assert state["subsystems"]["db_pool"]["in_use"] == 3
