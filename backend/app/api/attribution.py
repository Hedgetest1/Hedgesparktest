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

GET /attribution/summary?shop=&days=
    Lite (Strada 3.2, 2026-04-20) — same summary shape, opens the
    UTM/channel attribution picture to the €39 tier. Data is UTM-
    based (not ad-platform integration) so there's no "ad spend"
    component — but attribution to source, revenue per source, and
    top campaigns are all there.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_merchant_session, require_pro_session
from app.services.utm_attribution import (
    get_utm_attribution,
    get_utm_top_products_by_source,
    get_attribution_summary,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/attribution", tags=["attribution"])


# ---------------------------------------------------------------------------
# Response models for /attribution/summary/pro — Attribution Intelligence
# cassettone source. See reference_openapi_codegen.md for the pattern.
# ---------------------------------------------------------------------------


class AttributionSourceRow(BaseModel):
    """One row in top_sources_first_touch / top_sources_last_touch."""
    source: str
    label: str
    orders: int
    revenue: float


class AttributionCampaignRow(BaseModel):
    """One row in top_campaigns — ranked by revenue."""
    campaign: str
    orders: int
    revenue: float


class AttributionSummaryResponse(BaseModel):
    """GET /attribution/summary/pro — attribution overview dashboard."""
    window_days: int
    generated_at: str
    orders_total: int
    orders_attributed: int
    orders_unattributed: int
    attribution_rate: float
    top_sources_first_touch: list[AttributionSourceRow]
    top_sources_last_touch: list[AttributionSourceRow]
    top_campaigns: list[AttributionCampaignRow]
    first_vs_last_match_rate: float




@router.get("/sources")
def get_source_attribution_lite(
    days: int = 30,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
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
    db: Session = Depends(get_read_db),
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
    db: Session = Depends(get_read_db),
):
    """
    Pro — top (source × product) combinations by visitor volume.

    "Which traffic source is driving interest in which products?"
    """
    results = get_utm_top_products_by_source(db, shop, days=days)
    return {"window_days": days, "results": results, "count": len(results)}


@router.get(
    "/summary/pro",
    response_model=AttributionSummaryResponse,
    response_model_exclude_none=False,
)
def get_attribution_summary_pro(
    days: int = 30,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),
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


@router.get(
    "/summary",
    response_model=AttributionSummaryResponse,
    response_model_exclude_none=False,
)
def get_attribution_summary_lite(
    days: int = 30,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
):
    """Lite-accessible attribution summary (Strada 3.2, 2026-04-20).
    Same shape + service as /attribution/summary/pro; auth differs.
    Attribution math is UTM-based (deterministic, not modeled) — same
    evidence across tiers, opening it to Lite completes the channel-
    attribution picture at the €39 band."""
    return get_attribution_summary(db, shop, days=days)
