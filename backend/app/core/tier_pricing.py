"""tier_pricing.py — single source of truth for tier subscription cost.

Every place in the backend that computes `net_roi = prevented − cost`
or similar must import `TIER_SUBSCRIPTION_EUR` from here, never
hardcode the number inline. Without this discipline a pricing change
("Pro moves from €99 to €149") would require grepping every `99.0`
literal in the codebase — a class of drift detected by
`audit_tier_cost_literals.py` in preflight.

Founder moves the numbers via the pricing matrix memo
(`project_brutal_feature_pricing_matrix_2026_04_18.md`); this module
is the code-side mirror.

Constants match `docs/processors.md` and the memo's §2 bundle
justification. If they drift, preflight catches it.
"""
from __future__ import annotations

# Plan → monthly subscription cost in EUR.
# Lite: €0 today (closed beta). Early-access install = free
# trial until billing wire-up in Phase 2+.
# Pro: €99/mo per pricing matrix §2.2.
# Scale: €249/mo per pricing matrix §2.3.
TIER_SUBSCRIPTION_EUR: dict[str, float] = {
    "lite": 0.0,
    "lite":    0.0,  # alias for defensive lookups
    "pro":     99.0,
    "scale":   249.0,
}


def subscription_cost(plan: str) -> float:
    """Return the monthly subscription cost for a plan, defaulting to
    0.0 for unknown plans (Lite-equivalent — never invent a cost)."""
    return TIER_SUBSCRIPTION_EUR.get(plan, 0.0)
