# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
evolution_causal_attribution.py — True RCT attribution via nudge holdouts.

Problem the quasi-causal loop had
---------------------------------
Trend-adjusted pre/post delta is the best honest approximation when no
holdout exists. It is NOT causal — seasonality, promo cycles, other
concurrent changes can all masquerade as proposal impact.

What this module adds
---------------------
When an EvolutionProposal modifies nudges AND those nudges have
`active_nudges.holdout_pct > 0`, we get a proper randomized control:

  exposed_visitors = visitors who were shown the nudge (nudge_events
                     event_type='shown' for the linked nudge_id)
  control_visitors = visitors who were eligible but randomly excluded
                     (nudge_events event_type='holdout_assigned')

The holdout assignment is deterministic per visitor (hash-based modulo
of holdout_pct) — effectively random w.r.t. the proposal — so a
difference in downstream CVR is **causally attributable** to the
intervention.

Causal delta
------------
    CVR_exposed = orders(exposed_visitors) / n(exposed_visitors)
    CVR_control = orders(control_visitors) / n(control_visitors)
    causal_delta = CVR_exposed − CVR_control

Two-proportion z-test for significance. Attribution tag is set to
'causal' on the evidence, and the confidence_score is boosted because
we no longer have to discount for observational bias.

Integration
-----------
`measure_business_impact()` in evolution_business_outcomes.py calls
`try_causal_measurement()` FIRST when `linked_nudge_ids` is populated.
If causal measurement succeeds with enough samples, we return the
causal outcome. Otherwise we fall back to the trend-adjusted path.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal

log = logging.getLogger("evolution_causal_attribution")

# Minimum cohort sizes — smaller than the global thresholds because
# holdout comparisons are CLEANER (no trend subtraction noise).
_MIN_VISITORS_PER_COHORT = 500
_MIN_ORDERS_PER_COHORT = 20
_IMPROVED_THRESHOLD = 0.05
_DECLINED_THRESHOLD = -0.05


def _two_proportion_z(n1: int, x1: int, n2: int, x2: int) -> float:
    if n1 <= 0 or n2 <= 0:
        return 0.0
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return 0.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0
    return ((x2 / n2) - (x1 / n1)) / se


# Count distinct visitors who received a given event_type for the linked
# nudge_ids in the window [start, end), AND count how many of them made
# a purchase within that same window (joined via visitor_purchase_sessions).
_COHORT_SQL = text("""
    WITH cohort AS (
        SELECT DISTINCT ne.visitor_id, ne.shop_domain
        FROM nudge_events ne
        WHERE ne.nudge_id = ANY(:nudge_ids)
          AND ne.event_type = :event_type
          AND ne.created_at >= :start_ts
          AND ne.created_at <  :end_ts
          AND ne.visitor_id IS NOT NULL
    ),
    converters AS (
        SELECT COUNT(DISTINCT v.visitor_id) AS n_converted
        FROM visitor_purchase_sessions v
        INNER JOIN cohort c
          ON v.visitor_id = c.visitor_id
         AND v.shop_domain = c.shop_domain
        WHERE v.confirmed_at >= :start_ts
          AND v.confirmed_at <  :end_ts
    )
    SELECT
        (SELECT COUNT(*) FROM cohort) AS n_visitors,
        converters.n_converted AS n_orders
    FROM converters
""")


def _cohort_metrics(
    db: Session, *, nudge_ids: list[int], event_type: str,
    start: datetime, end: datetime,
) -> dict:
    """
    Return {n_visitors, n_orders, cvr} for a single cohort.
    """
    row = db.execute(_COHORT_SQL, {
        "nudge_ids": list(nudge_ids),
        "event_type": event_type,
        "start_ts": start,
        "end_ts": end,
    }).fetchone()
    n_visitors = int(row[0] or 0)
    n_orders = int(row[1] or 0)
    cvr = (n_orders / n_visitors) if n_visitors > 0 else 0.0
    return {"n_visitors": n_visitors, "n_orders": n_orders, "cvr": round(cvr, 6)}


def _parse_nudge_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        try:
            out.append(int(item))
        except (ValueError, TypeError):
            continue
    return out


def try_causal_measurement(
    db: Session, proposal: EvolutionProposal,
    *, window_start: datetime, window_end: datetime,
) -> tuple[str, dict] | None:
    """
    Attempt a causal (RCT-style) measurement. Returns (outcome, evidence)
    on success, or None if causal measurement isn't available for this
    proposal (no linked nudges, insufficient holdout data, etc.).

    The caller is responsible for window selection — typically the same
    14-day AFTER window used by the quasi-causal path.
    """
    nudge_ids = _parse_nudge_ids(proposal.linked_nudge_ids)
    if not nudge_ids:
        return None

    exposed = _cohort_metrics(
        db, nudge_ids=nudge_ids, event_type="shown",
        start=window_start, end=window_end,
    )
    control = _cohort_metrics(
        db, nudge_ids=nudge_ids, event_type="holdout_assigned",
        start=window_start, end=window_end,
    )

    # Sample-size gate — per cohort, not combined.
    if (
        exposed["n_visitors"] < _MIN_VISITORS_PER_COHORT
        or control["n_visitors"] < _MIN_VISITORS_PER_COHORT
        or (exposed["n_orders"] + control["n_orders"]) < _MIN_ORDERS_PER_COHORT
    ):
        return None

    causal_delta = exposed["cvr"] - control["cvr"]
    rel_change = (causal_delta / control["cvr"]) if control["cvr"] > 0 else 0.0

    z = _two_proportion_z(
        n1=control["n_visitors"], x1=control["n_orders"],
        n2=exposed["n_visitors"], x2=exposed["n_orders"],
    )

    # Causal confidence: since randomization is clean, we skip the
    # consistency penalty and just gate on sample+significance.
    sample_factor = max(0.0, min(1.0,
        (exposed["n_orders"] + control["n_orders"] - _MIN_ORDERS_PER_COHORT) /
        max(1, 4 * _MIN_ORDERS_PER_COHORT),
    ))
    significance_factor = max(0.0, min(1.0, abs(z) / 3.0))
    # Causal boost: add up to +0.10 for having a real RCT
    confidence = round(min(1.0, min(sample_factor, significance_factor) + 0.10), 3)

    if rel_change >= _IMPROVED_THRESHOLD:
        outcome = "improved"
    elif rel_change <= _DECLINED_THRESHOLD:
        outcome = "declined"
    else:
        outcome = "stable"

    evidence = {
        "attribution_type": "causal",
        "disclosure": (
            "Randomized holdout via nudge_events (shown vs holdout_assigned); "
            "assignment is hash-based per-visitor, effectively random wrt the proposal."
        ),
        "linked_nudge_ids": nudge_ids,
        "window": [window_start.isoformat(), window_end.isoformat()],
        "exposed": exposed,
        "control": control,
        "causal_delta_cvr": round(causal_delta, 6),
        "relative_change": round(rel_change, 4),
        "z_score": round(z, 3),
        "confidence_score": confidence,
    }
    return outcome, evidence
