from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_api_key, require_shop
from app.models.price_intelligence import PriceIntelligence
from app.services.price_radar_service import evaluate_price

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/price-intelligence/top")
def top_price_intelligence(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    results = (
        db.query(PriceIntelligence)
        .filter(PriceIntelligence.shop_domain == shop)
        .order_by(PriceIntelligence.confidence_score.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "market_status": r.market_status,
            "price_position": r.price_position,
            "price_opportunity": r.price_opportunity,
            "recommended_price_action": r.recommended_price_action,
            "intelligence_explanation": r.intelligence_explanation,
            "confidence_score": r.confidence_score,
            "plan_required": r.plan_required,
        }
        for r in results
    ]


@router.post("/price-radar")
def price_radar(data: dict):
    result = evaluate_price(data.get("product_name"))
    return result
