"""
attribution.py — UTM / traffic source attribution endpoints.

GET /attribution/sources?shop=&days=
    Lite — basic source breakdown (visitors + page_views only)

GET /attribution/sources/pro?shop=&days=&model=
    Pro — full attribution with HOT visitors, conversions, revenue, CVR
    Supports model=first_touch (default) or model=last_touch

GET /attribution/products?shop=&days=
    Pro — top product+source combinations by visitor volume

GET /attribution/summary/pro?shop=&days=
    Pro — attribution overview: attributed vs unattributed orders,
    top sources, top campaigns, first-touch vs last-touch breakdown.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_merchant_session, require_pro_session
from app.services.utm_attribution import (
    get_utm_attribution,
    get_utm_top_products_by_source,
    get_attribution_summary,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/attribution", tags=["attribution"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/sources")
def get_source_attribution_lite(
    days: int = 30,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Lite attribution — traffic source breakdown (no revenue data).

    Returns source_type, source_label, visitors, page_views per source.
    Revenue, CVR, and hot_visitor data are Pro-only and omitted here.
    """
    data = get_utm_attribution(db, shop, days=days)

    # Strip Pro-only fields from each source row
    lite_sources = [
        {
            "source_type":  s["source_type"],
            "source_label": s["source_label"],
            "visitors":     s["visitors"],
            "page_views":   s["page_views"],
        }
        for s in data["sources"]
    ]

    return {
        "window_days":  data["window_days"],
        "generated_at": data["generated_at"],
        "sources":      lite_sources,
        "totals": {
            "visitors": data["totals"]["visitors"],
        },
    }


@router.get("/sources/pro")
def get_source_attribution_pro(
    days: int = 30,
    model: str = Query("first_touch", pattern="^(first_touch|last_touch)$"),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Pro attribution — full source → behavior → conversion → revenue report.

    Supports model=first_touch (default) or model=last_touch.

    For each traffic source:
    - visitors, page_views
    - hot_visitors (behavioral intent — scroll/dwell/repeat)
    - conversions (from visitor_purchase_sessions → shop_orders)
    - revenue (from shop_orders.total_price)
    - cvr (conversion rate)
    - revenue_per_visitor
    - hot_visitor_rate
    - quality_score (composite: CVR + hot rate + revenue density)
    """
    return get_utm_attribution(db, shop, days=days, model=model)


@router.get("/products")
def get_product_source_attribution(
    days: int = 30,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Pro — top (source × product) combinations by visitor volume.

    "Which traffic source is driving interest in which products?"
    """
    results = get_utm_top_products_by_source(db, shop, days=days)
    return {"window_days": days, "results": results, "count": len(results)}


@router.get("/summary/pro")
def get_attribution_summary_pro(
    days: int = 30,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Pro — attribution overview dashboard.

    Returns:
    - orders_total, orders_attributed, orders_unattributed, attribution_rate
    - top_sources_first_touch, top_sources_last_touch
    - top_campaigns (by revenue)
    - first_vs_last_match_rate (how often first and last touch agree)

    Every number is evidence-based. No modeled/probabilistic attribution.
    """
    return get_attribution_summary(db, shop, days=days)
