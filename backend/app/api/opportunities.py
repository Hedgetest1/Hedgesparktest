from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_api_key, require_shop
from app.models.product_opportunity import ProductOpportunity
from app.services.opportunity_engine import get_or_refresh_signals

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/opportunities")
def opportunities(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    """
    Run the live opportunity detection engine for the given shop.

    Returns signals derived from the events table using four rule-based
    detectors (no AI, no external calls).  Results are served from an
    in-process cache (TTL 5 min) backed by the opportunity_signals table,
    so the detection queries run at most once every 5 minutes per shop.
    """
    return get_or_refresh_signals(shop)


@router.get("/opportunities/top")
def top_opportunities(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    results = (
        db.query(ProductOpportunity)
        .filter(ProductOpportunity.shop_domain == shop)
        .order_by(ProductOpportunity.priority_score.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "records": r.records,
            "avg_intent_score": r.avg_intent_score,
            "hot_count": r.hot_count,
            "wishlist_count": r.wishlist_count,
            "avg_dwell_seconds": r.avg_dwell_seconds,
            "avg_scroll_depth": r.avg_scroll_depth,
            "opportunity_type": r.opportunity_type,
            "priority_score": r.priority_score,
            "recommended_action": r.recommended_action,
            "opportunity_explanation": r.opportunity_explanation,
            "plan_required": r.plan_required,
        }
        for r in results
    ]
