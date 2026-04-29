"""
revenue_at_risk.py — GET /analytics/revenue-at-risk API endpoint.

Returns the Revenue-at-Risk Score (RARS) — the killer Pro-tier hero
of HedgeSpark. Pro/Scale-only as of 2026-04-29 per the strict $0-70
parity rule (no $0-70 competitor ships RARS-equivalent at any price,
so it cannot live in Lite €39).

Cached 5 min per shop. Both /analytics/revenue-at-risk (canonical)
and /pro/revenue-at-risk (legacy alias) require Pro session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
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
    # Pro/Scale only since 2026-04-29 — auth gate above ensures plan eligibility.
    # The full 5-dim breakdown is the standard response.
    result = get_revenue_at_risk(db, shop, plan="pro")
    # Strip internal debug field
    result.pop("_prevent_evidence", None)
    return result


@router.get(
    "/analytics/revenue-at-risk",
    response_model=RARSResponse,
    response_model_exclude_none=False,
)
def get_rars(
    shop: str = Depends(require_pro_session),
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
    - Starter/Lite merchants: total + prevented + net_roi + headline,
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
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Legacy alias of /analytics/revenue-at-risk. Kept for backward
    compatibility with dashboard builds on the old path. Same handler,
    same auth, same response. Will be removed once all clients migrate."""
    return _compute_rars(shop, db)
