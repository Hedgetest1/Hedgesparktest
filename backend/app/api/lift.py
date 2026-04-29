"""
lift.py — Aggregated holdout lift report (Pro only).

GET /pro/lift?shop=&window_hours=

Returns a shop-level summary aggregating lift across ALL nudges that have
holdout data.  This is the "proof of value" endpoint — answers the single
most important question for merchant retention:

    "Did WishSpark's nudges actually drive more revenue than no nudges?"

Individual nudge stats are at GET /pro/nudges/{id}/stats.
This endpoint gives the store-wide picture.

Lift model
----------
For each nudge with holdout_pct > 0:
  - exposed_group:  visitors who saw the nudge (render_allowed=true)
  - holdout_group:  visitors suppressed by holdout (holdout_assigned events)
  - lift_pct:       (exposed_cvr - holdout_cvr) / holdout_cvr × 100

Store-level aggregation:
  - Total exposed_count, holdout_count
  - Pooled exposed_cvr, holdout_cvr (weighted by group size)
  - Total attributed_revenue (from exposed converters)
  - Lift narrative: "Your nudges drove X% more purchases than the control group"

If no holdout data exists, returns a clear explanation with a setup guide.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_scale_session
from app.services.nudge_measurement import (
    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    get_nudge_lift_report,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/lift", tags=["lift"])


# ---------------------------------------------------------------------------
# Response models — emitted into OpenAPI, consumed by dashboard codegen.
# See reference_openapi_codegen.md memory for the migration pattern.
# ---------------------------------------------------------------------------


class LiftNudgeBreakdown(BaseModel):
    """Per-nudge lift row inside the aggregate Lift Report."""
    nudge_id: int
    product_url: str
    action_type: str
    holdout_pct: int
    exposed_count: int
    holdout_count: int
    exposed_cvr: float = Field(..., ge=0.0)
    holdout_cvr: float = Field(..., ge=0.0)
    lift_pct: float | None = None
    attributed_revenue: float
    currency: str


class LiftReportResponse(BaseModel):
    """GET /pro/lift aggregate response shape — feeds the Holdout Proof cassettone."""
    has_experiment_data: bool
    nudges_measured: int
    total_exposed: int
    total_holdout: int
    exposed_cvr: float = Field(..., ge=0.0)
    holdout_cvr: float = Field(..., ge=0.0)
    lift_pct: float | None = None
    attributed_revenue: float
    currency: str
    verdict: str
    nudge_breakdown: list[LiftNudgeBreakdown]
    window_hours: int
    generated_at: str




@router.get(
    "",
    response_model=LiftReportResponse,
    response_model_exclude_none=False,
)
def get_store_lift_summary(
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """
    Aggregated holdout lift across all nudges with experiment data.

    Returns:
        {
            "has_experiment_data":  bool,
            "nudges_measured":      int,
            "total_exposed":        int,
            "total_holdout":        int,
            "exposed_cvr":          float,
            "holdout_cvr":          float,
            "lift_pct":             float | None,
            "attributed_revenue":   float,
            "currency":             str,
            "verdict":              str,       # human-readable summary
            "nudge_breakdown":      list[dict],
            "generated_at":         str,
        }
    """
    window_hours = max(1, min(window_hours, 168))

    # Get all nudges for this shop that have holdout data
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
            {"shop": shop},
        ).fetchall()

    except Exception as exc:
        log.error("lift: query failed shop=%s: %s", shop, exc)
        return _no_data_response()

    if not nudge_rows:
        return _no_data_response()

    # Aggregate lift across all nudges
    total_exposed     = 0
    total_holdout     = 0
    total_exposed_cvr = 0.0
    total_holdout_cvr = 0.0
    total_revenue     = 0.0
    from app.services.revenue_metrics import get_shop_currency
    currency          = get_shop_currency(db, shop) or "USD"
    nudge_breakdown   = []

    valid_nudges = 0

    for row in nudge_rows:
        nudge_id    = int(row[0])
        product_url = str(row[1])
        action_type = str(row[2])
        holdout_pct = int(row[3])

        try:
            lift_report = get_nudge_lift_report(db, shop, nudge_id, window_hours)
        except Exception:
            continue

        # get_nudge_lift_report() returns `holdout_active` (bool).
        # Field was historically called `has_holdout_data` — fixed 2026-04-10.
        if not lift_report.get("holdout_active"):
            continue

        exp_count = int(lift_report.get("exposed_count", 0))
        hld_count = int(lift_report.get("holdout_count", 0))
        exp_cvr   = float(lift_report.get("exposed_cvr", 0))
        hld_cvr   = float(lift_report.get("holdout_cvr", 0))
        rev       = float(lift_report.get("attributed_revenue", 0))
        curr      = str(lift_report.get("currency", "USD"))

        if exp_count == 0 and hld_count == 0:
            continue

        total_exposed     += exp_count
        total_holdout     += hld_count
        total_exposed_cvr += exp_cvr * exp_count   # weighted sum
        total_holdout_cvr += hld_cvr * hld_count
        total_revenue     += rev
        currency           = curr
        valid_nudges      += 1

        lift_pct = lift_report.get("lift_pct")

        nudge_breakdown.append({
            "nudge_id":           nudge_id,
            "product_url":        product_url,
            "action_type":        action_type,
            "holdout_pct":        holdout_pct,
            "exposed_count":      exp_count,
            "holdout_count":      hld_count,
            "exposed_cvr":        round(exp_cvr, 4),
            "holdout_cvr":        round(hld_cvr, 4),
            "lift_pct":           lift_pct,
            "attributed_revenue": round(rev, 2),
            "currency":           curr,
        })

    if valid_nudges == 0:
        return _no_data_response()

    # Weighted average CVRs
    pooled_exp_cvr = round(
        total_exposed_cvr / total_exposed if total_exposed > 0 else 0.0, 4
    )
    pooled_hld_cvr = round(
        total_holdout_cvr / total_holdout if total_holdout > 0 else 0.0, 4
    )

    # Aggregate lift
    agg_lift_pct = None
    if pooled_hld_cvr > 0:
        agg_lift_pct = round(
            ((pooled_exp_cvr - pooled_hld_cvr) / pooled_hld_cvr) * 100, 1
        )

    # Build verdict sentence
    verdict = _build_verdict(
        valid_nudges, total_exposed, total_holdout,
        pooled_exp_cvr, pooled_hld_cvr, agg_lift_pct, total_revenue, currency,
    )

    return {
        "has_experiment_data": True,
        "nudges_measured":     valid_nudges,
        "total_exposed":       total_exposed,
        "total_holdout":       total_holdout,
        "exposed_cvr":         pooled_exp_cvr,
        "holdout_cvr":         pooled_hld_cvr,
        "lift_pct":            agg_lift_pct,
        "attributed_revenue":  round(total_revenue, 2),
        "currency":            currency,
        "verdict":             verdict,
        "nudge_breakdown":     nudge_breakdown,
        "window_hours":        window_hours,
        "generated_at":        datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


def _build_verdict(
    nudge_count: int,
    exposed: int,
    holdout: int,
    exp_cvr: float,
    hld_cvr: float,
    lift_pct: float | None,
    revenue: float,
    currency: str,
) -> str:
    if lift_pct is None:
        return (
            f"Measured {nudge_count} nudge(s) with holdout control groups. "
            f"{exposed:,} visitors saw nudges vs {holdout:,} in control. "
            "Insufficient conversion data for lift calculation — keep measuring."
        )

    if lift_pct > 0:
        rev_str = f" — ${revenue:,.0f} {currency} in attributed revenue" if revenue > 0 else ""
        return (
            f"Your nudges drove {lift_pct:+.1f}% more conversions than the control group "
            f"({exposed:,} exposed vs {holdout:,} holdout visitors){rev_str}. "
            "This is a statistically controlled measurement, not an estimate."
        )
    elif lift_pct == 0:
        return (
            f"No measurable lift detected across {nudge_count} nudge(s). "
            "The control group converted at the same rate. Try different copy strategies."
        )
    else:
        return (
            f"Nudges underperformed by {abs(lift_pct):.1f}% vs control across {nudge_count} nudge(s). "
            "Consider revising copy or targeting strategy."
        )


def _no_data_response() -> dict:
    return {
        "has_experiment_data": False,
        "nudges_measured":     0,
        "total_exposed":       0,
        "total_holdout":       0,
        "exposed_cvr":         0.0,
        "holdout_cvr":         0.0,
        "lift_pct":            None,
        "attributed_revenue":  0.0,
        "currency":            "USD",
        "verdict": (
            "No holdout experiment data yet. Enable holdout on a nudge using "
            "PATCH /pro/nudges/{id}/holdout to start measuring lift against a control group."
        ),
        "nudge_breakdown": [],
        "window_hours":    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
        "generated_at":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }
