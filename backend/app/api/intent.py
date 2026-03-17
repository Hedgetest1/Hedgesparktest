from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case

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
            "intent_level": r.intent_level,
            "recommended_action": r.recommended_action,
            "explanation": r.intent_explanation
        }
        for r in results
    ]


@router.get("/intent/visitor/{visitor_id}")
def visitor_intent(visitor_id: str, db: Session = Depends(get_db)):
    results = (
        db.query(VisitorProductState)
        .filter(VisitorProductState.visitor_id == visitor_id)
        .order_by(VisitorProductState.intent_score.desc())
        .all()
    )

    return [
        {
            "visitor_id": r.visitor_id,
            "product_url": r.product_url,
            "total_views": r.total_views,
            "total_dwell_seconds": r.total_dwell_seconds,
            "max_scroll_depth": r.max_scroll_depth,
            "wishlist_added": r.wishlist_added,
            "intent_score": r.intent_score,
            "intent_level": r.intent_level,
            "recommended_action": r.recommended_action,
            "explanation": r.intent_explanation
        }
        for r in results
    ]


@router.get("/intent/summary")
def intent_summary(db: Session = Depends(get_db)):
    total = db.query(func.count(VisitorProductState.id)).scalar() or 0
    hot = (
        db.query(func.count(VisitorProductState.id))
        .filter(VisitorProductState.intent_level == "HOT")
        .scalar()
        or 0
    )
    warm = (
        db.query(func.count(VisitorProductState.id))
        .filter(VisitorProductState.intent_level == "WARM")
        .scalar()
        or 0
    )
    cold = (
        db.query(func.count(VisitorProductState.id))
        .filter(VisitorProductState.intent_level == "COLD")
        .scalar()
        or 0
    )

    avg_score = db.query(func.avg(VisitorProductState.intent_score)).scalar()
    avg_score = round(float(avg_score), 2) if avg_score is not None else 0

    return {
        "total_records": total,
        "hot_records": hot,
        "warm_records": warm,
        "cold_records": cold,
        "average_intent_score": avg_score
    }


@router.get("/intent/products/top")
def top_products(db: Session = Depends(get_db)):
    rows = (
        db.query(
            VisitorProductState.product_url,
            func.count(VisitorProductState.id).label("records"),
            func.avg(VisitorProductState.intent_score).label("avg_intent_score"),
            func.sum(
                case((VisitorProductState.intent_level == "HOT", 1), else_=0)
            ).label("hot_count"),
            func.sum(
                case((VisitorProductState.wishlist_added == True, 1), else_=0)
            ).label("wishlist_count")
        )
        .group_by(VisitorProductState.product_url)
        .order_by(func.avg(VisitorProductState.intent_score).desc())
        .limit(20)
        .all()
    )

    return [
        {
            "product_url": r.product_url,
            "records": int(r.records or 0),
            "avg_intent_score": round(float(r.avg_intent_score or 0), 2),
            "hot_count": int(r.hot_count or 0),
            "wishlist_count": int(r.wishlist_count or 0)
        }
        for r in rows
    ]
@router.get("/intent/products/opportunities")
def product_opportunities(db: Session = Depends(get_db)):
    rows = (
        db.query(
            VisitorProductState.product_url,
            func.count(VisitorProductState.id).label("records"),
            func.avg(VisitorProductState.intent_score).label("avg_intent_score"),
            func.sum(
                case((VisitorProductState.intent_level == "HOT", 1), else_=0)
            ).label("hot_count"),
            func.sum(
                case((VisitorProductState.wishlist_added == True, 1), else_=0)
            ).label("wishlist_count"),
            func.avg(VisitorProductState.total_dwell_seconds).label("avg_dwell"),
            func.avg(VisitorProductState.max_scroll_depth).label("avg_scroll")
        )
        .group_by(VisitorProductState.product_url)
        .order_by(func.avg(VisitorProductState.intent_score).desc())
        .limit(50)
        .all()
    )

    opportunities = []

    for r in rows:
        records = int(r.records or 0)
        avg_intent_score = round(float(r.avg_intent_score or 0), 2)
        hot_count = int(r.hot_count or 0)
        wishlist_count = int(r.wishlist_count or 0)
        avg_dwell = round(float(r.avg_dwell or 0), 2)
        avg_scroll = round(float(r.avg_scroll or 0), 2)

        opportunity_type = "NO_ACTION"
        explanation = "No strong product opportunity detected"

        if avg_intent_score >= 80 and wishlist_count >= 1:
            opportunity_type = "PRICE_DROP_OR_LOW_STOCK_NUDGE"
            explanation = "High intent product with strong commitment signals"

        elif avg_intent_score >= 60 and wishlist_count == 0:
            opportunity_type = "WISHLIST_PROMPT_TEST"
            explanation = "High interest but low commitment; test stronger wishlist CTA"

        elif avg_dwell >= 20 and avg_scroll >= 70 and wishlist_count == 0:
            opportunity_type = "FRICTION_OR_PRICE_SENSITIVITY"
            explanation = "Users explore deeply but do not commit; review offer, price, trust, or CTA"

        elif hot_count >= 2:
            opportunity_type = "HIGH_INTEREST_PRODUCT"
            explanation = "Multiple HOT visitor-product states detected"

        opportunities.append({
            "product_url": r.product_url,
            "records": records,
            "avg_intent_score": avg_intent_score,
            "hot_count": hot_count,
            "wishlist_count": wishlist_count,
            "avg_dwell_seconds": avg_dwell,
            "avg_scroll_depth": avg_scroll,
            "opportunity_type": opportunity_type,
            "explanation": explanation
        })

    return opportunities
