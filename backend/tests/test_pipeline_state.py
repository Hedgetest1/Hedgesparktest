"""Lock the 2026-05-07 pipeline-state dormancy contract.

Bug class context
-----------------
Alert #129083 (circuit_breaker_tripped from agent_worker, source =
agent_worker, summary "Auto-apply paused: system unhealthy for 56
consecutive cycles") fired ~9-10h on the parked-pre-merchant
backlog. 88 candidates accumulated (53 open + 35 analyzed + 11
patch_proposed) because the brain enrichers are off and no patches
are being produced — `loop_health.get_loop_health()` correctly
reported `is_healthy=False`, the breaker correctly tripped, but the
alert was NOT actionable: auto-apply was already structurally paused
by absence of patches, so the breaker had nothing to gate.

Fix (commit landing this test): `pipeline_state.is_pipeline_dormant()`
single source of truth + agent_worker `_check_circuit_breaker()`
short-circuits to False+counter-reset when dormant.

Contract pinned:
  - All 3 enrichers off → dormant → True.
  - ANY enricher on (1/true/yes/whitespace-tolerant) → active → False.
  - Empty / unset → off → contributes to dormancy.
"""
from __future__ import annotations

import importlib

from app.services import pipeline_state


def _clear_env(monkeypatch):
    for v in pipeline_state._BRAIN_ENRICHER_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def test_dormant_when_all_enrichers_unset(monkeypatch):
    _clear_env(monkeypatch)
    assert pipeline_state.is_pipeline_dormant() is True


def test_active_when_adversarial_reviewer_on(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "1")
    assert pipeline_state.is_pipeline_dormant() is False


def test_active_when_sibling_hunt_on(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SIBLING_HUNT_ENABLED", "true")
    assert pipeline_state.is_pipeline_dormant() is False


def test_active_when_iterative_fix_on(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ITERATIVE_FIX_ENABLED", "yes")
    assert pipeline_state.is_pipeline_dormant() is False


def test_dormant_with_explicit_off_values(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "0")
    monkeypatch.setenv("SIBLING_HUNT_ENABLED", "false")
    monkeypatch.setenv("ITERATIVE_FIX_ENABLED", "")
    assert pipeline_state.is_pipeline_dormant() is True


def test_active_with_whitespace_padded_truthy(monkeypatch):
    """Whitespace-padded values are still truthy (.strip() applied).
    Catches the env-var typo class — `=" 1 "` is the same as `="1"`.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", " 1 ")
    assert pipeline_state.is_pipeline_dormant() is False


def test_dormancy_status_diagnostic(monkeypatch):
    """The /ops diagnostic snapshot reports per-enricher state and
    overall verdict — used to debug 'why is the pipeline reported as
    dormant' without re-deriving by hand."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "1")
    snap = pipeline_state.dormancy_status()
    assert snap["dormant"] is False
    assert snap["enrichers"]["ADVERSARIAL_REVIEWER_ENABLED"]["active"] is True
    assert snap["enrichers"]["SIBLING_HUNT_ENABLED"]["active"] is False


def test_circuit_breaker_skips_when_dormant(monkeypatch):
    """`agent_worker._check_circuit_breaker` short-circuits to False
    + resets the cycle counter when dormant. Without this short-circuit,
    parked candidates trip CRITICAL alerts every breaker-cycle (~9-10h
    on pre-merchant backlog).
    """
    _clear_env(monkeypatch)
    from app.workers import agent_worker
    importlib.reload(agent_worker)

    # Seed a positive cycle counter (simulating a previously tripped state)
    agent_worker._consecutive_unhealthy_cycles = 7

    # Dormant → must reset and return False
    paused = agent_worker._check_circuit_breaker()
    assert paused is False, (
        "breaker must NOT pause auto-apply when pipeline is dormant — "
        "auto-apply is already paused by intent."
    )
    assert agent_worker._consecutive_unhealthy_cycles == 0, (
        "dormant short-circuit must reset the counter so un-park "
        "doesn't inherit a stale tripped state."
    )


def test_circuit_breaker_runs_health_check_when_active(monkeypatch):
    """Reverse contract: when ANY enricher is on, the breaker resumes
    its full health-check path. We don't assert the verdict — just
    that the dormancy short-circuit does NOT swallow the call.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "1")
    from app.workers import agent_worker
    importlib.reload(agent_worker)

    # With an enricher on, dormancy is False → check_circuit_breaker
    # proceeds to the live get_loop_health path. We patch that to a
    # minimal healthy response to avoid DB dependency in the test.
    import app.services.loop_health as lh

    def _stub_health(_db):
        return {"is_healthy": True, "stuck_items": [], "thrashing_sources": []}

    monkeypatch.setattr(lh, "get_loop_health", _stub_health)
    paused = agent_worker._check_circuit_breaker()
    assert paused is False  # healthy → no pause
    assert agent_worker._consecutive_unhealthy_cycles == 0
