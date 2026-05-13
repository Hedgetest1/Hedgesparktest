"""
revenue_radar.py — GET /revenue-radar/top

Product boundary
----------------
GET /revenue-radar/top is a Pro-only endpoint.
Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

Why there is no Lite split
--------------------------
The revenue radar is the Action Layer in concentrated form.  Every field in
the response is either a scoring output of a Pro-tier inference pipeline or
a direct prescription:

  revenue_opportunity_score / band — the core output of the revenue scoring
    pipeline.  The score IS the Pro feature — it is not a diagnostic
    observation; it is a ranked action agenda derived from behavioral,
    market, and pricing signals.

  conversion_probability / time_to_conversion — output of
    infer_conversion_outcome(), a Pro-tier inference engine.  Lite shops have
    no access to the underlying market_lookup or price_intelligence data
    (both enforced as Pro-only), so these scores cannot be produced for them.

  recommended_action, expected_uplift — direct prescriptions (Action Layer).

  primary_driver / primary_barrier — explain the recommendation; only useful
    alongside the prescription that motivated them.

  auto_action_candidate, expected_loss, loss_band, urgency_score — risk and
    urgency signals designed to trigger automated or human action.

Even the response envelope keys (push_now, price_watch, auto_action_candidates)
are prescriptive categories, not observational groupings.  There is no
"what is happening" layer here that is separable from "what to do about it".

Unlike surfaces with a genuine Lite/Pro field boundary (e.g. opportunities
where explanation is Lite and human_action is Pro, or alerts where message is
Lite and action is Pro), there is no diagnostic count or observation in this
response that stands on its own without the Pro analytical context.

Data-source dependency chain (all Pro-only):
  visitor_product_state  — behavioral signals (views, wishlist, intent)
  product_metrics        — 24-hour view counts for expected_loss
  market_lookup          — uniqueness and comparability (Pro-only table)
  price_intelligence     — price pressure score (Pro-only table)

This surface is structurally identical to /price-intelligence/top and
/market-lookup/top: plan_required is implicit in all source data, every
meaningful field is prescriptive or pro-tier analytical, and no row variant
safe for Lite callers exists.

Note: the main frontend dashboard does not call this endpoint directly.
It is a headless API surface consumed by external integrations or future
Pro dashboard sections.

Request
-------
    GET /revenue-radar/top?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON object with four keys:
        top_revenue_opportunities  list[dict]  top 10, sorted by revenue_opportunity_score DESC
        push_now                   list[dict]  top 3 candidates for immediate promotion
        price_watch                list[dict]  top 3 with price pressure >= 60
        auto_action_candidates     list[dict]  top 3 auto-actionable products

    400 if shop param is missing or invalid (from require_shop, composed
        inside require_pro_plan).
    403 if the shop does not have an active Pro plan.

Each item in the lists contains:
    product_id                  str
    product_name                str
    revenue_opportunity_score   float
    revenue_opportunity_band    str
    conversion_probability      float
    time_to_conversion          str
    recommended_action          str
    expected_uplift             float
    primary_driver              str
    primary_barrier             str
    price_pressure_score        float
    uniqueness_score            float
    comparability_score         float
    auto_action_candidate       bool
    expected_loss               float   views_24h × conv_prob × AOV
    loss_band                   str     LOW | MEDIUM | HIGH
    urgency_score               float   0–100 composite urgency
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.core.deps import require_pro_session
from app.services.conversion_metrics import (
    compute_real_conversion_probability,
    get_real_product_conversion_map,
)
from app.services.conversion_service import infer_conversion_outcome
from app.services.empirical_calibration import (
    apply_calibration,
    compute_behavioral_index_from_features,
    get_or_train_model,
)
from app.services.revenue_loss import calculate_expected_loss
from app.services.revenue_metrics import get_shop_aov

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

router = APIRouter(prefix="/revenue-radar", tags=["revenue-radar"])


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _rows(query: str, params: dict) -> list[dict]:
    db = SessionLocal()
    try:
        result = db.execute(text(query), params)
        return [dict(row._mapping) for row in result.fetchall()]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pro route — GET /revenue-radar/top
#
# Entire endpoint is Pro-only.  No Lite subset exists — see module docstring.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# revenue_radar_top — stage helpers
# Refactor 2026-05-13 (A3 close): 204-LOC endpoint → composer + 9 pure
# stage helpers (4 SQL constants + 4 fetchers + per-shop deps loader
# + lookup-map builder + per-product enricher + 3-tier conversion-
# probability resolver + radar-item builder + subset filter).
# Contract preserved byte-identical. SQL hoisted to module constants.
# ---------------------------------------------------------------------------


_PRODUCTS_SQL = """
    SELECT
        vps.product_url AS product_id,
        vps.product_url AS product_name,
        COALESCE(SUM(vps.total_views), 0)                                         AS total_views,
        COALESCE(SUM(CASE WHEN COALESCE(vps.wishlist_added, FALSE) THEN 1 ELSE 0 END), 0)
                                                                                   AS wishlist_adds,
        COALESCE(ROUND(AVG(vps.intent_score), 2), 0)                              AS avg_intent_score
    FROM visitor_product_state vps
    WHERE vps.shop_domain = :shop_domain
    GROUP BY vps.product_url
    ORDER BY avg_intent_score DESC, total_views DESC
    LIMIT 20
"""


_METRICS_SQL = """
    SELECT
        product_url,
        COALESCE(views_24h, 0) AS views_24h
    FROM product_metrics
    WHERE shop_domain = :shop_domain
"""


_MARKET_LOOKUP_SQL = """
    SELECT
        product_url AS product_id,
        COALESCE(lookup_confidence, 70) AS market_confidence,
        CASE
            WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'LIKELY_UNIQUE'         THEN 80
            WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'UNCLEAR'               THEN 55
            ELSE 35
        END AS uniqueness_score,
        CASE
            WHEN UPPER(COALESCE(comparable_presence, 'REQUIRES_EXTERNAL_CHECK'))
                 = 'LIKELY_FOUND_EXTERNALLY'                                           THEN 80
            WHEN UPPER(COALESCE(comparable_presence, 'REQUIRES_EXTERNAL_CHECK'))
                 = 'REQUIRES_EXTERNAL_CHECK'                                           THEN 55
            ELSE 30
        END AS comparability_score
    FROM market_lookup
    WHERE shop_domain = :shop_domain
"""


_PRICE_INTEL_SQL = """
    SELECT
        product_url AS product_id,
        COALESCE(confidence_score, 0) AS price_confidence,
        CASE
            WHEN UPPER(COALESCE(price_opportunity, '')) = 'HIGH_INTENT_PRICE_OPPORTUNITY' THEN 75
            ELSE 35
        END AS price_pressure_score
    FROM price_intelligence
    WHERE shop_domain = :shop_domain
"""


def _fetch_per_shop_deps(shop_domain: str):
    """Resolve aov + real_conv_map + calibration in a single DB context."""
    _db = SessionLocal()
    try:
        return (
            get_shop_aov(_db, shop_domain),
            get_real_product_conversion_map(_db, shop_domain),
            get_or_train_model(_db, shop_domain),
        )
    finally:
        _db.close()


def _build_radar_lookup_maps(
    metrics_rows: list[dict],
    market_rows: list[dict],
    price_rows: list[dict],
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """3 row-lists → 3 maps keyed by product_id/product_url (stable
    string keys to absorb numeric/string ID drift)."""
    return (
        {str(r["product_url"]): r for r in metrics_rows},
        {str(r["product_id"]): r for r in market_rows},
        {str(r["product_id"]): r for r in price_rows},
    )


def _resolve_radar_conversion_probability(
    *,
    pid: str, enriched: dict, metrics_row: dict, inferred_prob: float,
    real_conv_map: dict, calibration,
) -> float:
    """3-tier conversion probability resolution (same hierarchy as
    action engine):
      Tier 1 (real):      product-level CVR from order data
      Tier 2 (empirical): shop-level behavioral calibration
      Tier 3 (inferred):  handcrafted model
    """
    real_cvr = compute_real_conversion_probability(
        product_url=pid,
        conv_map=real_conv_map,
        views_24h=int(metrics_row.get("views_24h") or 0),
        # 7d not in metrics_map scope — reuse 24h as a conservative anchor
        views_7d=int(metrics_row.get("views_24h") or 0),
    )
    if real_cvr is not None:
        return real_cvr
    behavioral_index = compute_behavioral_index_from_features(enriched)
    conversion_prob, _ = apply_calibration(
        inferred_prob=inferred_prob,
        behavioral_index=behavioral_index,
        model=calibration,
    )
    return conversion_prob


def _build_radar_item(outcome: dict, loss_result: dict) -> dict:
    """Compose one radar response item from outcome + loss_result."""
    return {
        "product_id": outcome.get("product_id"),
        "product_name": outcome.get("product_name"),
        "revenue_opportunity_score": outcome.get("revenue_opportunity_score"),
        "revenue_opportunity_band": outcome.get("revenue_opportunity_band"),
        "conversion_probability": outcome.get("conversion_probability"),
        "time_to_conversion": outcome.get("time_to_conversion"),
        "recommended_action": outcome.get("recommended_action"),
        "expected_uplift": outcome.get("expected_uplift"),
        "primary_driver": outcome.get("primary_driver"),
        "primary_barrier": outcome.get("primary_barrier"),
        "price_pressure_score": outcome.get("price_pressure_score"),
        "uniqueness_score": outcome.get("uniqueness_score"),
        "comparability_score": outcome.get("comparability_score"),
        "auto_action_candidate": outcome.get("auto_action_candidate"),
        # Revenue loss fields
        "expected_loss": loss_result["expected_loss"],
        "loss_band": loss_result["loss_band"],
        "urgency_score": loss_result["urgency_score"],
    }


def _filter_radar_subsets(
    ranked: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """3 prescriptive categories (push_now / price_watch / auto_actions),
    each capped at 3."""
    push_now = [
        item for item in ranked
        if item.get("recommended_action") == "HIGHLIGHT_UNIQUENESS_AND_SCARCITY"
    ][:3]
    price_watch = [
        item for item in ranked
        if float(item.get("price_pressure_score") or 0) >= 60
    ][:3]
    auto_actions = [
        item for item in ranked
        if item.get("auto_action_candidate") is True
    ][:3]
    return push_now, price_watch, auto_actions


@router.get("/top")
def revenue_radar_top(
    shop: str = Depends(require_pro_session),
):
    """
    Pro revenue radar — full response, backend-enforced.

    Returns top revenue opportunities ranked by revenue_opportunity_score,
    plus three filtered subsets (push_now, price_watch, auto_action_candidates).
    All fields are outputs of a Pro-tier inference pipeline or direct
    prescriptions — see module docstring for the complete reasoning.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.

    Refactored 2026-05-13 (A3 close): 204-LOC endpoint → 35-LOC
    composer + 9 pure helpers.
    """
    params = {"shop_domain": shop}
    aov, real_conv_map, calibration = _fetch_per_shop_deps(shop)

    products = _rows(_PRODUCTS_SQL, params)
    metrics_rows = _rows(_METRICS_SQL, params)
    market_rows = _rows(_MARKET_LOOKUP_SQL, params)
    price_rows = _rows(_PRICE_INTEL_SQL, params)

    metrics_map, market_map, price_map = _build_radar_lookup_maps(
        metrics_rows, market_rows, price_rows,
    )

    ranked: list[dict] = []
    for product in products:
        pid = str(product.get("product_id"))
        enriched = {**product, **market_map.get(pid, {}), **price_map.get(pid, {})}
        outcome = infer_conversion_outcome(enriched)

        conversion_prob = _resolve_radar_conversion_probability(
            pid=pid, enriched=enriched,
            metrics_row=metrics_map.get(pid, {"views_24h": 0}),
            inferred_prob=float(outcome.get("conversion_probability") or 0),
            real_conv_map=real_conv_map, calibration=calibration,
        )
        loss_result = calculate_expected_loss(
            product_metrics_row=metrics_map.get(pid, {"views_24h": 0}),
            conversion_probability=conversion_prob,
            aov=aov,
        )
        ranked.append(_build_radar_item(outcome, loss_result))

    ranked.sort(
        key=lambda x: float(x.get("revenue_opportunity_score") or 0),
        reverse=True,
    )
    push_now, price_watch, auto_actions = _filter_radar_subsets(ranked)

    return {
        "top_revenue_opportunities": ranked[:10],
        "push_now": push_now,
        "price_watch": price_watch,
        "auto_action_candidates": auto_actions,
    }
