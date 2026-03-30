import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.services.unique_product_engine import update_unique_product_detection
from app.models.product_opportunity import ProductOpportunity
from app.models.price_intelligence import PriceIntelligence

log = logging.getLogger("price_intelligence_engine")


def classify_price_intelligence(opportunity: ProductOpportunity):
    market_status = "COMPARABLE_PRODUCT"
    price_position = "UNKNOWN"
    price_opportunity = "NO_ACTION"
    recommended_price_action = "NONE"
    explanation = "No strong price intelligence signal yet"
    confidence_score = 40

    if opportunity.opportunity_type == "PRICE_DROP_OR_LOW_STOCK_NUDGE":
        market_status = "COMPARABLE_PRODUCT"
        price_position = "REVIEW_NEEDED"
        price_opportunity = "HIGH_INTENT_PRICE_OPPORTUNITY"
        recommended_price_action = "TEST_PRICE_DROP_OR_SCARCITY"
        explanation = "High-intent product with strong commitment signals; test a price drop or urgency lever"
        confidence_score = 85

    elif opportunity.opportunity_type == "WISHLIST_PROMPT_TEST":
        market_status = "COMPARABLE_PRODUCT"
        price_position = "UNCLEAR"
        price_opportunity = "COMMITMENT_GAP"
        recommended_price_action = "IMPROVE_WISHLIST_PLACEMENT_BEFORE_PRICE_CHANGE"
        explanation = "Users show interest but low commitment; improve CTA before changing price"
        confidence_score = 70

    elif opportunity.opportunity_type == "FRICTION_OR_PRICE_SENSITIVITY":
        market_status = "COMPARABLE_PRODUCT"
        price_position = "POSSIBLY_TOO_HIGH"
        price_opportunity = "PRICE_OR_TRUST_FRICTION"
        recommended_price_action = "REVIEW_PRICE_AND_TRUST_SIGNALS"
        explanation = "Users spend time on the page but do not commit; review pricing, trust, or offer clarity"
        confidence_score = 78

    elif opportunity.opportunity_type == "HIGH_INTEREST_PRODUCT":
        market_status = "COMPARABLE_PRODUCT"
        price_position = "MONITOR"
        price_opportunity = "MONITOR_MARKET_RESPONSE"
        recommended_price_action = "MONITOR_BEFORE_CHANGING_PRICE"
        explanation = "Product shows strong internal interest; monitor before applying pricing changes"
        confidence_score = 65

    return (
        market_status,
        price_position,
        price_opportunity,
        recommended_price_action,
        explanation,
        confidence_score,
    )


def update_price_intelligence(db: Session, product_url: str, shop_domain: str) -> None:
    """
    Derive and upsert a PriceIntelligence row from the ProductOpportunity for
    the given (shop_domain, product_url) pair.

    Both arguments are required.  Raises ValueError on missing input so the
    caller always knows exactly what went wrong rather than silently operating
    on the wrong tenant's data.
    """
    if not product_url:
        raise ValueError("update_price_intelligence: product_url is required")
    if not shop_domain:
        raise ValueError("update_price_intelligence: shop_domain is required")

    opportunity = (
        db.query(ProductOpportunity)
        .filter(
            ProductOpportunity.shop_domain == shop_domain,
            ProductOpportunity.product_url == product_url,
        )
        .first()
    )

    if not opportunity:
        return

    (
        market_status,
        price_position,
        price_opportunity,
        recommended_price_action,
        explanation,
        confidence_score,
    ) = classify_price_intelligence(opportunity)

    existing = (
        db.query(PriceIntelligence)
        .filter(
            PriceIntelligence.shop_domain == shop_domain,
            PriceIntelligence.product_url == product_url,
        )
        .first()
    )

    if not existing:
        existing = PriceIntelligence(shop_domain=shop_domain, product_url=product_url)
        db.add(existing)
        db.flush()

    existing.market_status = market_status
    existing.price_position = price_position
    existing.price_opportunity = price_opportunity
    existing.recommended_price_action = recommended_price_action
    existing.intelligence_explanation = explanation
    existing.confidence_score = confidence_score
    existing.plan_required = "pro"
    existing.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()

    update_unique_product_detection(db, product_url, shop_domain)
