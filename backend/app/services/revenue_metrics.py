"""
revenue_metrics.py — Real per-merchant revenue metric resolvers.

Public interface
----------------
    get_shop_aov(db: Session, shop_domain: str) -> float
        Return the average order value for the shop, computed from
        real Shopify order data in shop_orders.

        Falls back to FALLBACK_AOV = 50.0 only when the shop has no
        ingested orders yet (i.e. the webhook has not delivered anything).
        Logs clearly at WARNING level when the fallback is used so the
        condition is visible in production logs.

Design intent
-------------
This module is the single source of truth for revenue context that feeds
the scoring pipeline.  All callers that previously passed aov=None to
calculate_expected_loss() must now call get_shop_aov() first.

The fallback (50.0) is intentionally preserved — it makes the system
degrade gracefully for new shops that have installed the webhook but
have not yet processed any orders.  Once orders are ingested, the real
AOV takes effect automatically on the next request with no migration or
config change needed.

Currency note
-------------
v1 computes AVG(total_price) across all currencies.  For single-currency
shops (the majority of Shopify stores) this is correct.  For multi-currency
shops, the resulting blended AOV introduces a small distortion.
A per-currency breakdown (WHERE currency = :currency) should be added once
merchant currency preference is stored in the merchants table.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Fallback AOV used when no real orders are available for the shop.
# Retained for new merchants who have installed the webhook but have not
# yet had a paid order ingested.  All pre-existing code used 50.0 — this
# preserves that baseline while making the fallback path explicit and logged.
FALLBACK_AOV: float = 50.0


def get_shop_aov(db: Session, shop_domain: str) -> float:
    """
    Compute the real average order value for a shop from shop_orders.

    Parameters
    ----------
    db          Active SQLAlchemy session.
    shop_domain Merchant shop domain (e.g. "example.myshopify.com").

    Returns
    -------
    float — real AOV if orders exist, FALLBACK_AOV otherwise.
            Always returns a positive float.  Never raises.
    """
    try:
        result = db.execute(
            text(
                """
                SELECT AVG(total_price) AS avg_aov
                FROM shop_orders
                WHERE shop_domain = :shop
                """
            ),
            {"shop": shop_domain},
        )
        row = result.fetchone()
        if row is None or row[0] is None:
            log.warning(
                "revenue_metrics: no orders found for shop=%s — using fallback AOV=%.2f",
                shop_domain, FALLBACK_AOV,
            )
            return FALLBACK_AOV

        aov = float(row[0])
        if aov <= 0:
            log.warning(
                "revenue_metrics: computed AOV=%.2f <= 0 for shop=%s — using fallback AOV=%.2f",
                aov, shop_domain, FALLBACK_AOV,
            )
            return FALLBACK_AOV

        log.debug(
            "revenue_metrics: resolved AOV=%.2f for shop=%s",
            aov, shop_domain,
        )
        return aov

    except Exception as exc:
        log.error(
            "revenue_metrics: error computing AOV for shop=%s: %s — using fallback AOV=%.2f",
            shop_domain, exc, FALLBACK_AOV,
        )
        return FALLBACK_AOV
