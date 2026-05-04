"""
proof_report.py — Unified proof-of-value endpoint (Pro only).

GET /pro/proof-report?window_hours=168

Returns a single, trust-calibrated report that answers:
"How much money has HedgeSpark made me?"

Combines holdout lift (quasi-experimental) with action proof (before/after)
into one merchant-facing payload suitable for both dashboard rendering
and email digest inclusion.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.proof_engine import get_proof_report

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/proof-report", tags=["proof"])


# ---------------------------------------------------------------------------
# Response models — Holdout Proof cassettone (unified dashboard + digest).
# ---------------------------------------------------------------------------


class ProofNudgeRow(BaseModel):
    """One nudge inside the holdout_proof.nudges list."""
    nudge_id: int
    product_url: str
    action_type: str
    exposed_count: int
    holdout_count: int
    exposed_cvr: float
    holdout_cvr: float
    lift_pct: float | None = None
    incremental_revenue: float
    attributed_revenue: float
    p_value: float
    significance: str
    currency: str


class HoldoutProofBlock(BaseModel):
    """Aggregate holdout lift across all measured nudges."""
    has_data: bool
    nudges_measured: int
    total_exposed: int
    total_holdout: int
    pooled_exposed_cvr: float
    pooled_holdout_cvr: float
    lift_pct: float | None = None
    incremental_revenue: float
    attributed_revenue: float
    currency: str
    nudges: list[ProofNudgeRow]


class ActionImprovementRow(BaseModel):
    """One before/after action snapshot marked 'improved'."""
    product_url: str
    action_type: str
    summary: str | None = None
    delta_cvr: float | None = None
    delta_revenue: float | None = None
    measured_at: str | None = None


class ActionProofBlock(BaseModel):
    """Aggregate action proof (before/after snapshots)."""
    actions_measured: int
    improvements_count: int
    improvements: list[ActionImprovementRow]
    total_revenue_delta: float


class ProofConfidenceBlock(BaseModel):
    """Graduated confidence assessment (strong / moderate / early / insufficient)."""
    level: str
    label: str
    description: str


class ProofReportResponse(BaseModel):
    """GET /pro/proof-report — unified proof-of-value payload."""
    has_proof: bool
    holdout_proof: HoldoutProofBlock
    action_proof: ActionProofBlock
    total_incremental_revenue: float
    confidence: ProofConfidenceBlock
    headline: str
    detail: str
    trust_note: str
    currency: str
    store_revenue_7d: float
    generated_at: str




@router.get(
    "",
    response_model=ProofReportResponse,
    response_model_exclude_none=False,
)
def proof_report(
    window_hours: int = 168,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),
):
    """
    Unified proof-of-value report.

    Combines holdout experiments and action snapshots into a single
    merchant-facing report with incremental revenue, confidence levels,
    and trust-calibrated messaging.

    Query params:
        window_hours: Attribution window (1-168, default 168 = 7 days)
    """
    return get_proof_report(db, shop, window_hours)
