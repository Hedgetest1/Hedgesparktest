"""
product_metrics.py — GET /products/metrics

Returns pre-aggregated per-product behavioral metrics for a shop, sorted
by 24-hour view count descending.  Limited to the top 20 products.

Source table: product_metrics (written by aggregation_worker every 5 min).

Request
-------
    GET /products/metrics?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — ProductMetricsResponse JSON

    shop_domain   str
    count         int   number of products returned (0 – 20)
    products      list of ProductMetricsRow:

      product_url             str
      views_24h               int
      views_7d                int
      unique_visitors_24h     int
      unique_visitors_7d      int
      return_visitor_count_7d int
      cart_conversions_24h    int
      avg_dwell_24h           float | null
      avg_scroll_24h          float | null

      cart_abandonment_rate   float | null
        (views_24h - cart_conversions_24h) / views_24h
        Null when views_24h == 0.

      return_visitor_rate     float | null
        return_visitor_count_7d / unique_visitors_7d
        Null when unique_visitors_7d == 0.

      engagement_score        float | null
        (avg_dwell_24h / 60) * 0.5 + (avg_scroll_24h / 100) * 0.5
        Null when both avg_dwell_24h and avg_scroll_24h are null.
        When only one is null, the missing component contributes 0.

    400 if shop param is missing or invalid.
    401 if API key is wrong.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_api_key, require_shop
from app.models.product_metrics import ProductMetrics
from app.schemas.product_metrics import ProductMetricsResponse, ProductMetricsRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])

_LIMIT = 20


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------

def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Computed field helpers
# ---------------------------------------------------------------------------

def _cart_abandonment_rate(views_24h: int, cart_conversions_24h: int) -> float | None:
    """
    Fraction of product views that did NOT result in a cart event.

    Returns None when views_24h == 0 — the rate is mathematically undefined,
    not zero.  A value of 0.0 means every viewer added to cart.
    """
    if views_24h == 0:
        return None
    abandoned = views_24h - cart_conversions_24h
    # Clamp to [0, 1]: cart_conversions_24h can theoretically exceed views_24h
    # if a visitor added to cart via a non-product-page path.
    return round(max(0.0, min(1.0, abandoned / views_24h)), 4)


def _return_visitor_rate(
    return_visitor_count_7d: int,
    unique_visitors_7d: int,
) -> float | None:
    """
    Fraction of 7-day unique visitors who viewed the product on 2+ days.

    Returns None when unique_visitors_7d == 0.
    Clamped to [0, 1].
    """
    if unique_visitors_7d == 0:
        return None
    return round(min(1.0, return_visitor_count_7d / unique_visitors_7d), 4)


def _engagement_score(
    avg_dwell_24h: float | None,
    avg_scroll_24h: float | None,
) -> float | None:
    """
    Composite engagement score in [0, 1].

    Formula: (avg_dwell_24h / 60) * 0.5 + (avg_scroll_24h / 100) * 0.5

    - avg_dwell_24h is normalised over 60 seconds (median engaged dwell).
    - avg_scroll_24h is a percentage (0–100), normalised to 0–1.
    - Each component is clamped to [0, 1] before weighting so outliers
      (e.g. dwell > 60 s) don't push the score above 1.

    Returns None when both inputs are NULL — there is no engagement data.
    When only one input is NULL it contributes 0 to the score rather than
    making the whole result None, so partial data still produces a signal.
    """
    if avg_dwell_24h is None and avg_scroll_24h is None:
        return None

    dwell_component = min(1.0, (avg_dwell_24h or 0.0) / 60.0) * 0.5
    scroll_component = min(1.0, (avg_scroll_24h or 0.0) / 100.0) * 0.5

    return round(dwell_component + scroll_component, 4)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/metrics", response_model=ProductMetricsResponse)
def get_product_metrics(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
    db: Session = Depends(_get_db),
) -> ProductMetricsResponse:
    """
    Return pre-aggregated behavioral metrics for the top 20 products of a shop.

    Data is read from product_metrics, which is refreshed every 5 minutes by
    the aggregation worker.  No live event queries are performed here.

    Sorted by views_24h DESC so the most active products appear first.
    """
    rows = (
        db.query(ProductMetrics)
        .filter(ProductMetrics.shop_domain == shop)
        .order_by(ProductMetrics.views_24h.desc())
        .limit(_LIMIT)
        .all()
    )

    products: list[ProductMetricsRow] = []
    for row in rows:
        views_24h = int(row.views_24h or 0)
        views_7d = int(row.views_7d or 0)
        unique_24h = int(row.unique_visitors_24h or 0)
        unique_7d = int(row.unique_visitors_7d or 0)
        return_7d = int(row.return_visitor_count_7d or 0)
        cart_24h = int(row.cart_conversions_24h or 0)
        dwell = float(row.avg_dwell_24h) if row.avg_dwell_24h is not None else None
        scroll = float(row.avg_scroll_24h) if row.avg_scroll_24h is not None else None

        products.append(
            ProductMetricsRow(
                product_url=row.product_url,
                views_24h=views_24h,
                views_7d=views_7d,
                unique_visitors_24h=unique_24h,
                unique_visitors_7d=unique_7d,
                return_visitor_count_7d=return_7d,
                cart_conversions_24h=cart_24h,
                avg_dwell_24h=dwell,
                avg_scroll_24h=scroll,
                cart_abandonment_rate=_cart_abandonment_rate(views_24h, cart_24h),
                return_visitor_rate=_return_visitor_rate(return_7d, unique_7d),
                engagement_score=_engagement_score(dwell, scroll),
            )
        )

    return ProductMetricsResponse(
        shop_domain=shop,
        count=len(products),
        products=products,
    )
