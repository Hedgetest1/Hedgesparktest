"""
proof_engine.py — Unified proof-of-value engine for merchants.

Combines:
  1. Holdout lift (quasi-experimental) — incremental revenue from nudges
  2. Action proof (before/after) — revenue changes from executed actions

Into a single, trust-calibrated merchant-facing proof report that answers:
  "How much money has HedgeSpark made me?"

Public interface:
    get_proof_report(db, shop_domain, window_hours=168) -> dict

Design principles:
  - Never overclaim: cap incremental revenue at actual store revenue
  - Honest confidence: communicate sample size limitations clearly
  - Revenue-first: translate CVR lift into dollar amounts
  - Additive: holdout lift + action proof are separate evidence streams
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.nudge_measurement import (
    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    MIN_SAMPLE_PER_GROUP,
    get_nudge_lift_report,
)
from app.services.action_proof import get_proof_summary
from app.services.revenue_metrics import get_shop_aov, get_shop_currency

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confidence levels — honest, graduated communication
# ---------------------------------------------------------------------------

_CONFIDENCE_LEVELS = {
    "strong": {
        "label": "High confidence",
        "description": "Large sample, statistically significant lift measured against a control group.",
        "emoji_safe": True,
    },
    "moderate": {
        "label": "Moderate confidence",
        "description": "Meaningful sample with positive signal, but not yet statistically significant.",
        "emoji_safe": True,
    },
    "early": {
        "label": "Early signal",
        "description": "Positive trend visible, but sample size is still small. Keep measuring.",
        "emoji_safe": False,
    },
    "insufficient": {
        "label": "Measuring",
        "description": "Not enough data for reliable conclusions yet. Results will improve over time.",
        "emoji_safe": False,
    },
}

# Minimum total visitors (exposed + holdout) before we show any revenue number
_MIN_VISITORS_FOR_REVENUE = 50

# Cap: incremental revenue cannot exceed this fraction of attributed revenue
_MAX_INCREMENTAL_FRACTION = 0.80


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Core proof report
# ---------------------------------------------------------------------------

def get_proof_report(
    db: Session,
    shop_domain: str,
    window_hours: int = 168,
) -> dict:
    """
    Build a unified proof-of-value report for one merchant.

    Returns:
        {
            "has_proof":               bool,
            "holdout_proof":           dict,  # from nudge experiments
            "action_proof":            dict,  # from before/after snapshots
            "total_incremental_revenue": float,
            "confidence":              dict,
            "headline":                str,   # merchant-facing summary
            "detail":                  str,   # supporting explanation
            "trust_note":              str,   # honest methodology note
            "currency":                str,
            "generated_at":            str,
        }
    """
    window_hours = max(1, min(window_hours, 168))

    holdout_proof = _build_holdout_proof(db, shop_domain, window_hours)
    action_proof_data = get_proof_summary(db, shop_domain, days=30)

    # Sum incremental revenue from both evidence streams
    holdout_incremental = holdout_proof.get("incremental_revenue", 0.0)
    action_incremental = _safe_action_revenue(action_proof_data)

    # Apply store-revenue cap: never claim more than the store actually earned
    store_revenue_7d = _store_revenue(db, shop_domain, days=7)
    total_incremental = holdout_incremental + action_incremental
    if store_revenue_7d > 0:
        cap = store_revenue_7d * _MAX_INCREMENTAL_FRACTION
        total_incremental = min(total_incremental, cap)

    # Determine overall confidence
    confidence = _assess_confidence(holdout_proof, action_proof_data)

    # Build merchant-facing messages
    currency = holdout_proof.get("currency", "USD")
    headline, detail = _build_messages(
        holdout_proof, action_proof_data,
        total_incremental, currency, confidence,
    )

    trust_note = _build_trust_note(holdout_proof, confidence)

    has_proof = (
        holdout_proof.get("has_data", False)
        or len(action_proof_data.get("improvements", [])) > 0
    )

    return {
        "has_proof": has_proof,
        "holdout_proof": holdout_proof,
        "action_proof": {
            "actions_measured": action_proof_data.get("actions_measured", 0),
            "improvements_count": len(action_proof_data.get("improvements", [])),
            "improvements": action_proof_data.get("improvements", [])[:3],
            "total_revenue_delta": action_proof_data.get("total_revenue_delta", 0),
        },
        "total_incremental_revenue": round(total_incremental, 2),
        "confidence": confidence,
        "headline": headline,
        "detail": detail,
        "trust_note": trust_note,
        "currency": currency,
        "store_revenue_7d": round(store_revenue_7d, 2),
        "generated_at": _now().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Holdout proof — aggregate across all nudges with experiment data
# ---------------------------------------------------------------------------

def _build_holdout_proof(
    db: Session, shop_domain: str, window_hours: int,
) -> dict:
    """Aggregate holdout lift across all nudges, compute incremental revenue."""
    empty = {
        "has_data": False,
        "nudges_measured": 0,
        "total_exposed": 0,
        "total_holdout": 0,
        "pooled_exposed_cvr": 0.0,
        "pooled_holdout_cvr": 0.0,
        "lift_pct": None,
        "incremental_revenue": 0.0,
        "attributed_revenue": 0.0,
        "currency": "USD",
        "nudges": [],
    }

    try:
        nudge_rows = db.execute(
            text("""
                SELECT DISTINCT an.id, an.product_url, an.action_type, an.holdout_pct
                FROM active_nudges an
                JOIN nudge_events ne
                    ON ne.nudge_id   = an.id
                   AND ne.shop_domain = an.shop_domain
                WHERE an.shop_domain = :shop
                  AND an.holdout_pct  > 0
                  AND ne.event_type   = 'holdout_assigned'
                ORDER BY an.id DESC
                LIMIT 20
            """),
            {"shop": shop_domain},
        ).fetchall()
    except Exception as exc:
        log.error("proof_engine: nudge query failed shop=%s: %s", shop_domain, exc)
        return empty

    if not nudge_rows:
        return empty

    total_exposed = 0
    total_holdout = 0
    weighted_exp_cvr = 0.0
    weighted_hld_cvr = 0.0
    total_attributed = 0.0
    total_incremental = 0.0
    currency = "USD"
    nudge_details = []
    valid = 0

    aov = get_shop_aov(db, shop_domain)

    for row in nudge_rows:
        nudge_id = int(row[0])
        product_url = str(row[1])
        action_type = str(row[2])

        try:
            lift = get_nudge_lift_report(db, shop_domain, nudge_id, window_hours)
        except Exception:
            continue

        if not lift.get("holdout_active"):
            continue

        exp_count = int(lift.get("exposed_count", 0))
        hld_count = int(lift.get("holdout_count", 0))
        exp_cvr = float(lift.get("exposed_cvr", 0))
        hld_cvr = float(lift.get("holdout_cvr", 0))

        if exp_count == 0 and hld_count == 0:
            continue

        # Revenue from the lift report
        rev_lift = lift.get("revenue_lift", {})
        attributed = float(rev_lift.get("exposed_revenue", 0))
        est_incremental = float(rev_lift.get("estimated_incremental_revenue") or 0)
        curr = str(rev_lift.get("currency", "USD"))

        # If the lift report didn't compute incremental, estimate from CVR
        if est_incremental == 0 and exp_cvr > hld_cvr and exp_count > 0:
            cvr_delta = exp_cvr - hld_cvr
            est_incremental = cvr_delta * exp_count * aov

        total_exposed += exp_count
        total_holdout += hld_count
        weighted_exp_cvr += exp_cvr * exp_count
        weighted_hld_cvr += hld_cvr * hld_count
        total_attributed += attributed
        total_incremental += max(est_incremental, 0)  # never negative
        currency = curr
        valid += 1

        p_value = float(lift.get("p_value", 1.0))
        significance = str(lift.get("significance", ""))

        nudge_details.append({
            "nudge_id": nudge_id,
            "product_url": product_url,
            "action_type": action_type,
            "exposed_count": exp_count,
            "holdout_count": hld_count,
            "exposed_cvr": round(exp_cvr, 4),
            "holdout_cvr": round(hld_cvr, 4),
            "lift_pct": lift.get("estimated_lift_pct"),
            "incremental_revenue": round(max(est_incremental, 0), 2),
            "attributed_revenue": round(attributed, 2),
            "p_value": round(p_value, 4),
            "significance": significance,
            "currency": curr,
        })

    if valid == 0:
        return empty

    pooled_exp = round(weighted_exp_cvr / total_exposed, 4) if total_exposed > 0 else 0.0
    pooled_hld = round(weighted_hld_cvr / total_holdout, 4) if total_holdout > 0 else 0.0

    lift_pct = None
    if pooled_hld > 0:
        lift_pct = round(((pooled_exp - pooled_hld) / pooled_hld) * 100, 1)

    # Guard: if total sample is too small, zero out incremental
    if (total_exposed + total_holdout) < _MIN_VISITORS_FOR_REVENUE:
        total_incremental = 0.0

    return {
        "has_data": True,
        "nudges_measured": valid,
        "total_exposed": total_exposed,
        "total_holdout": total_holdout,
        "pooled_exposed_cvr": pooled_exp,
        "pooled_holdout_cvr": pooled_hld,
        "lift_pct": lift_pct,
        "incremental_revenue": round(total_incremental, 2),
        "attributed_revenue": round(total_attributed, 2),
        "currency": currency,
        "nudges": nudge_details,
    }


# ---------------------------------------------------------------------------
# Action proof helpers
# ---------------------------------------------------------------------------

def _safe_action_revenue(action_proof: dict) -> float:
    """Extract positive revenue delta from action proof, conservatively."""
    delta = action_proof.get("total_revenue_delta", 0)
    # Only count positive deltas — negative deltas mean the action didn't help
    return max(float(delta), 0.0)


# ---------------------------------------------------------------------------
# Store revenue — for capping claims
# ---------------------------------------------------------------------------

def _store_revenue(db: Session, shop: str, days: int) -> float:
    currency = get_shop_currency(db, shop)
    try:
        row = db.execute(
            text("""
                SELECT COALESCE(SUM(total_price), 0)
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND (:currency IS NULL OR currency = :currency)
            """),
            {"shop": shop, "days": days, "currency": currency},
        ).fetchone()
        return float(row[0] or 0) if row else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Confidence assessment
# ---------------------------------------------------------------------------

def _assess_confidence(holdout: dict, action_proof: dict) -> dict:
    """
    Determine overall confidence level from evidence quality.

    Factors:
      - Sample size (exposed + holdout)
      - Statistical significance of lift
      - Number of actions measured
    """
    if not holdout.get("has_data") and not action_proof.get("improvements"):
        return {**_CONFIDENCE_LEVELS["insufficient"], "level": "insufficient"}

    total_sample = holdout.get("total_exposed", 0) + holdout.get("total_holdout", 0)

    # Check if any nudge has significant results
    has_significant = False
    best_p = 1.0
    for n in holdout.get("nudges", []):
        p = n.get("p_value", 1.0)
        if p < best_p:
            best_p = p
        if p < 0.05:
            has_significant = True

    if has_significant and total_sample >= 200:
        level = "strong"
    elif (best_p < 0.10 and total_sample >= 100) or (
        holdout.get("lift_pct") is not None
        and holdout["lift_pct"] > 0
        and total_sample >= 100
    ):
        level = "moderate"
    elif total_sample >= _MIN_VISITORS_FOR_REVENUE or len(action_proof.get("improvements", [])) > 0:
        level = "early"
    else:
        level = "insufficient"

    return {**_CONFIDENCE_LEVELS[level], "level": level}


# ---------------------------------------------------------------------------
# Merchant-facing messaging — trust-calibrated
# ---------------------------------------------------------------------------

def _build_messages(
    holdout: dict,
    action_proof: dict,
    total_incremental: float,
    currency: str,
    confidence: dict,
) -> tuple[str, str]:
    """Build headline + detail strings. Revenue-first, honest, compelling."""
    level = confidence["level"]
    lift_pct = holdout.get("lift_pct")
    nudges_measured = holdout.get("nudges_measured", 0)
    actions_improved = len(action_proof.get("improvements", []))

    # --- Headline (short, dashboard-worthy) ---
    if total_incremental > 0 and level in ("strong", "moderate"):
        headline = (
            f"Estimated +{currency} {total_incremental:,.0f} incremental revenue this week"
        )
    elif lift_pct is not None and lift_pct > 0:
        headline = f"Your nudges are driving {lift_pct:+.1f}% more conversions"
    elif actions_improved > 0:
        delta = action_proof.get("total_revenue_delta", 0)
        if delta > 0:
            headline = f"{actions_improved} action{'s' if actions_improved != 1 else ''} improved results (+{currency} {delta:,.0f})"
        else:
            headline = f"{actions_improved} action{'s' if actions_improved != 1 else ''} produced measurable improvement"
    elif nudges_measured > 0:
        headline = f"Measuring impact across {nudges_measured} nudge{'s' if nudges_measured != 1 else ''}"
    else:
        headline = "Building your proof-of-impact baseline"

    # --- Detail (1-2 sentences, email-worthy) ---
    parts = []

    if holdout.get("has_data") and lift_pct is not None:
        exp = holdout["total_exposed"]
        hld = holdout["total_holdout"]
        exp_cvr = holdout["pooled_exposed_cvr"]
        hld_cvr = holdout["pooled_holdout_cvr"]

        if lift_pct > 0:
            parts.append(
                f"Visitors who saw your nudges converted at {exp_cvr * 100:.2f}% "
                f"vs {hld_cvr * 100:.2f}% for the control group "
                f"({exp:,} exposed, {hld:,} control)."
            )
        elif lift_pct == 0:
            parts.append(
                f"No measurable difference between nudge recipients ({exp_cvr * 100:.2f}%) "
                f"and control group ({hld_cvr * 100:.2f}%). Try different messaging."
            )
        else:
            parts.append(
                f"Control group slightly outperformed ({hld_cvr * 100:.2f}% vs "
                f"{exp_cvr * 100:.2f}%). Consider revising nudge content."
            )

    if actions_improved > 0:
        top = action_proof["improvements"][0]
        parts.append(top.get("summary", ""))

    if not parts:
        parts.append(
            "As your nudges accumulate visitor data, this report will show "
            "the revenue impact measured against a control group."
        )

    detail = " ".join(parts)

    return headline, detail


def _build_trust_note(holdout: dict, confidence: dict) -> str:
    """Methodology transparency — always shown."""
    level = confidence["level"]

    if not holdout.get("has_data"):
        return (
            "Revenue impact is measured by comparing product metrics before and after "
            "actions are taken. Results are observational, not causal."
        )

    base = (
        "Lift is measured using a holdout control group — a percentage of eligible "
        "visitors are randomly withheld from seeing nudges, creating a baseline for comparison."
    )

    if level == "strong":
        return base + " Results are statistically significant."
    elif level == "moderate":
        return base + " Results show a positive trend but have not yet reached full statistical significance."
    elif level == "early":
        return base + " Sample size is still building — treat these numbers as directional, not definitive."
    else:
        return base + " More data is needed before drawing conclusions."


# ---------------------------------------------------------------------------
# Digest-optimized proof summary
# ---------------------------------------------------------------------------

def get_digest_proof(db: Session, shop_domain: str) -> dict:
    """
    Compact proof summary optimized for the weekly digest email.

    Returns:
        {
            "has_proof":               bool,
            "headline":                str,
            "detail":                  str,
            "incremental_revenue":     float,
            "currency":                str,
            "confidence_label":        str,
            "nudges_measured":         int,
            "actions_improved":        int,
            "trust_note":              str,
            "show_revenue":            bool,  # only true when confidence >= moderate
        }
    """
    report = get_proof_report(db, shop_domain, window_hours=168)

    level = report["confidence"]["level"]
    show_revenue = (
        report["total_incremental_revenue"] > 0
        and level in ("strong", "moderate")
    )

    return {
        "has_proof": report["has_proof"],
        "headline": report["headline"],
        "detail": report["detail"],
        "incremental_revenue": report["total_incremental_revenue"] if show_revenue else 0.0,
        "currency": report["currency"],
        "confidence_label": report["confidence"]["label"],
        "nudges_measured": report["holdout_proof"].get("nudges_measured", 0),
        "actions_improved": report["action_proof"].get("improvements_count", 0),
        "trust_note": report["trust_note"],
        "show_revenue": show_revenue,
    }
