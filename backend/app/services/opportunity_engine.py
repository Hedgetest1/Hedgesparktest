from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from app.services.price_intelligence_engine import update_price_intelligence
from app.models.visitor_product_state import VisitorProductState
from app.models.product_opportunity import ProductOpportunity


def classify_opportunity(avg_intent_score, hot_count, wishlist_count, avg_dwell, avg_scroll):
    opportunity_type = "NO_ACTION"
    recommended_action = "NONE"
    explanation = "No strong product opportunity detected"
    priority_score = 0

    if avg_intent_score >= 80 and wishlist_count >= 1:
        opportunity_type = "PRICE_DROP_OR_LOW_STOCK_NUDGE"
        recommended_action = "PRICE_DROP_ALERT"
        explanation = "High intent product with strong commitment signals"
        priority_score = 90

    elif avg_intent_score >= 60 and wishlist_count == 0:
        opportunity_type = "WISHLIST_PROMPT_TEST"
        recommended_action = "PROMINENT_WISHLIST_CTA"
        explanation = "High interest but low commitment; test stronger wishlist CTA"
        priority_score = 75

    elif avg_dwell >= 20 and avg_scroll >= 70 and wishlist_count == 0:
        opportunity_type = "FRICTION_OR_PRICE_SENSITIVITY"
        recommended_action = "REVIEW_PRICE_TRUST_CTA"
        explanation = "Users explore deeply but do not commit; review offer, price, trust, or CTA"
        priority_score = 70

    elif hot_count >= 2:
        opportunity_type = "HIGH_INTEREST_PRODUCT"
        recommended_action = "MONITOR_AND_PROMOTE"
        explanation = "Multiple HOT visitor-product states detected"
        priority_score = 65

    return opportunity_type, recommended_action, explanation, priority_score


def update_product_opportunity(db: Session, product_url: str):
    if not product_url:
        return

    row = (
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
        .filter(VisitorProductState.product_url == product_url)
        .group_by(VisitorProductState.product_url)
        .first()
    )

    if not row:
        return

    records = int(row.records or 0)
    avg_intent_score = float(row.avg_intent_score or 0)
    hot_count = int(row.hot_count or 0)
    wishlist_count = int(row.wishlist_count or 0)
    avg_dwell = float(row.avg_dwell or 0)
    avg_scroll = float(row.avg_scroll or 0)

    opportunity_type, recommended_action, explanation, priority_score = classify_opportunity(
        avg_intent_score=avg_intent_score,
        hot_count=hot_count,
        wishlist_count=wishlist_count,
        avg_dwell=avg_dwell,
        avg_scroll=avg_scroll
    )

    existing = (
        db.query(ProductOpportunity)
        .filter(ProductOpportunity.product_url == product_url)
        .first()
    )

    if not existing:
        existing = ProductOpportunity(product_url=product_url)
        db.add(existing)
        db.flush()

    existing.records = records
    existing.avg_intent_score = avg_intent_score
    existing.hot_count = hot_count
    existing.wishlist_count = wishlist_count
    existing.avg_dwell_seconds = avg_dwell
    existing.avg_scroll_depth = avg_scroll

    existing.opportunity_type = opportunity_type
    existing.priority_score = priority_score
    existing.recommended_action = recommended_action
    existing.opportunity_explanation = explanation
    existing.plan_required = "pro"
    existing.updated_at = datetime.utcnow()

    db.commit()
    update_price_intelligence(db, product_url)
