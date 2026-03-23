"""
GET /analytics/funnel — Basic conversion funnel.

Computes unique-visitor counts at each stage of the purchase funnel:
  product_view → add_to_cart → checkout → purchase

Returns each step with:
  - count: distinct visitors who reached this step
  - pct:   percentage relative to product_view (top of funnel)
  - drop_off: percentage of the previous step that did not continue

All computed directly from the events table — no separate funnel table needed.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round((numerator / denominator) * 100, 1)


@router.get("/funnel")
def conversion_funnel(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    query = text("""
        SELECT
            COUNT(DISTINCT visitor_id) FILTER (
                WHERE event_type IN ('product_view', 'page_view')
            ) AS product_views,

            COUNT(DISTINCT visitor_id) FILTER (
                WHERE event_type = 'add_to_cart'
            ) AS add_to_cart,

            COUNT(DISTINCT visitor_id) FILTER (
                WHERE event_type IN (
                    'checkout_start', 'checkout_begin',
                    'begin_checkout', 'checkout'
                )
            ) AS checkout,

            COUNT(DISTINCT visitor_id) FILTER (
                WHERE event_type IN (
                    'purchase', 'order_placed', 'order_completed'
                )
            ) AS purchase

        FROM events
        WHERE shop_domain = :shop_domain
    """)

    with engine.begin() as conn:
        row = conn.execute(query, {"shop_domain": shop}).mappings().first()

    if not row:
        return {"steps": []}

    views    = int(row["product_views"] or 0)
    cart     = int(row["add_to_cart"]   or 0)
    checkout = int(row["checkout"]      or 0)
    purchase = int(row["purchase"]      or 0)

    steps = [
        {
            "step":     "product_view",
            "label":    "Product View",
            "count":    views,
            "pct":      100.0 if views > 0 else None,
            "drop_off": None,
        },
        {
            "step":     "add_to_cart",
            "label":    "Add to Cart",
            "count":    cart,
            "pct":      _pct(cart, views),
            "drop_off": _pct(views - cart, views),
        },
        {
            "step":     "checkout",
            "label":    "Checkout",
            "count":    checkout,
            "pct":      _pct(checkout, views),
            "drop_off": _pct(cart - checkout, cart) if cart > 0 else None,
        },
        {
            "step":     "purchase",
            "label":    "Purchase",
            "count":    purchase,
            "pct":      _pct(purchase, views),
            "drop_off": _pct(checkout - purchase, checkout) if checkout > 0 else None,
        },
    ]

    return {"steps": steps}
