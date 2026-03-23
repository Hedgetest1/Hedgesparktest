"""
market_lookup.py — /market-lookup/top endpoint.

Product boundary
----------------
GET /market-lookup/top is a Pro-only endpoint.
Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

Why there is no Lite split
--------------------------
Unlike surfaces with a genuine Lite/Pro field boundary (e.g. opportunities
where `explanation` is Lite and `human_action` is Pro, or alerts where
`message` is Lite and `action` is Pro), market lookup has no diagnostic
layer worth exposing to Lite subscribers.

The fields that might appear Lite-safe (`lookup_status`, `lookup_confidence`)
are implementation details of the market analysis pipeline — they carry no
actionable meaning to a merchant without the surrounding analysis
(`comparable_presence`, `uniqueness_hint`, `market_summary`).  The analysis
itself is a Pro-tier intelligence product: it tells the merchant whether
their product has comparable alternatives in the market and what to do about
it.  There is no honest "what is happening" observation to return to Lite
that is separate from the prescriptive layer.

The model encodes this intent: plan_required defaults to "pro" for every
row written to the market_lookup (or unique_product_detection) table.

This surface is structurally identical to /price-intelligence/top:
  - plan_required="pro" at the data layer for every row
  - all meaningful fields are prescriptive or pro-tier analytical
  - no row variant safe for Lite callers exists
  - the dedicated endpoint is not called by the main frontend dashboard
    (market lookup data flows through /dashboard/overview/pro)

To add a Lite-safe field in the future:
  If a genuinely diagnostic market field is added (e.g. a simple "we found
  X competitors" count that is meaningful without the full analysis), create
  a /market-lookup/top/lite route and strip the prescriptive fields there,
  following the pattern in live_alerts.py.  Update this docstring.

Request
-------
    GET /market-lookup/top?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON list of market lookup records, ordered by lookup_confidence
             descending, limit 20.
    400 if shop param is missing or invalid (from require_shop, composed
        inside require_pro_plan).
    403 if the shop does not have an active Pro plan.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.models.market_lookup import MarketLookup

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pro route — GET /market-lookup/top
#
# Entire endpoint is Pro-only.  No Lite subset exists — see module docstring.
# ---------------------------------------------------------------------------
@router.get("/market-lookup/top")
def top_market_lookup(
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Pro market lookup — full response, backend-enforced.

    Returns market analysis records for the shop ordered by lookup_confidence
    descending.  All fields (market_summary, recommended_next_step,
    comparable_presence, uniqueness_hint) are Pro-tier analytical or
    prescriptive content.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.
    """
    results = (
        db.query(MarketLookup)
        .filter(MarketLookup.shop_domain == shop)
        .order_by(MarketLookup.lookup_confidence.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "lookup_status": r.lookup_status,
            "comparable_presence": r.comparable_presence,
            "uniqueness_hint": r.uniqueness_hint,
            "lookup_confidence": r.lookup_confidence,
            "market_summary": r.market_summary,
            "recommended_next_step": r.recommended_next_step,
            "plan_required": r.plan_required,
        }
        for r in results
    ]
