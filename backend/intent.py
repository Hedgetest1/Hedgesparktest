from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.visitor_product_state import VisitorProductState

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/intent/top-hot")
def top_hot_visitors(db: Session = Depends(get_db)):

    results = (
        db.query(VisitorProductState)
        .filter(VisitorProductState.intent_level == "HOT")
        .order_by(VisitorProductState.intent_score.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "visitor_id": r.visitor_id,
            "product_url": r.product_url,
            "intent_score": r.intent_score,
            "recommended_action": r.recommended_action,
            "explanation": r.intent_explanation
        }
        for r in results
    ]
