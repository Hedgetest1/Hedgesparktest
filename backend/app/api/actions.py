"""
actions.py — GET /actions/candidates/pro

Product boundary
----------------
GET /actions/candidates/pro is a Pro-only endpoint.
Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

This surface is the first layer of the Action Engine. It produces a ranked
list of action candidates derived from existing Pro-tier data signals.
It does NOT execute any action — it is a read-only recommendation surface.

No Lite split exists here. Every field is prescriptive or a direct output of
the Pro-tier inference pipeline:

  action_type, action_hint   — what to do (prescriptive, Action Layer)
  confidence, urgency        — ranking inputs derived from Pro-tier scoring
  expected_loss, loss_band   — revenue impact estimates (Pro-tier pipeline)
  conversion_probability     — output of infer_conversion_outcome (Pro-tier)
  estimated_uplift           — output of simulate_action_uplift (Pro-tier)
  ready_now                  — composite gate; meaningless without the above

Data-source dependency chain (all Pro-only or Pro-tier):
  opportunity_signals        — behavioral triggers (rule-based, shared)
  product_metrics            — traffic and engagement aggregates (shared)
  price_intelligence         — price positioning (Pro-only table)
  unique_product_detection   — product uniqueness (Pro-only table)
  visitor_product_state      — behavioral aggregates for enrichment
  market_lookup              — uniqueness and comparability (Pro-only table)

Request
-------
    GET /actions/candidates/pro?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON object:
        shop_domain       str
        generated_at      str (ISO 8601)
        total_candidates  int
        candidates        list[dict]   at most 20 items, sorted by rank_score DESC

    Each candidate contains:
        rank                   int
        product_url            str
        action_type            str    CRO_FIX | SCARCITY_NUDGE | PRICE_TEST |
                                      RETARGET_HOT_TRAFFIC | FLASH_INCENTIVE
        reason                 str    metric-interpolated explanation
        supporting_signals     list[str]
        confidence             float  [0–1]
        urgency                float  [0–100]
        expected_loss          float  views_24h × conv_prob × AOV
        loss_band              str    LOW | MEDIUM | HIGH
        conversion_probability float  [0–1]
        time_to_conversion     str    IMMINENT_24H | LIKELY_3D | LIKELY_7D |
                                      LONGER_HORIZON | LOW_PROBABILITY
        estimated_uplift       float  expected conversion lift from the action
        source_systems         list[str]
        ready_now              bool   urgency >= 60 AND confidence >= 0.65
        action_hint            str    one-line prescriptive merchant instruction

    400 if shop param is missing or invalid.
    403 if the shop does not have an active Pro plan.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.services.action_candidates_engine import generate_action_candidates

router = APIRouter(prefix="/actions", tags=["actions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pro route — GET /actions/candidates/pro
#
# Entire endpoint is Pro-only. No Lite subset exists — see module docstring.
# ---------------------------------------------------------------------------

@router.get("/candidates/pro")
def action_candidates_pro(
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Pro action candidates — ranked list of actionable signals, backend-enforced.

    Returns at most 20 action candidates derived from the current state of all
    Pro-tier data sources.  Each candidate represents a distinct (product, action)
    pair ranked by a composite of urgency, confidence, and expected revenue loss.

    This is a read-only surface. No actions are executed, no state is written.
    The response is recomputed on every request (no caching in v1).

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.
    """
    candidates = generate_action_candidates(shop_domain=shop, db=db)

    return {
        "shop_domain":      shop,
        "generated_at":     datetime.now(tz=timezone.utc).isoformat(),
        "total_candidates": len(candidates),
        "candidates":       candidates,
    }
