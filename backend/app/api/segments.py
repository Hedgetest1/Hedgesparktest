"""
segments.py — GET /pro/segments

Pro-only audience segmentation API.

Returns live behavioral segments (hot / warm / cold) for a given product URL,
computed from active visitor behavioral data, empirical conversion calibration,
and real shop AOV.

This endpoint is the operational core of WishSpark's competitive advantage:
  - No competitor can produce visitor-level behavioral conversion segments
  - No competitor can anchor CVR estimates to this shop's real buyer behavior
  - No competitor can produce an empirically grounded dollar revenue window
    for a specific product's active visitor cohort

Plan gate
---------
Pro-only via require_pro_plan.  The segment data includes visitor_ids
(pseudonymous UUIDs) which are operational inputs for Pro agent execution.
A future Lite summary endpoint can expose counts + revenue window without IDs.

Route shape
-----------
GET /pro/segments?shop=<domain>&product_url=<url>&hours=<n>

product_url is a query param (not a path param) because /products/{handle}
contains slashes and path-param encoding is fragile across proxies and clients.

Query parameters
----------------
shop          Required. Validated as *.myshopify.com. Enforced by require_pro_plan.
product_url   Required. Canonical product path: /products/{handle}.
              Normalised server-side (strips query string, validates format).
hours         Optional. Active visitor window in hours. Default 72, max 168.
              Visitors who engaged with the product page within this window
              are considered "in the decision phase".

Response
--------
200 OK — JSON segment report.
    Structure:
        product_url          str   — normalised canonical product path
        shop_domain          str
        active_window_hours  int
        total_active_visitors int
        hot   / warm / cold  dict  — one per segment (see below)
        meta                 dict  — calibration + AOV metadata

    Per-segment dict:
        visitor_count            int
        visitor_ids              list[str]  — pseudonymous localStorage UUIDs
        visitors                 list[dict] — behavioral detail per visitor
            visitor_id, behavioral_index, avg_scroll, avg_dwell_secs, visit_count
        avg_behavioral_index     float | null
        cvr_estimate             float | null  — empirical or fallback CVR
        estimated_revenue_window float  — visitor_count × cvr_estimate × aov
        cvr_source               str    — "empirical" | "fallback" | "none"

    Meta dict:
        calibration_state         str    — "empirical" | "fallback"
        calibration_base_cvr      float
        converter_behavioral_mean float
        non_converter_behavioral_mean float
        discriminability          float
        calibration_sample_size   int
        calibration_converter_count int
        hot_threshold             float | null
        warm_threshold            float | null
        aov_used                  float
        aov_source                str    — "real" | "fallback"
        generated_at              str    — ISO 8601 UTC

400 — missing or invalid product_url
403 — shop not on active Pro plan
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.core.url_utils import normalize_product_url
from app.services.audience_segments import segment_product_visitors

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["segments"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/segments")
def get_audience_segments(
    shop: str = Depends(require_pro_plan),
    product_url: str = Query(..., description="Canonical product path, e.g. /products/handle"),
    hours: int = Query(
        default=72,
        ge=1,
        le=168,
        description="Active visitor window in hours. Default 72 (3 days). Max 168 (7 days).",
    ),
    db: Session = Depends(get_db),
):
    """
    Live behavioral audience segments for a single product.

    Returns hot / warm / cold visitor segments classified by behavioral engagement
    relative to this shop's empirical conversion profile.  Includes per-segment
    CVR estimates and revenue window projections.

    All active visitors who have NOT yet converted (not in visitor_purchase_sessions)
    are considered.  Converted visitors are excluded — they are customers, not
    prospects.

    Backend-enforced: require_pro_plan raises HTTP 403 for non-Pro shops.
    """
    # Normalise and validate product_url — strip query string, validate /products/ format.
    canonical = normalize_product_url(product_url)
    if not canonical:
        log.warning(
            "segments: invalid product_url=%r for shop=%s — rejected",
            product_url, shop,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid product_url. Must be a canonical Shopify product path: "
                "/products/{handle}. Query strings and variant params are stripped automatically."
            ),
        )

    log.info(
        "segments: GET /pro/segments shop=%s product=%s hours=%d",
        shop, canonical, hours,
    )

    return segment_product_visitors(
        db=db,
        shop_domain=shop,
        product_url=canonical,
        hours=hours,
    )
