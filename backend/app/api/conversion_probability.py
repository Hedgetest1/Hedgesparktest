from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.services.conversion_service import infer_conversion_outcome

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

router = APIRouter(prefix="/conversion-probability", tags=["conversion-probability"])


def _rows(query: str) -> list[dict]:
    db = SessionLocal()
    try:
        result = db.execute(text(query))
        return [dict(row._mapping) for row in result.fetchall()]
    finally:
        db.close()


@router.get("/top")
def top_conversion_candidates():
    products = _rows(
        """
        SELECT
            vps.product_url AS product_id,
            vps.product_url AS product_name,
            COALESCE(SUM(vps.total_views), 0) AS total_views,
            COALESCE(SUM(CASE WHEN COALESCE(vps.wishlist_added, FALSE) THEN 1 ELSE 0 END), 0) AS wishlist_adds,
            COALESCE(ROUND(AVG(vps.intent_score), 2), 0) AS avg_intent_score
        FROM visitor_product_state vps
        GROUP BY vps.product_url
        ORDER BY avg_intent_score DESC, total_views DESC
        LIMIT 10
        """
    )

    market_lookup_rows = _rows(
        """
        SELECT
            product_url AS product_id,
            COALESCE(lookup_confidence, 70) AS market_confidence,
            CASE
                WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'LIKELY_UNIQUE' THEN 80
                WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'UNCLEAR' THEN 55
                ELSE 35
            END AS uniqueness_score,
            CASE
                WHEN UPPER(COALESCE(comparable_presence, 'REQUIRES_EXTERNAL_CHECK')) = 'LIKELY_FOUND_EXTERNALLY' THEN 80
                WHEN UPPER(COALESCE(comparable_presence, 'REQUIRES_EXTERNAL_CHECK')) = 'REQUIRES_EXTERNAL_CHECK' THEN 55
                ELSE 30
            END AS comparability_score
        FROM market_lookup
        """
    )

    price_rows = _rows(
        """
        SELECT
            product_url AS product_id,
            COALESCE(confidence_score, 0) AS price_confidence,
            CASE
                WHEN UPPER(COALESCE(price_opportunity, '')) = 'HIGH_INTENT_PRICE_OPPORTUNITY' THEN 75
                ELSE 35
            END AS price_pressure_score
        FROM price_intelligence
        """
    )

    market_map = {str(row["product_id"]): row for row in market_lookup_rows}
    price_map = {str(row["product_id"]): row for row in price_rows}

    results = []
    for product in products:
        pid = str(product.get("product_id"))
        enriched = {
            **product,
            **market_map.get(pid, {}),
            **price_map.get(pid, {}),
        }
        results.append(infer_conversion_outcome(enriched))

    results = sorted(
        results,
        key=lambda x: float(x.get("revenue_opportunity_score", 0)),
        reverse=True,
    )

    return {
        "top_conversion_candidates": results
    }
