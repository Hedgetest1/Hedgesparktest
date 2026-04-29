"""
revenue_at_risk.py — GET /analytics/revenue-at-risk API endpoint.

Returns the Revenue-at-Risk Score (RARS) — the hero number of the
HedgeSpark dashboard. Accessible to all merchant sessions; response
fidelity reduces for non-Pro plans (Lite sees headline total +
prevented + net ROI, Pro sees full 5-dim component breakdown).

Cached 5 min per shop; cache is tier-agnostic and filter is applied
at response time.

Path: /analytics/revenue-at-risk is the canonical path (Lite-accessible
via require_merchant_session). The legacy /pro/revenue-at-risk path is
preserved as a deprecated alias for any dashboard build still on the
old URL — same handler, same auth, same response.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.models.merchant import Merchant

router = APIRouter(tags=["revenue_at_risk"])


class RARSComponentResponse(BaseModel):
    source: str
    loss_eur: float
    narrative: str
    evidence: dict = Field(default_factory=dict)


class RARSResponse(BaseModel):
    shop_domain: str
    total_at_risk_eur: float = 0.0
    prevented_eur_this_month: float = 0.0
    net_roi_eur: float = 0.0
    components: list[RARSComponentResponse] = Field(default_factory=list)
    # Shop's native currency — all `_eur`-suffixed fields above are
    # in this currency. Component `loss_eur` too.
    currency: str = "USD"
    generated_at: str | None = None
    headline: str | None = None


def _compute_rars(shop: str, db: Session) -> dict:
    from app.services.revenue_at_risk import get_revenue_at_risk
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    # Only active Pro subscribers get the 5-dim breakdown; everyone
    # else (Lite, trial, missing row) sees the reduced-fidelity view.
    # Internal plan key still "starter" pending TIER_2 rename sprint.
    plan = "pro" if (row is not None and row.plan == "pro" and row.billing_active) else "starter"
    result = get_revenue_at_risk(db, shop, plan=plan)
    # Strip internal debug field
    result.pop("_prevent_evidence", None)
    return result


@router.get(
    "/analytics/revenue-at-risk",
    response_model=RARSResponse,
    response_model_exclude_none=False,
)
def get_rars(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Return the Revenue-at-Risk Score: one number showing how much
    monthly revenue is currently at risk, broken down by source so
    merchants can drill into any component and see the action plan.

    This is the hero metric of the HedgeSpark dashboard. Every other
    feature (benchmarks, refund loss, goals, segments) feeds into or
    drills down from this number.

    Plan-aware response:
    - Pro merchants: full 5-dim `components` breakdown
    - Lite merchants: total + prevented + net_roi + headline,
      `components` returned as empty list (upgrade prompt lives in UI)
    """
    return _compute_rars(shop, db)


@router.get(
    "/pro/revenue-at-risk",
    response_model=RARSResponse,
    response_model_exclude_none=False,
    deprecated=True,
)
def get_rars_legacy(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Legacy alias of /analytics/revenue-at-risk. Kept for backward
    compatibility with dashboard builds on the old path. Same handler,
    same auth, same response. Will be removed once all clients migrate."""
    return _compute_rars(shop, db)
