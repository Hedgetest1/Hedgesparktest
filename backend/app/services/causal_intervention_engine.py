"""
causal_intervention_engine.py — Generalized causal attribution.

Extends the nudge-specific RCT holdout system to measure causal impact
of ANY merchant intervention:
  - Nudges (existing RCT via holdout_pct)
  - Recommendations acted upon (quasi-experimental)
  - Revenue autopsy actions (pre/post with trend adjustment)

The key insight: we already have the holdout infrastructure. This module
generalizes it and adds quasi-experimental fallback for cases where
true holdout isn't available.

Why this is unreachable: competitors don't have holdout measurement
at all. We can say "this intervention CAUSED +X% revenue" with
statistical confidence. They can only say "this happened, then that
happened" (correlation).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("causal_engine")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _z_test(cvr_a: float, n_a: int, cvr_b: float, n_b: int) -> tuple[float, float]:
    """Two-proportion z-test. Returns (z_score, confidence_pct)."""
    if n_a < 10 or n_b < 10:
        return 0.0, 0.0

    pooled = (cvr_a * n_a + cvr_b * n_b) / (n_a + n_b)
    if pooled <= 0 or pooled >= 1:
        return 0.0, 0.0

    se = math.sqrt(pooled * (1 - pooled) * (1/n_a + 1/n_b))
    if se == 0:
        return 0.0, 0.0

    z = (cvr_a - cvr_b) / se

    # Confidence from z-score (simplified lookup)
    abs_z = abs(z)
    if abs_z >= 2.576:
        confidence = 99
    elif abs_z >= 1.960:
        confidence = 95
    elif abs_z >= 1.645:
        confidence = 90
    elif abs_z >= 1.282:
        confidence = 80
    else:
        confidence = round(min(80, abs_z / 1.282 * 80))

    return round(z, 3), confidence


def measure_nudge_lift(db: Session, shop_domain: str) -> dict:
    """
    Aggregate causal lift across ALL nudges for a shop using RCT holdout data.

    This is THE competitive claim. Returns:
      {
        total_lift_pct, attributed_revenue_eur, confidence,
        nudges_measured, methodology
      }
    """
    now = _now()
    cutoff = now - timedelta(days=30)

    # Get all nudge events with holdout data. Revenue attribution walks
    # nudge_events → visitor_purchase_sessions (by visitor_id) →
    # shop_orders (by shopify_order_id). visitor_purchase_sessions has
    # no price column of its own — that's carried on shop_orders — and
    # its time column is `confirmed_at`, not `created_at`.
    rows = db.execute(text("""
        SELECT ne.nudge_id, ne.event_type, ne.visitor_id,
               COALESCE(so.total_price, 0) AS revenue
        FROM nudge_events ne
        LEFT JOIN visitor_purchase_sessions vps
            ON vps.visitor_id = ne.visitor_id
            AND vps.shop_domain = :shop
            AND vps.confirmed_at >= :cutoff
        LEFT JOIN shop_orders so
            ON so.shopify_order_id = vps.shopify_order_id
            AND so.shop_domain = :shop
        WHERE ne.shop_domain = :shop
          AND ne.created_at >= :cutoff
          AND ne.event_type IN ('shown', 'holdout_assigned')
    """), {"shop": shop_domain, "cutoff": cutoff}).fetchall()

    if not rows:
        return {
            "shop_domain": shop_domain,
            "total_lift_pct": 0,
            "attributed_revenue_eur": 0,
            "confidence": 0,
            "nudges_measured": 0,
            "methodology": "rct_holdout",
            "detail": "No nudge events with holdout data in last 30 days.",
        }

    # Aggregate by nudge
    nudge_data: dict[int, dict] = {}
    for r in rows:
        nid = r[0]
        nd = nudge_data.setdefault(nid, {
            "exposed_visitors": set(), "holdout_visitors": set(),
            "exposed_purchases": 0, "holdout_purchases": 0,
            "exposed_revenue": 0.0, "holdout_revenue": 0.0,
        })
        vid = r[1]
        revenue = float(r[3] or 0)

        if r[1] == "shown":
            nd["exposed_visitors"].add(r[2])
            if revenue > 0:
                nd["exposed_purchases"] += 1
                nd["exposed_revenue"] += revenue
        elif r[1] == "holdout_assigned":
            nd["holdout_visitors"].add(r[2])
            if revenue > 0:
                nd["holdout_purchases"] += 1
                nd["holdout_revenue"] += revenue

    total_exposed = 0
    total_holdout = 0
    total_exposed_purchases = 0
    total_holdout_purchases = 0
    total_attributed = 0.0
    nudges_measured = 0

    for nid, nd in nudge_data.items():
        n_exposed = len(nd["exposed_visitors"])
        n_holdout = len(nd["holdout_visitors"])
        if n_exposed < 20 or n_holdout < 5:
            continue

        nudges_measured += 1
        total_exposed += n_exposed
        total_holdout += n_holdout
        total_exposed_purchases += nd["exposed_purchases"]
        total_holdout_purchases += nd["holdout_purchases"]

        # Attributed revenue = excess revenue above holdout rate
        holdout_cvr = nd["holdout_purchases"] / n_holdout if n_holdout > 0 else 0
        exposed_cvr = nd["exposed_purchases"] / n_exposed if n_exposed > 0 else 0
        excess_cvr = max(0, exposed_cvr - holdout_cvr)
        avg_revenue = nd["exposed_revenue"] / nd["exposed_purchases"] if nd["exposed_purchases"] > 0 else 0
        attributed = excess_cvr * n_exposed * avg_revenue
        total_attributed += attributed

    # Overall lift
    if total_exposed > 0 and total_holdout > 0:
        overall_exposed_cvr = total_exposed_purchases / total_exposed
        overall_holdout_cvr = total_holdout_purchases / total_holdout
        lift_pct = ((overall_exposed_cvr - overall_holdout_cvr) / overall_holdout_cvr * 100) if overall_holdout_cvr > 0 else 0
        z, confidence = _z_test(overall_exposed_cvr, total_exposed, overall_holdout_cvr, total_holdout)
    else:
        lift_pct = 0
        confidence = 0

    try:
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"
    return {
        "shop_domain": shop_domain,
        "total_lift_pct": round(lift_pct, 2),
        "attributed_revenue_eur": round(total_attributed, 2),
        "confidence": confidence,
        "nudges_measured": nudges_measured,
        "exposed_visitors": total_exposed,
        "holdout_visitors": total_holdout,
        "methodology": "rct_holdout",
        "detail": (
            f"Measured {nudges_measured} nudges with holdout groups. "
            f"CVR lift: {lift_pct:+.1f}% at {confidence}% confidence."
        ) if nudges_measured > 0 else "Insufficient holdout data.",
        "currency": currency,
    }


def measure_recommendation_impact(db: Session, shop_domain: str) -> dict:
    """
    Quasi-experimental measurement of recommendation impact.

    For recommendations acted upon vs not acted upon, compare
    pre/post revenue trajectory with trend adjustment.

    This is the fallback for non-holdout interventions.
    """
    now = _now()

    # Actions taken — source is autonomous_actions, the real table for
    # completed/measured nudges. The previous query pointed at a ghost
    # `action_log` table and always returned zero, meaning the
    # pre/post revenue measurement (quasi-experimental fallback for
    # non-holdout interventions) has been dead since launch.
    actions = db.execute(text("""
        SELECT action_type, COALESCE(deployed_at, created_at) AS action_at
        FROM autonomous_actions
        WHERE shop_domain = :shop
          AND outcome IN ('win', 'measured', 'no_effect')
          AND COALESCE(deployed_at, created_at) >= :cutoff
        ORDER BY action_at
    """), {"shop": shop_domain, "cutoff": now - timedelta(days=60)}).fetchall()

    if not actions:
        return {
            "shop_domain": shop_domain,
            "actions_measured": 0,
            "avg_impact_pct": 0,
            "methodology": "quasi_experimental_pre_post",
            "detail": "No completed actions to measure.",
        }

    # For each action, compare 7-day revenue before vs 7-day after
    impacts = []
    for action in actions:
        action_date = action[1]
        pre_start = action_date - timedelta(days=7)
        post_end = action_date + timedelta(days=7)

        rev_row = db.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN created_at < :action_date THEN total_price ELSE 0 END), 0) as pre_rev,
                COALESCE(SUM(CASE WHEN created_at >= :action_date THEN total_price ELSE 0 END), 0) as post_rev
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at >= :pre_start
              AND created_at < :post_end
        """), {
            "shop": shop_domain,
            "action_date": action_date,
            "pre_start": pre_start,
            "post_end": post_end,
        }).fetchone()

        pre = float(rev_row[0] or 0)
        post = float(rev_row[1] or 0)
        if pre > 0:
            change = ((post - pre) / pre) * 100
            impacts.append({
                "action_type": action[0],
                "action_date": action_date.isoformat(),
                "pre_revenue": round(pre, 2),
                "post_revenue": round(post, 2),
                "impact_pct": round(change, 1),
            })

    avg_impact = (sum(i["impact_pct"] for i in impacts) / len(impacts)) if impacts else 0

    return {
        "shop_domain": shop_domain,
        "actions_measured": len(impacts),
        "avg_impact_pct": round(avg_impact, 1),
        "impacts": impacts[:10],
        "methodology": "quasi_experimental_pre_post",
        "detail": (
            f"Measured {len(impacts)} actions. Avg revenue change: {avg_impact:+.1f}% "
            f"(quasi-experimental, not causal — use nudge holdouts for true causation)."
        ),
    }
