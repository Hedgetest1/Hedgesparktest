from datetime import datetime
from sqlalchemy.orm import Session
from app.services.market_lookup_engine import update_market_lookup
from app.models.product_opportunity import ProductOpportunity
from app.models.price_intelligence import PriceIntelligence
from app.models.unique_product_detection import UniqueProductDetection


def classify_uniqueness(opportunity, price_info):

    uniqueness_status = "UNSURE"
    uniqueness_score = 50
    evidence_summary = "Not enough signals yet"
    recommended_strategy = "COLLECT_MORE_DATA"

    if (
        opportunity.avg_intent_score >= 80
        and opportunity.wishlist_count >= 1
        and opportunity.opportunity_type == "PRICE_DROP_OR_LOW_STOCK_NUDGE"
    ):
        uniqueness_status = "UNIQUE_LIKELY"
        uniqueness_score = 78
        evidence_summary = "Strong internal demand and commitment signals"
        recommended_strategy = "USE_SCARCITY_OR_STORYTELLING"

    elif (
        opportunity.opportunity_type == "FRICTION_OR_PRICE_SENSITIVITY"
        or (
            price_info
            and price_info.price_opportunity == "PRICE_OR_TRUST_FRICTION"
        )
    ):
        uniqueness_status = "COMPARABLE_LIKELY"
        uniqueness_score = 72
        evidence_summary = "Visitors hesitate; product may be price compared"
        recommended_strategy = "IMPROVE_TRUST_AND_COMPARE_PRICE"

    elif (
        opportunity.opportunity_type == "WISHLIST_PROMPT_TEST"
        and opportunity.avg_intent_score >= 60
    ):
        uniqueness_status = "UNSURE"
        uniqueness_score = 58
        evidence_summary = "Interest exists but commitment incomplete"
        recommended_strategy = "TEST_POSITIONING"

    return (
        uniqueness_status,
        uniqueness_score,
        evidence_summary,
        recommended_strategy,
    )


def update_unique_product_detection(db: Session, product_url: str, shop_domain: str) -> None:
    """
    Derive and upsert a UniqueProductDetection row from ProductOpportunity and
    PriceIntelligence for the given (shop_domain, product_url) pair.

    Both arguments are required.  Raises ValueError on missing input.
    """
    if not product_url:
        raise ValueError("update_unique_product_detection: product_url is required")
    if not shop_domain:
        raise ValueError("update_unique_product_detection: shop_domain is required")

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

    price_info = (
        db.query(PriceIntelligence)
        .filter(
            PriceIntelligence.shop_domain == shop_domain,
            PriceIntelligence.product_url == product_url,
        )
        .first()
    )

    (
        uniqueness_status,
        uniqueness_score,
        evidence_summary,
        recommended_strategy,
    ) = classify_uniqueness(opportunity, price_info)

    existing = (
        db.query(UniqueProductDetection)
        .filter(
            UniqueProductDetection.shop_domain == shop_domain,
            UniqueProductDetection.product_url == product_url,
        )
        .first()
    )

    if not existing:
        existing = UniqueProductDetection(shop_domain=shop_domain, product_url=product_url)
        db.add(existing)
        db.flush()

    existing.uniqueness_status = uniqueness_status
    existing.uniqueness_score = uniqueness_score
    existing.evidence_summary = evidence_summary
    existing.recommended_strategy = recommended_strategy
    existing.plan_required = "pro"
    existing.updated_at = datetime.utcnow()

    db.commit()
    update_market_lookup(db, product_url, shop_domain)
