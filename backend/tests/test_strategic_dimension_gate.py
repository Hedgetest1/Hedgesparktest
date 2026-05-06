"""Locks G3 — strategic-dim gate parity (2026-05-06).

Before this fix `_STRATEGIC_DIMENSIONS = {memory, llm_usage, cost}` had
NO matching emitter — every `_assess_*` returned names like
`workers/pipeline/merchants/freshness/fix_rate/alerts` which never
satisfied the gate. Result: `_is_strategic_critical()` returned False
for every state, suppressing 100% of the CTO Telegram heartbeat. A
real memory blowout or LLM cap exhaustion would page the founder
ZERO times — silent failure mode.

After the fix:
    - 3 new assessors emit `name="memory"`, `name="llm_usage"`, `name="cost"`
    - audit_strategic_dimension_names_match_emitters.py blocks regression

These tests pin the contract at the gate level: when ANY strategic
dim is critical, the gate must return True; when only operational
dims are critical, the gate must return False.
"""
from __future__ import annotations

from app.services.system_health_synthesizer import (
    HealthDimension,
    SystemHealthState,
    _STRATEGIC_DIMENSIONS,
    _is_strategic_critical,
)


def _mk_state(*dims: HealthDimension) -> SystemHealthState:
    return SystemHealthState(
        overall_status="critical",
        confidence=1.0,
        dimensions=list(dims),
        top_issues=[],
        assessed_at="",
        previous_status=None,
    )


def _crit(name: str) -> HealthDimension:
    return HealthDimension(
        name=name, status="critical", value=1.0, trend="stable", detail="x",
    )


def _healthy(name: str) -> HealthDimension:
    return HealthDimension(
        name=name, status="healthy", value=0.0, trend="stable", detail="ok",
    )


def test_gate_passes_when_memory_critical():
    state = _mk_state(_crit("memory"), _healthy("workers"))
    assert _is_strategic_critical(state) is True


def test_gate_passes_when_llm_usage_critical():
    state = _mk_state(_crit("llm_usage"), _healthy("workers"))
    assert _is_strategic_critical(state) is True


def test_gate_passes_when_cost_critical():
    state = _mk_state(_crit("cost"), _healthy("workers"))
    assert _is_strategic_critical(state) is True


def test_gate_blocks_when_only_operational_critical():
    """workers/pipeline/merchants/etc. must NOT trip the founder Telegram.
    They drive overall_status (visible in /ops/system-health) but the
    brain handles them autonomously — strategic-only doctrine."""
    for op_name in ("workers", "pipeline", "liveness", "merchants",
                    "freshness", "fix_rate", "alerts"):
        state = _mk_state(_crit(op_name))
        assert _is_strategic_critical(state) is False, (
            f"operational dim '{op_name}' wrongly tripped strategic gate"
        )


def test_gate_blocks_when_strategic_only_degraded():
    """Degraded strategic dim is below the founder-page threshold; only
    *critical* strategic dims breach the gate."""
    state = _mk_state(
        HealthDimension(name="memory", status="degraded", value=0.85,
                        trend="stable", detail="80% used"),
        HealthDimension(name="llm_usage", status="degraded", value=0.75,
                        trend="stable", detail="75% used"),
    )
    assert _is_strategic_critical(state) is False


def test_gate_passes_when_strategic_critical_alongside_operational():
    state = _mk_state(_crit("workers"), _crit("memory"))
    assert _is_strategic_critical(state) is True


def test_strategic_dimensions_constant_canonical():
    """Lock the public contract — _STRATEGIC_DIMENSIONS is the gate
    source-of-truth. Adding/removing a strategic dim is a doctrine
    change that requires updating the audit AND the emitter list."""
    assert _STRATEGIC_DIMENSIONS == frozenset({"memory", "llm_usage", "cost"})


def test_each_strategic_name_has_emitter_in_module():
    """Static parity check at runtime: every name in _STRATEGIC_DIMENSIONS
    must appear in the synthesizer source as a HealthDimension(name=...)
    return value. This is the same invariant audit_strategic_dimension_
    names_match_emitters.py enforces at preflight, asserted here so a
    pytest run also catches the regression."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "system_health_synthesizer.py"
    ).read_text()
    for name in _STRATEGIC_DIMENSIONS:
        # Either `name="memory"` or `name='memory'` somewhere in the file.
        # That's deliberately weak — the audit script has the strict regex.
        assert (f'name="{name}"' in src) or (f"name='{name}'" in src), (
            f"strategic dim '{name}' has no emitter in synthesizer — silent "
            f"gate suppression regression"
        )
