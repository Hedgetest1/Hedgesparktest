"""
price_intelligence.py — /price-intelligence/top endpoint.

Product boundary
----------------
GET /price-intelligence/top is a Pro-only endpoint.
Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

Unlike other Pro surfaces (opportunities, alerts, brief) there is NO Lite
diagnostic subset to expose.  Every field in this response is either:
  - prescriptive (recommended_price_action, intelligence_explanation,
    price_opportunity — what to do and why), or
  - derived from a proprietary pricing analysis that is a Pro-tier feature
    end-to-end (market_status, price_position, confidence_score only make
    sense in the context of the prescriptive layer).

The model itself encodes this intent: plan_required defaults to "pro" for
every row written to the price_intelligence table.  There is no row variant
safe to serve to Lite subscribers.

This is why there is no /price-intelligence/top/lite split and no field-
level stripping — this surface does not follow the Lite/Pro field-boundary
pattern used by alerts or opportunities.  The entire endpoint is Pro.

Note: the main frontend dashboard sources price intelligence from
GET /dashboard/overview (which bundles it via _build_price_intelligence()
and UI-gates it with <ProGate>).  That path is a separate concern — it
returns price intelligence data to all shops without backend plan enforcement.
Fixing /dashboard/overview is a broader step and is tracked separately.
This file only enforces the dedicated /price-intelligence/top endpoint.

Request
-------
    GET /price-intelligence/top?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON list of price intelligence records, ordered by
             confidence_score descending, limit 20.
    400 if shop param is missing or invalid (from require_shop, composed
        inside require_pro_plan).
    403 if the shop does not have an active Pro plan.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.models.price_intelligence import PriceIntelligence
from app.services.price_radar_service import evaluate_price

router = APIRouter()




# ---------------------------------------------------------------------------
# Pro route — GET /price-intelligence/top
#
# Entire endpoint is Pro-only.  No Lite subset exists — see module docstring.
# ---------------------------------------------------------------------------
@router.get("/price-intelligence/top")
def top_price_intelligence(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),
):
    """
    Pro price intelligence — full response, backend-enforced.

    Returns pricing analysis records for the shop ordered by confidence_score
    descending.  All fields (recommended_price_action, intelligence_explanation,
    price_opportunity) are prescriptive and Pro-only.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.
    """
    results = (
        db.query(PriceIntelligence)
        .filter(PriceIntelligence.shop_domain == shop)
        .order_by(PriceIntelligence.confidence_score.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "market_status": r.market_status,
            "price_position": r.price_position,
            "price_opportunity": r.price_opportunity,
            "recommended_price_action": r.recommended_price_action,
            "intelligence_explanation": r.intelligence_explanation,
            "confidence_score": r.confidence_score,
            "plan_required": r.plan_required,
        }
        for r in results
    ]


@router.post("/price-radar")
def price_radar(data: dict):
    result = evaluate_price(data.get("product_name"))
    return result
