"""
store_intelligence.py — GET /products/store-intelligence

Returns precomputed store-level intelligence. ALL heavy computation
happens in the aggregation_worker → store_metrics table.

This endpoint is READ-ONLY:
  - store_summary, revenue_concentration, device_split, source_split:
    lightweight aggregation over product_metrics rows (already in memory)
  - co_viewed, cohort_snapshot: read directly from store_metrics (precomputed)
  - execution_opportunities: read from execution_opportunities table (relational)

ZERO event table queries at runtime.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_merchant_session
from app.models.product_metrics import ProductMetrics
from app.models.store_metrics import StoreMetrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class CoViewedPair(BaseModel):
    product_a: str
    product_b: str
    shared_visitors: int
    a_views: int = 0
    b_views: int = 0

class StoreSummary(BaseModel):
    total_views_24h: int = 0
    total_carts_24h: int = 0
    total_purchases_24h: int = 0
    total_revenue_24h: float = 0
    product_count: int = 0
    active_products: int = 0

class RevenueConcentration(BaseModel):
    top_product_url: str | None = None
    top_product_revenue_pct: float | None = None
    top_3_revenue_pct: float | None = None
    is_concentrated: bool = False

class DeviceSplit(BaseModel):
    views_mobile_pct: float = 0
    views_desktop_pct: float = 0
    purchases_mobile_pct: float = 0
    purchases_desktop_pct: float = 0
    mobile_conversion_gap: bool = False

class SourceSplit(BaseModel):
    views_paid_pct: float = 0
    views_organic_pct: float = 0
    views_direct_pct: float = 0
    purchases_paid_pct: float = 0
    purchases_organic_pct: float = 0
    purchases_direct_pct: float = 0
    paid_revenue_gap: bool = False

class CohortSnapshot(BaseModel):
    new_visitors_7d: int = 0
    returning_visitors_7d: int = 0
    new_visitor_cart_rate: float | None = None
    returning_visitor_cart_rate: float | None = None

class ExecutionOpportunityResponse(BaseModel):
    execution_id: str
    type: str
    product_a: str
    product_b: str
    audience_size: int
    suggested_message: str | None = None
    timing: str | None = None
    expected_impact: str | None = None
    # Execution lifecycle
    execution_status: str = "suggested"
    executed_at: str | None = None
    # Proof loop metrics (None = not enough data yet)
    return_rate: float | None = None
    view_rate: float | None = None
    purchase_rate: float | None = None
    tracked_count: int = 0
    # Baseline (captured at execution time)
    baseline_return_rate: float | None = None
    baseline_view_rate: float | None = None
    baseline_purchase_rate: float | None = None
    # Post-execution deltas (before/after, positive = improvement)
    delta_return_rate: float | None = None
    delta_view_rate: float | None = None
    delta_purchase_rate: float | None = None
    post_sample_size: int = 0
    # Counterfactual (exposed vs holdout)
    exposed_sample_size: int = 0
    holdout_sample_size: int = 0
    view_rate_exposed: float | None = None
    view_rate_holdout: float | None = None
    purchase_rate_exposed: float | None = None
    purchase_rate_holdout: float | None = None
    lift_view_rate: float | None = None
    lift_purchase_rate: float | None = None
    confidence_label: str | None = None
    enforcement_mode: str = "unknown"   # email | onsite | unknown

class StoreIntelligenceResponse(BaseModel):
    shop_domain: str
    store_summary: StoreSummary
    co_viewed: list[CoViewedPair]
    revenue_concentration: RevenueConcentration
    device_split: DeviceSplit
    source_split: SourceSplit
    cohort_snapshot: CohortSnapshot
    execution_opportunities: list[ExecutionOpportunityResponse]


# ---------------------------------------------------------------------------
# Helpers (lightweight — product_metrics rows only, no event queries)
# ---------------------------------------------------------------------------

def _pct(part: float, total: float) -> float:
    if total <= 0:
        return 0
    return round(part / total * 100, 1)


def _compute_store_summary(rows: list) -> StoreSummary:
    total_v = sum(int(r.views_24h or 0) for r in rows)
    total_c = sum(int(r.cart_conversions_24h or 0) for r in rows)
    total_p = sum(int(r.purchases_24h or 0) for r in rows)
    total_r = sum(float(r.revenue_24h or 0) for r in rows)
    active = sum(1 for r in rows if (r.views_24h or 0) > 0)
    return StoreSummary(
        total_views_24h=total_v, total_carts_24h=total_c,
        total_purchases_24h=total_p, total_revenue_24h=round(total_r, 2),
        product_count=len(rows), active_products=active,
    )


def _compute_revenue_concentration(rows: list) -> RevenueConcentration:
    revenues = [(r.product_url, float(r.revenue_24h or 0)) for r in rows]
    total = sum(rev for _, rev in revenues)
    if total <= 0:
        return RevenueConcentration()
    revenues.sort(key=lambda x: x[1], reverse=True)
    top_url, top_rev = revenues[0]
    top_pct = _pct(top_rev, total)
    top_3 = sum(rev for _, rev in revenues[:3])
    return RevenueConcentration(
        top_product_url=top_url, top_product_revenue_pct=top_pct,
        top_3_revenue_pct=_pct(top_3, total), is_concentrated=top_pct > 50,
    )


def _compute_device_split(rows: list) -> DeviceSplit:
    vm = sum(int(r.views_mobile or 0) for r in rows)
    vd = sum(int(r.views_desktop or 0) for r in rows)
    pm = sum(int(r.purchases_mobile or 0) for r in rows)
    pd = sum(int(r.purchases_desktop or 0) for r in rows)
    vt = vm + vd
    pt = pm + pd
    mv = _pct(vm, vt)
    mp = _pct(pm, pt)
    return DeviceSplit(
        views_mobile_pct=mv, views_desktop_pct=_pct(vd, vt),
        purchases_mobile_pct=mp, purchases_desktop_pct=_pct(pd, pt),
        mobile_conversion_gap=(mv > 50 and mp < 30 and pt >= 2),
    )


def _compute_source_split(rows: list) -> SourceSplit:
    vp = sum(int(r.views_paid or 0) for r in rows)
    vo = sum(int(r.views_organic or 0) for r in rows)
    vd = sum(int(r.views_direct or 0) for r in rows)
    pp = sum(int(r.purchases_paid or 0) for r in rows)
    po = sum(int(r.purchases_organic or 0) for r in rows)
    pdi = sum(int(r.purchases_direct or 0) for r in rows)
    vt = vp + vo + vd
    pt = pp + po + pdi
    pv = _pct(vp, vt)
    ppct = _pct(pp, pt)
    return SourceSplit(
        views_paid_pct=pv, views_organic_pct=_pct(vo, vt), views_direct_pct=_pct(vd, vt),
        purchases_paid_pct=ppct, purchases_organic_pct=_pct(po, pt), purchases_direct_pct=_pct(pdi, pt),
        paid_revenue_gap=(pv > 40 and ppct < 15 and pt >= 2),
    )


# ---------------------------------------------------------------------------
# Route — READ-ONLY, zero event table queries
# ---------------------------------------------------------------------------

@router.get("/store-intelligence", response_model=StoreIntelligenceResponse)
def get_store_intelligence(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(_get_db),
) -> StoreIntelligenceResponse:
    """
    Store-level strategic intelligence. All heavy computation is precomputed
    by the aggregation worker into store_metrics + product_metrics.
    This endpoint does zero event table queries.
    """
    # Product-level rows (lightweight aggregation)
    rows = (
        db.query(ProductMetrics)
        .filter(ProductMetrics.shop_domain == shop)
        .order_by(ProductMetrics.views_24h.desc())
        .limit(50)
        .all()
    )

    # Precomputed store-level data
    sm = (
        db.query(StoreMetrics)
        .filter(StoreMetrics.shop_domain == shop)
        .first()
    )

    # Co-viewed from precomputed JSONB
    co_viewed = []
    if sm and sm.co_viewed_pairs:
        for p in sm.co_viewed_pairs:
            if isinstance(p, dict):
                co_viewed.append(CoViewedPair(
                    product_a=p.get("product_a", ""),
                    product_b=p.get("product_b", ""),
                    shared_visitors=p.get("shared_visitors", 0),
                    a_views=p.get("a_views", 0),
                    b_views=p.get("b_views", 0),
                ))

    # Cohort from precomputed fields
    cohort = CohortSnapshot(
        new_visitors_7d=sm.new_visitors_7d if sm else 0,
        returning_visitors_7d=sm.returning_visitors_7d if sm else 0,
        new_visitor_cart_rate=sm.new_visitor_cart_rate if sm else None,
        returning_visitor_cart_rate=sm.returning_visitor_cart_rate if sm else None,
    )

    # Execution opportunities from relational tables (with proof + causal data)
    exec_opps = []
    try:
        # Single query: all active opportunities with full state
        opp_rows = db.execute(
            text("""
                SELECT
                    eo.execution_id, eo.opp_type, eo.product_a, eo.product_b,
                    eo.audience_size, eo.suggested_message, eo.timing, eo.expected_impact,
                    eo.execution_status, eo.executed_at,
                    eo.post_sample_size, eo.confidence_label,
                    eo.delta_return_rate, eo.delta_view_rate, eo.delta_purchase_rate,
                    eb.return_rate AS bl_return, eb.view_rate AS bl_view,
                    eb.purchase_rate AS bl_purchase,
                    eo.exposed_sample_size, eo.holdout_sample_size,
                    eo.view_rate_exposed, eo.view_rate_holdout,
                    eo.purchase_rate_exposed, eo.purchase_rate_holdout,
                    eo.lift_view_rate, eo.lift_purchase_rate,
                    eo.enforcement_mode
                FROM execution_opportunities eo
                LEFT JOIN execution_baselines eb
                    ON eb.execution_id = eo.execution_id
                   AND eb.shop_domain  = eo.shop_domain
                WHERE eo.shop_domain = :shop AND eo.is_active = true
                ORDER BY eo.audience_size DESC
                LIMIT 10
            """),
            {"shop": shop},
        ).fetchall()

        from app.services.execution_engine import compute_proof_metrics
        proof_map = {p["execution_id"]: p for p in compute_proof_metrics(db, shop)}

        for r in opp_rows:
            eid = r[0]
            proof = proof_map.get(eid, {})
            exec_opps.append(ExecutionOpportunityResponse(
                execution_id=eid,
                type=r[1], product_a=r[2], product_b=r[3],
                audience_size=int(r[4] or 0),
                suggested_message=r[5], timing=r[6], expected_impact=r[7],
                execution_status=r[8] or "suggested",
                executed_at=r[9].isoformat() if r[9] else None,
                return_rate=proof.get("return_rate"),
                view_rate=proof.get("view_rate"),
                purchase_rate=proof.get("purchase_rate"),
                tracked_count=proof.get("tracked_count", 0),
                baseline_return_rate=float(r[15]) if r[15] is not None else None,
                baseline_view_rate=float(r[16]) if r[16] is not None else None,
                baseline_purchase_rate=float(r[17]) if r[17] is not None else None,
                delta_return_rate=float(r[12]) if r[12] is not None else None,
                delta_view_rate=float(r[13]) if r[13] is not None else None,
                delta_purchase_rate=float(r[14]) if r[14] is not None else None,
                post_sample_size=int(r[10] or 0),
                exposed_sample_size=int(r[18] or 0),
                holdout_sample_size=int(r[19] or 0),
                view_rate_exposed=float(r[20]) if r[20] is not None else None,
                view_rate_holdout=float(r[21]) if r[21] is not None else None,
                purchase_rate_exposed=float(r[22]) if r[22] is not None else None,
                purchase_rate_holdout=float(r[23]) if r[23] is not None else None,
                lift_view_rate=float(r[24]) if r[24] is not None else None,
                lift_purchase_rate=float(r[25]) if r[25] is not None else None,
                confidence_label=r[11],
                enforcement_mode=r[26] or "unknown",
            ))
    except Exception as exc:
        logger.warning("store_intelligence: execution_opportunities read failed: %s", exc)

    return StoreIntelligenceResponse(
        shop_domain=shop,
        store_summary=_compute_store_summary(rows),
        co_viewed=co_viewed,
        revenue_concentration=_compute_revenue_concentration(rows),
        device_split=_compute_device_split(rows),
        source_split=_compute_source_split(rows),
        cohort_snapshot=cohort,
        execution_opportunities=exec_opps,
    )
