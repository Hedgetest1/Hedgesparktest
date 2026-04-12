"""roi_report.py — GET /pro/roi-report API endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["roi_report"])


class ROIComponent(BaseModel):
    source: str
    loss_eur: float
    narrative: str = ""


class ROIReportResponse(BaseModel):
    shop_domain: str
    month: str
    cost_eur: float
    at_risk_detected_eur: float
    prevented_eur: float
    net_roi_eur: float
    components: list[dict] = Field(default_factory=list)
    headline: str
    generated_at: str


@router.get(
    "/pro/roi-report",
    response_model=ROIReportResponse,
    response_model_exclude_none=False,
)
def get_roi_report(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Generate the current month's ROI self-justification report.
    This is the data behind the monthly email, also exposed for the
    dashboard UI to render a live 'ROI card'.
    """
    from app.services.roi_report import generate_roi_report
    report = generate_roi_report(db, shop)
    return report.to_dict()
