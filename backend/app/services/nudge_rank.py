"""
nudge_rank.py — Autonomous nudge revenue prioritization ranking engine.

Computes a ranked list of all active nudges for a shop, ordered by estimated
economic impact.  Designed to be consumed by AI agents and the merchant
dashboard.

Design principles
-----------------
- Batch queries: 3 total DB queries for all N nudges (not N × heavy-join each).
- Truthful labeling: every estimate carries a ranking_basis and sample label.
- Agent-ready: each entry includes an agent_action block with a callable endpoint.
- Fallback chain: ranking degrades gracefully from revenue → RPV → CVR → no_data.

Performance
-----------
Three queries total regardless of nudge count:
  1. Event counts    — nudge_id × event_type × COUNT(DISTINCT visitor_id)
  2. Exposed attribution — per-nudge exposed CVR + revenue (CTE join chain)
  3. Holdout attribution — per-nudge holdout CVR + revenue (same CTE pattern)

Decision engine — 7 rules in strict priority order
---------------------------------------------------
  1. investigate_negative_lift  — nudge may be actively hurting revenue / CVR
  2. promote_winner_variant      — A/B winner ready to promote (p < 0.10)
  3. expand_eligible_segment     — positive lift confirmed, consider wider audience
  4. enable_holdout              — holdout not configured, causal measurement absent
  5. collect_more_data           — sample below MIN_SAMPLE_PER_GROUP
  6. deactivate_low_value        — low CVR + no positive lift + sufficient sample
  7. monitor                     — default: continue observing

Ranking signal fallback chain
------------------------------
  Primary  : estimated_incremental_revenue (has_order_data + sufficient + positive)
             ranking_basis = "incremental_revenue"
  Fallback1: incremental_rpv               (has_order_data, any sign)
             ranking_basis = "incremental_rpv"
  Fallback2: exposed post-exposure CVR     (no revenue data)
             ranking_basis = "cvr_fallback"
  Fallback3: 0.0                           (no data at all)
             ranking_basis = "no_data"
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.active_nudge import ActiveNudge
from app.services.nudge_measurement import (
    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    MIN_SAMPLE_PER_GROUP,
    _compute_revenue_lift,
    _resolve_currency,
    _two_prop_z_test,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decision engine thresholds
# ---------------------------------------------------------------------------
_MIN_CVR_THRESHOLD        = 0.03    # below this → deactivate_low_value candidate
_NEGATIVE_RPV_THRESHOLD   = -0.01   # incremental_rpv below this → investigate
_NEGATIVE_CVR_LIFT_PCT    = -5.0    # fallback (no revenue data) → investigate


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def compute_nudge_rank(
    db:           Session,
    shop_domain:  str,
    nudges:       list[ActiveNudge],
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
) -> list[dict]:
    """
    Rank all provided nudges by economic impact using 3 batch queries.

    Parameters
    ----------
    db           : SQLAlchemy session
    shop_domain  : validated shop domain (authenticated by caller)
    nudges       : list of ActiveNudge objects from list_active_nudges()
    window_hours : attribution window in hours (1–168)

    Returns
    -------
    List of rank-entry dicts sorted by ranking_signal descending.
    Each entry contains: nudge summary, CVR metrics, revenue_lift block,
    ranking_signal, ranking_basis, recommendation, agent_action, rank position.
    """
    if not nudges:
        return []

    nudge_ids   = [n.id for n in nudges]
    window_secs = window_hours * 3600

    # 3 batch queries — total DB round-trips regardless of N nudges
    event_counts = _batch_event_counts(db, shop_domain, nudge_ids)
    exposed_attr = _batch_group_attribution(
        db, shop_domain, nudge_ids, window_secs, event_type="shown"
    )
    holdout_attr = _batch_group_attribution(
        db, shop_domain, nudge_ids, window_secs, event_type="holdout_assigned"
    )

    entries = []
    for nudge in nudges:
        entry = _build_rank_entry(
            nudge        = nudge,
            event_counts = event_counts.get(nudge.id, {}),
            exposed_attr = exposed_attr.get(nudge.id, _empty_group_attr()),
            holdout_attr = holdout_attr.get(nudge.id, _empty_group_attr()),
            window_hours = window_hours,
        )
        entries.append(entry)

    # Sort highest ranking_signal first
    entries.sort(key=lambda e: e["ranking_signal"], reverse=True)

    # Assign rank position after sorting
    for pos, entry in enumerate(entries, start=1):
        entry["rank"] = pos

    return entries


# ---------------------------------------------------------------------------
# Batch query 1 — event counts per nudge per event_type
# ---------------------------------------------------------------------------

def _batch_event_counts(
    db:          Session,
    shop_domain: str,
    nudge_ids:   list[int],
) -> dict[int, dict[str, int]]:
    """
    Return {nudge_id: {event_type: distinct_visitor_count}} in one query.

    nudge_ids are integers sourced from the DB — safe to interpolate directly
    into the IN clause (no user-supplied strings involved).
    """
    id_list = ", ".join(str(i) for i in nudge_ids)
    sql = text(f"""
        SELECT
            nudge_id,
            event_type,
            COUNT(DISTINCT visitor_id) AS visitor_count
        FROM nudge_events
        WHERE shop_domain = :shop
          AND nudge_id IN ({id_list})
        GROUP BY nudge_id, event_type
    """)

    rows = db.execute(sql, {"shop": shop_domain}).mappings().all()

    result: dict[int, dict[str, int]] = {}
    for row in rows:
        nid   = int(row["nudge_id"])
        etype = row["event_type"]
        cnt   = int(row["visitor_count"] or 0)
        if nid not in result:
            result[nid] = {}
        result[nid][etype] = cnt

    return result


# ---------------------------------------------------------------------------
# Batch query 2 / 3 — attributed CVR + revenue per nudge for one group
# ---------------------------------------------------------------------------

def _batch_group_attribution(
    db:          Session,
    shop_domain: str,
    nudge_ids:   list[int],
    window_secs: int,
    event_type:  str,   # "shown" | "holdout_assigned"
) -> dict[int, dict]:
    """
    Return {nudge_id: {purchasers, revenue, currency_count, sample_currency}}.

    Called twice — once for the exposed group (event_type='shown') and once for
    the holdout group (event_type='holdout_assigned').

    Join chain (mirrors nudge_measurement.py per-nudge query):
        nudge_events (first qualifying event per visitor per nudge)
        → visitor_purchase_sessions (confirmed_at within window)
        → shop_orders LEFT JOIN (1:1 on shopify_order_id — no duplication)

    nudge_ids are DB integers — safe for direct interpolation.
    """
    id_list = ", ".join(str(i) for i in nudge_ids)
    sql = text(f"""
        WITH first_events AS (
            SELECT
                nudge_id,
                visitor_id,
                MIN(created_at) AS first_event_at
            FROM nudge_events
            WHERE shop_domain = :shop
              AND nudge_id    IN ({id_list})
              AND event_type  = :event_type
              AND visitor_id  IS NOT NULL
            GROUP BY nudge_id, visitor_id
        ),
        attributed_purchases AS (
            SELECT
                fe.nudge_id,
                fe.visitor_id,
                vps.shopify_order_id
            FROM first_events fe
            JOIN visitor_purchase_sessions vps
              ON  vps.visitor_id  = fe.visitor_id
              AND vps.shop_domain = :shop
              AND vps.confirmed_at > fe.first_event_at
              AND vps.confirmed_at < fe.first_event_at + (:window_secs * INTERVAL '1 second')
        )
        SELECT
            ap.nudge_id,
            COUNT(DISTINCT ap.visitor_id)      AS purchasers,
            COALESCE(SUM(so.total_price), 0.0) AS revenue,
            COUNT(DISTINCT so.currency)        AS currency_count,
            MIN(so.currency)                   AS sample_currency
        FROM attributed_purchases ap
        LEFT JOIN shop_orders so
          ON  so.shopify_order_id = ap.shopify_order_id
          AND so.shop_domain      = :shop
        GROUP BY ap.nudge_id
    """)

    rows = db.execute(sql, {
        "shop":        shop_domain,
        "event_type":  event_type,
        "window_secs": window_secs,
    }).mappings().all()

    result: dict[int, dict] = {}
    for row in rows:
        result[int(row["nudge_id"])] = {
            "purchasers":      int(row["purchasers"] or 0),
            "revenue":         float(row["revenue"] or 0.0),
            "currency_count":  int(row["currency_count"] or 0),
            "sample_currency": row["sample_currency"],
        }
    return result


# ---------------------------------------------------------------------------
# Per-nudge rank entry assembly
# ---------------------------------------------------------------------------

def _build_rank_entry(
    nudge:        ActiveNudge,
    event_counts: dict[str, int],
    exposed_attr: dict,
    holdout_attr: dict,
    window_hours: int,
) -> dict:
    """
    Assemble the full rank entry dict for one nudge from batch query results.
    """
    exposed_count = event_counts.get("shown", 0)
    holdout_count = event_counts.get("holdout_assigned", 0)
    dismissed     = event_counts.get("dismissed", 0)
    clicked       = event_counts.get("clicked", 0)

    exposed_purchases = exposed_attr["purchasers"]
    exposed_revenue   = exposed_attr["revenue"]
    holdout_purchases = holdout_attr["purchasers"]
    holdout_revenue   = holdout_attr["revenue"]

    # CVR metrics
    exposed_cvr = (exposed_purchases / exposed_count) if exposed_count > 0 else 0.0
    holdout_cvr = (holdout_purchases / holdout_count) if holdout_count > 0 else 0.0
    cvr_lift_pct = (
        (exposed_cvr - holdout_cvr) / holdout_cvr * 100
        if holdout_cvr > 0 else None
    )

    # Sample sufficiency
    sufficient_exposed = exposed_count >= MIN_SAMPLE_PER_GROUP
    sufficient_holdout = holdout_count >= MIN_SAMPLE_PER_GROUP
    sufficient_sample  = sufficient_exposed and sufficient_holdout

    # CVR z-test — only when both groups have sufficient sample
    p_value = None
    if sufficient_sample and holdout_count > 0:
        _, p_value = _two_prop_z_test(
            n1=exposed_count, k1=exposed_purchases,
            n2=holdout_count, k2=holdout_purchases,
        )

    # Currency resolution for both groups
    exposed_currency, _ = _resolve_currency(
        exposed_attr["currency_count"], exposed_attr["sample_currency"]
    )
    holdout_currency, _ = _resolve_currency(
        holdout_attr["currency_count"], holdout_attr["sample_currency"]
    )

    # Revenue lift — reuse _compute_revenue_lift from nudge_measurement
    revenue_lift = _compute_revenue_lift(
        exposed_count     = exposed_count,
        holdout_count     = holdout_count,
        exposed_revenue   = exposed_revenue,
        holdout_revenue   = holdout_revenue,
        exposed_purchases = exposed_purchases,
        holdout_purchases = holdout_purchases,
        exposed_currency  = exposed_currency,
        holdout_currency  = holdout_currency,
        window_hours      = window_hours,
    )

    # Ranking signal (scalar for sort key)
    ranking_signal, ranking_basis = _compute_ranking_signal(
        revenue_lift      = revenue_lift,
        exposed_cvr       = exposed_cvr,
        sufficient_sample = sufficient_sample,
    )

    # Recommendation + agent_action
    rec = _assign_recommendation(
        nudge             = nudge,
        exposed_count     = exposed_count,
        holdout_count     = holdout_count,
        exposed_cvr       = exposed_cvr,
        holdout_cvr       = holdout_cvr,
        cvr_lift_pct      = cvr_lift_pct,
        sufficient_sample = sufficient_sample,
        revenue_lift      = revenue_lift,
        p_value           = p_value,
    )

    return {
        # Nudge identity
        "nudge_id":           nudge.id,
        "product_url":        nudge.product_url,
        "action_type":        nudge.action_type,
        "status":             nudge.status,
        "is_ab_experiment":   nudge.is_ab_experiment(),
        "holdout_pct":        nudge.holdout_pct or 0,
        "is_holdout_active":  nudge.is_holdout_active(),
        "created_at":         nudge.created_at.isoformat() if nudge.created_at else None,

        # Attribution config
        "attribution_window_hours": window_hours,

        # Exposure counts
        "exposed_count":   exposed_count,
        "holdout_count":   holdout_count,
        "dismissed_count": dismissed,
        "clicked_count":   clicked,
        "sufficient_sample": sufficient_sample,

        # CVR metrics
        "post_exposure_cvr": round(exposed_cvr, 6),
        "holdout_cvr":       round(holdout_cvr, 6) if holdout_count > 0 else None,
        "cvr_lift_pct":      round(cvr_lift_pct, 2) if cvr_lift_pct is not None else None,
        "p_value":           round(p_value, 4) if p_value is not None else None,

        # Revenue lift (full block from _compute_revenue_lift)
        "revenue_lift": revenue_lift,

        # Ranking
        "ranking_signal": ranking_signal,
        "ranking_basis":  ranking_basis,

        # rank is injected after sort in compute_nudge_rank()

        # Decision engine output
        "recommendation":        rec["label"],
        "recommendation_reason": rec["reason"],
        "agent_action":          rec["agent_action"],
    }


# ---------------------------------------------------------------------------
# Decision engine — 7 rules in strict priority order
# ---------------------------------------------------------------------------

def _assign_recommendation(
    nudge:             ActiveNudge,
    exposed_count:     int,
    holdout_count:     int,
    exposed_cvr:       float,
    holdout_cvr:       float,
    cvr_lift_pct:      Optional[float],
    sufficient_sample: bool,
    revenue_lift:      dict,
    p_value:           Optional[float],
) -> dict:
    """
    Assign one of 7 recommendations in strict priority order.
    Returns {label, reason, agent_action}.
    """
    nudge_id        = nudge.id
    holdout_pct     = nudge.holdout_pct or 0
    is_ab           = nudge.is_ab_experiment()
    has_order_data  = revenue_lift.get("has_order_data", False)
    incremental_rpv = revenue_lift.get("incremental_rpv")

    # ------------------------------------------------------------------
    # Rule 1 — investigate_negative_lift
    # ------------------------------------------------------------------
    if sufficient_sample:
        # Revenue-based check (preferred)
        if has_order_data and incremental_rpv is not None:
            if incremental_rpv < _NEGATIVE_RPV_THRESHOLD:
                return _rec(
                    label="investigate_negative_lift",
                    reason=(
                        f"Estimated incremental RPV is {incremental_rpv:.4f} — negative. "
                        "This nudge may be suppressing revenue for the exposed group compared "
                        "to the holdout control. Review nudge copy, targeting, and timing "
                        "before the next traffic cycle."
                    ),
                    method="GET",
                    endpoint=f"/pro/nudges/{nudge_id}/stats",
                    payload=None,
                    description="Retrieve full stats to investigate the negative revenue lift.",
                )
        # CVR-based fallback (no revenue data)
        elif not has_order_data and holdout_count >= MIN_SAMPLE_PER_GROUP:
            if cvr_lift_pct is not None and cvr_lift_pct < _NEGATIVE_CVR_LIFT_PCT:
                return _rec(
                    label="investigate_negative_lift",
                    reason=(
                        f"CVR lift is {cvr_lift_pct:.1f}% — negative. No revenue data is "
                        "available (order webhook may not be configured). "
                        "Investigate nudge performance."
                    ),
                    method="GET",
                    endpoint=f"/pro/nudges/{nudge_id}/stats",
                    payload=None,
                    description="Retrieve full stats to investigate the negative CVR lift.",
                )

    # ------------------------------------------------------------------
    # Rule 2 — promote_winner_variant (A/B experiments only)
    # ------------------------------------------------------------------
    if is_ab and sufficient_sample and p_value is not None and p_value < 0.10:
        return _rec(
            label="promote_winner_variant",
            reason=(
                f"A/B test shows a statistically significant leader (p={p_value:.3f}, "
                "one-tailed, observational). Consider promoting the winning variant "
                "to 100% of eligible visitors."
            ),
            method="GET",
            endpoint=f"/pro/nudges/{nudge_id}/stats",
            payload=None,
            description="Review per-variant breakdown and winner to decide which variant to promote.",
        )

    # ------------------------------------------------------------------
    # Rule 3 — expand_eligible_segment (single-variant, positive lift)
    # ------------------------------------------------------------------
    if not is_ab and sufficient_sample:
        positive_lift = (
            (has_order_data and incremental_rpv is not None and incremental_rpv > 0)
            or (holdout_cvr > 0 and exposed_cvr > holdout_cvr)
        )
        if positive_lift:
            return _rec(
                label="expand_eligible_segment",
                reason=(
                    "Nudge shows positive incremental lift with sufficient sample. "
                    "Consider broadening the eligible visitor segment to reach more shoppers."
                ),
                method="GET",
                endpoint=f"/pro/nudges/{nudge_id}/stats",
                payload=None,
                description="Review gating calibration and segment reach before expanding.",
            )

    # ------------------------------------------------------------------
    # Rule 4 — enable_holdout (no holdout configured → no causal data)
    # ------------------------------------------------------------------
    if holdout_pct == 0:
        return _rec(
            label="enable_holdout",
            reason=(
                "Holdout is not enabled. Without a control group, incremental lift "
                "cannot be estimated. Recommend enabling 20% holdout to begin "
                "quasi-experimental measurement."
            ),
            method="PATCH",
            endpoint=f"/pro/nudges/{nudge_id}/holdout",
            payload={"holdout_pct": 20},
            description=(
                "Enable 20% holdout to begin quasi-experimental incremental lift measurement."
            ),
        )

    # ------------------------------------------------------------------
    # Rule 5 — collect_more_data (sample too small)
    # ------------------------------------------------------------------
    if not sufficient_sample:
        need_exposed = max(0, MIN_SAMPLE_PER_GROUP - exposed_count)
        need_holdout = max(0, MIN_SAMPLE_PER_GROUP - holdout_count)
        return _rec(
            label="collect_more_data",
            reason=(
                f"Insufficient sample for reliable measurement "
                f"(need ≥{MIN_SAMPLE_PER_GROUP} per group). "
                f"Exposed: {exposed_count} (need {need_exposed} more). "
                f"Holdout: {holdout_count} (need {need_holdout} more)."
            ),
            method=None,
            endpoint=None,
            payload=None,
            description="No action available yet. Wait for more traffic to accumulate.",
            available=False,
        )

    # ------------------------------------------------------------------
    # Rule 6 — deactivate_low_value
    # ------------------------------------------------------------------
    if sufficient_sample and exposed_cvr < _MIN_CVR_THRESHOLD:
        rpv_not_positive = (incremental_rpv is None or incremental_rpv <= 0)
        if rpv_not_positive:
            return _rec(
                label="deactivate_low_value",
                reason=(
                    f"Post-exposure CVR is {exposed_cvr:.2%} "
                    f"(below {_MIN_CVR_THRESHOLD:.0%} threshold) with no positive "
                    "incremental lift detected. This nudge is not contributing "
                    "measurable value."
                ),
                method="DELETE",
                endpoint=f"/pro/nudges/{nudge_id}",
                payload=None,
                description="Deactivate this nudge — it is not generating measurable revenue.",
            )

    # ------------------------------------------------------------------
    # Rule 7 — monitor (default)
    # ------------------------------------------------------------------
    return _rec(
        label="monitor",
        reason=(
            "Nudge is performing within normal parameters. Continue monitoring "
            "as data accumulates."
        ),
        method="GET",
        endpoint=f"/pro/nudges/{nudge_id}/stats",
        payload=None,
        description="Check the detailed stats report for this nudge.",
    )


# ---------------------------------------------------------------------------
# Ranking signal — scalar for sort key
# ---------------------------------------------------------------------------

def _compute_ranking_signal(
    revenue_lift:      dict,
    exposed_cvr:       float,
    sufficient_sample: bool,
) -> tuple[float, str]:
    """
    Return (signal_value, basis_label) using the fallback chain:
      1. estimated_incremental_revenue — has_order_data + sufficient + positive
      2. incremental_rpv               — has_order_data (any sign)
      3. exposed_cvr                   — CVR fallback
      4. 0.0                           — no data
    """
    has_order_data  = revenue_lift.get("has_order_data", False)
    incremental_rpv = revenue_lift.get("incremental_rpv")
    est_revenue     = revenue_lift.get("estimated_incremental_revenue")

    if has_order_data and sufficient_sample and est_revenue is not None and est_revenue > 0:
        return float(est_revenue), "incremental_revenue"

    if has_order_data and incremental_rpv is not None:
        return float(incremental_rpv), "incremental_rpv"

    if exposed_cvr > 0:
        return float(exposed_cvr), "cvr_fallback"

    return 0.0, "no_data"


# ---------------------------------------------------------------------------
# Helper — build recommendation dict
# ---------------------------------------------------------------------------

def _rec(
    label:       str,
    reason:      str,
    method:      Optional[str],
    endpoint:    Optional[str],
    payload:     Optional[dict],
    description: str,
    available:   bool = True,
) -> dict:
    return {
        "label":  label,
        "reason": reason,
        "agent_action": {
            "method":      method,
            "endpoint":    endpoint,
            "payload":     payload,
            "available":   available,
            "description": description,
        },
    }


# ---------------------------------------------------------------------------
# Empty group attribution (no query result for this nudge)
# ---------------------------------------------------------------------------

def _empty_group_attr() -> dict:
    return {
        "purchasers":      0,
        "revenue":         0.0,
        "currency_count":  0,
        "sample_currency": None,
    }
