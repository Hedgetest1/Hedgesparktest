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
from app.core.deps import require_merchant_session
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


def _cart_rate(conversions: int, views: int) -> float | None:
    """Cart conversion rate. None when views == 0."""
    if views == 0:
        return None
    return round(min(1.0, max(0.0, conversions / views)), 4)


def _cart_rate_trend(
    rate_24h: float | None,
    rate_7d: float | None,
) -> str | None:
    """
    Compare 24h cart rate to 7d average.
    Returns 'improving', 'declining', or 'stable'.
    None when either rate is undefined.
    """
    if rate_24h is None or rate_7d is None:
        return None
    if rate_7d < 0.005:
        # Not enough signal in 7d rate to compare meaningfully
        return None
    ratio = rate_24h / rate_7d if rate_7d > 0 else 0.0
    if ratio >= 1.3:
        return "improving"
    if ratio <= 0.7:
        return "declining"
    return "stable"


def _peak_conversion_label(
    peak_views: int,
    peak_carts: int,
    off_peak_views: int,
    off_peak_carts: int,
) -> str | None:
    """
    Determine if the peak time block converts significantly better or worse.
    Returns 'peak_converts_better', 'off_peak_converts_better', or None.
    """
    if peak_views < 5 or off_peak_views < 5:
        return None
    peak_rate = peak_carts / peak_views
    off_peak_rate = off_peak_carts / off_peak_views
    if peak_rate > 0 and off_peak_rate > 0:
        if peak_rate > off_peak_rate * 1.5:
            return "peak_converts_better"
        if off_peak_rate > peak_rate * 1.5:
            return "off_peak_converts_better"
    elif peak_rate > 0 and off_peak_rate == 0:
        return "peak_converts_better"
    elif off_peak_rate > 0 and peak_rate == 0:
        return "off_peak_converts_better"
    return None


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
    shop: str = Depends(require_merchant_session),
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
        cart_7d = int(row.cart_conversions_7d or 0)
        dwell = float(row.avg_dwell_24h) if row.avg_dwell_24h is not None else None
        scroll = float(row.avg_scroll_24h) if row.avg_scroll_24h is not None else None

        cart_rate_24h = _cart_rate(cart_24h, views_24h)
        cart_rate_7d = _cart_rate(cart_7d, views_7d)
        cart_rate_trend = _cart_rate_trend(cart_rate_24h, cart_rate_7d)

        # Time-of-day
        phv = int(row.peak_hour_views or 0)
        phc = int(row.peak_hour_carts or 0)
        ophv = int(row.off_peak_hour_views or 0)
        ophc = int(row.off_peak_hour_carts or 0)
        peak_conversion_label = _peak_conversion_label(phv, phc, ophv, ophc)

        # Session context
        lv = int(row.landing_views_24h or 0)
        bv = int(row.browsing_views_24h or 0)
        lc = int(row.landing_carts_24h or 0)
        bc = int(row.browsing_carts_24h or 0)

        products.append(
            ProductMetricsRow(
                product_url=row.product_url,
                views_24h=views_24h,
                views_7d=views_7d,
                unique_visitors_24h=unique_24h,
                unique_visitors_7d=unique_7d,
                return_visitor_count_7d=return_7d,
                cart_conversions_24h=cart_24h,
                cart_conversions_7d=cart_7d,
                avg_dwell_24h=dwell,
                avg_scroll_24h=scroll,
                views_mobile=int(row.views_mobile or 0),
                views_desktop=int(row.views_desktop or 0),
                carts_mobile=int(row.carts_mobile or 0),
                carts_desktop=int(row.carts_desktop or 0),
                views_paid=int(row.views_paid or 0),
                views_organic=int(row.views_organic or 0),
                views_direct=int(row.views_direct or 0),
                carts_paid=int(row.carts_paid or 0),
                carts_organic=int(row.carts_organic or 0),
                carts_direct=int(row.carts_direct or 0),
                purchases_24h=int(row.purchases_24h or 0),
                purchases_7d=int(row.purchases_7d or 0),
                revenue_24h=float(row.revenue_24h or 0),
                purchases_mobile=int(row.purchases_mobile or 0),
                purchases_desktop=int(row.purchases_desktop or 0),
                purchases_paid=int(row.purchases_paid or 0),
                purchases_organic=int(row.purchases_organic or 0),
                purchases_direct=int(row.purchases_direct or 0),
                peak_hour_views=phv,
                peak_hour_carts=phc,
                off_peak_hour_views=ophv,
                off_peak_hour_carts=ophc,
                landing_views_24h=lv,
                browsing_views_24h=bv,
                landing_carts_24h=lc,
                browsing_carts_24h=bc,
                cart_abandonment_rate=_cart_abandonment_rate(views_24h, cart_24h),
                return_visitor_rate=_return_visitor_rate(return_7d, unique_7d),
                engagement_score=_engagement_score(dwell, scroll),
                cart_rate_24h=cart_rate_24h,
                cart_rate_7d=cart_rate_7d,
                cart_rate_trend=cart_rate_trend,
                peak_conversion_label=peak_conversion_label,
                landing_cart_rate=_cart_rate(lc, lv),
                browsing_cart_rate=_cart_rate(bc, bv),
            )
        )

    return ProductMetricsResponse(
        shop_domain=shop,
        count=len(products),
        products=products,
    )
