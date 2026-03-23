from datetime import datetime
from sqlalchemy.orm import Session

from app.models.unique_product_detection import UniqueProductDetection
from app.models.market_lookup import MarketLookup


def classify_market_lookup(unique_signal):
    lookup_status = "INFERRED_INTERNAL"
    comparable_presence = "UNKNOWN"
    uniqueness_hint = "UNSURE"
    lookup_confidence = 50
    market_summary = "Internal-only inference, no external lookup yet"
    recommended_next_step = "RUN_EXTERNAL_LOOKUP"

    if unique_signal.uniqueness_status == "UNIQUE_LIKELY":
        comparable_presence = "NOT_FOUND_YET"
        uniqueness_hint = "LIKELY_UNIQUE"
        lookup_confidence = 72
        market_summary = "Internal behavior suggests a product with unique perceived value"
        recommended_next_step = "CHECK_EXTERNAL_MATCHES_AND_STORYTELLING"

    elif unique_signal.uniqueness_status == "COMPARABLE_LIKELY":
        comparable_presence = "LIKELY_EXISTS_ELSEWHERE"
        uniqueness_hint = "LIKELY_COMPARABLE"
        lookup_confidence = 78
        market_summary = "Internal behavior suggests that users may compare this product with alternatives"
        recommended_next_step = "RUN_PRICE_AND_COMPETITOR_LOOKUP"

    elif unique_signal.uniqueness_status == "UNSURE":
        comparable_presence = "UNCLEAR"
        uniqueness_hint = "UNCERTAIN"
        lookup_confidence = 58
        market_summary = "Signals are inconclusive; more data or external lookup is needed"
        recommended_next_step = "COLLECT_MORE_SIGNALS"

    return (
        lookup_status,
        comparable_presence,
        uniqueness_hint,
        lookup_confidence,
        market_summary,
        recommended_next_step,
    )


def update_market_lookup(db: Session, product_url: str, shop_domain: str) -> None:
    """
    Derive and upsert a MarketLookup row from UniqueProductDetection for the
    given (shop_domain, product_url) pair.

    Both arguments are required.  Raises ValueError on missing input.
    """
    if not product_url:
        raise ValueError("update_market_lookup: product_url is required")
    if not shop_domain:
        raise ValueError("update_market_lookup: shop_domain is required")

    unique_signal = (
        db.query(UniqueProductDetection)
        .filter(
            UniqueProductDetection.shop_domain == shop_domain,
            UniqueProductDetection.product_url == product_url,
        )
        .first()
    )

    if not unique_signal:
        return

    (
        lookup_status,
        comparable_presence,
        uniqueness_hint,
        lookup_confidence,
        market_summary,
        recommended_next_step,
    ) = classify_market_lookup(unique_signal)

    existing = (
        db.query(MarketLookup)
        .filter(
            MarketLookup.shop_domain == shop_domain,
            MarketLookup.product_url == product_url,
        )
        .first()
    )

    if not existing:
        existing = MarketLookup(shop_domain=shop_domain, product_url=product_url)
        db.add(existing)
        db.flush()

    existing.lookup_status = lookup_status
    existing.comparable_presence = comparable_presence
    existing.uniqueness_hint = uniqueness_hint
    existing.lookup_confidence = lookup_confidence
    existing.market_summary = market_summary
    existing.recommended_next_step = recommended_next_step
    existing.plan_required = "pro"
    existing.updated_at = datetime.utcnow()

    db.commit()
