from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.product_opportunity import ProductOpportunity

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/opportunities/top")
def top_opportunities(db: Session = Depends(get_db)):
    results = (
        db.query(ProductOpportunity)
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
            "plan_required": r.plan_required
        }
        for r in results
    ]
