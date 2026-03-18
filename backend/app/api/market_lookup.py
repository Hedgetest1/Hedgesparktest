from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_api_key, require_shop
from app.models.market_lookup import MarketLookup

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/market-lookup/top")
def top_market_lookup(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    results = (
        db.query(MarketLookup)
        .filter(MarketLookup.shop_domain == shop)
        .order_by(MarketLookup.lookup_confidence.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "lookup_status": r.lookup_status,
            "comparable_presence": r.comparable_presence,
            "uniqueness_hint": r.uniqueness_hint,
            "lookup_confidence": r.lookup_confidence,
            "market_summary": r.market_summary,
            "recommended_next_step": r.recommended_next_step,
            "plan_required": r.plan_required,
        }
        for r in results
    ]
