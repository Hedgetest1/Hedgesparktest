"""pipeline_state.py — single source of truth for brain dormancy.

Born 2026-05-07 closing alert #129083 (circuit_breaker_tripped firing
~9-10h on 88-candidate parked backlog). Pipeline parks itself
pre-merchant per `project_pipeline_closed_until_merchants.md` —
candidates accumulate while the founder waits for the 1st paying
merchant.

In that parked state, `loop_health.get_loop_health()` correctly
reports `is_healthy=False` (88 stuck candidates exceed `_STUCK_HOURS`
thresholds). `agent_worker._check_circuit_breaker` correctly trips at
the configured threshold. But the alert is NOT actionable — the
breaker exists to PAUSE auto-apply on degradation, and auto-apply is
already structurally paused because the brain enrichers are off and
no patches are being produced.

Tripping CRITICAL ops_alerts on a structurally-paused pipeline
generates noise that:
  (a) pollutes ops_alerts (drains via 72h TTL but obscures real
      signals while live);
  (b) accumulates `_consecutive_unhealthy_cycles` indefinitely until
      un-park, defeating the un-park ceremony's intent;
  (c) inverts the breaker's signal-to-noise — when un-parked, real
      degradation cannot be distinguished from inherited dormancy.

This module provides the unified abstraction. Every brain hot path
that needs to know "is the pipeline structurally paused" calls
`is_pipeline_dormant()` rather than re-deriving from inline env reads.

Fail-safe semantics
-------------------
ANY brain enricher being on (1/true/yes) = ACTIVE. Only ALL-off =
DORMANT. This biases toward false-active, which is the safe direction
— a false-active pipeline lets the breaker fire on real degradation;
a false-dormant pipeline silences real degradation alerts.

Brain enrichers (the un-park ceremony flips these ON):
  * ADVERSARIAL_REVIEWER_ENABLED — 3-lens patch review
  * SIBLING_HUNT_ENABLED          — propagation discovery on alerts
  * ITERATIVE_FIX_ENABLED         — multi-iteration patch refinement

NOT a dormancy signal:
  * AUTO_APPLY_TIER1 — emergency kill switch (default ON; only flipped
    OFF mid-flight to halt apply during operator intervention).
"""
from __future__ import annotations

import os

# The 3 brain quality-enricher env vars. The founder's un-park
# ceremony (per project_pipeline_closed_until_merchants.md B.6) is
# "flip these ON after 1st paying merchant lands". Until then,
# they're all OFF and the brain is structurally paused.
_BRAIN_ENRICHER_ENV_VARS: tuple[str, ...] = (
    "ADVERSARIAL_REVIEWER_ENABLED",
    "SIBLING_HUNT_ENABLED",
    "ITERATIVE_FIX_ENABLED",
)


def _is_truthy(raw: str | None) -> bool:
    """Match the same truthy parse used by adversarial_reviewer / sibling_hunt
    / iterative_fix so dormancy detection cannot drift from those modules."""
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def is_pipeline_dormant() -> bool:
    """True when ALL brain enrichers are off — proxy for 'founder has
    not yet performed the un-park ceremony'.

    Returns
    -------
    bool : True if dormant (no enricher on), False if any on.

    Fail-safe direction: any-on → active. A false-dormant verdict
    would silence real degradation; a false-active verdict only costs
    one CRITICAL alert per breaker trip (acceptable).
    """
    return not any(_is_truthy(os.getenv(v)) for v in _BRAIN_ENRICHER_ENV_VARS)


def dormancy_status() -> dict:
    """Diagnostic snapshot for /ops endpoints. Returns each enricher's
    raw env value + truthy verdict + the overall dormancy verdict.
    Useful when an operator wants to see WHY the pipeline is reported
    as dormant (or active) without re-deriving by hand."""
    enrichers = {
        v: {
            "raw": os.getenv(v),
            "active": _is_truthy(os.getenv(v)),
        }
        for v in _BRAIN_ENRICHER_ENV_VARS
    }
    return {
        "dormant": is_pipeline_dormant(),
        "enrichers": enrichers,
    }
