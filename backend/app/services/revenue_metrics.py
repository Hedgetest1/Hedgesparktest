"""
revenue_metrics.py — Real per-merchant revenue metric resolvers.

Public interface
----------------
    get_shop_aov(db: Session, shop_domain: str, currency: str | None = None) -> float
        Return the average order value for the shop, computed from
        real Shopify order data in shop_orders.

        When currency is provided, only orders in that currency are included.
        When currency is None, all orders are included (blended AOV).

        Falls back to FALLBACK_AOV = 50.0 only when the shop has no
        ingested orders yet (i.e. the webhook has not delivered anything).
        Logs clearly at WARNING level when the fallback is used so the
        condition is visible in production logs.

    get_shop_currency(db: Session, shop_domain: str) -> str | None
        Return the most common order currency for the shop, or None when
        no orders have been ingested yet.  Used to select the currency-aware
        AOV path automatically when merchant currency preference is not stored.

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

Currency handling
-----------------
v1: per-currency AOV when currency is provided; blended AOV when not.

For single-currency shops (the majority of Shopify stores) the blended
and per-currency values are identical.  For multi-currency shops:
  - Blended AOV introduces distortion (EUR and USD orders averaged together).
  - Per-currency AOV is correct but requires knowing which currency to filter on.

The caller chain is:
  1. get_shop_currency() → resolves the shop's dominant currency
  2. get_shop_aov(currency=resolved_currency) → returns currency-correct AOV
  3. If no orders exist → FALLBACK_AOV (50.0)

Callers that do not pass a currency still get a blended value — this is
backward-compatible and acceptable for single-currency shops.
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


# Redis cache for shop currency. A shop's primary_currency is set from
# Shopify shop.json at install and essentially never changes. With 20+
# Pro endpoints calling get_shop_currency() on every dashboard paint,
# caching cuts ~25 DB round-trips per paint down to 1 per TTL window
# (bounded Redis lookup instead). At 10k merchants this is load we
# genuinely avoid. 1-hour TTL is a safe compromise — merchants who
# change their Shopify base currency see the new symbol within an hour
# (and can force-refresh by uninstall/reinstall which wipes the key).
_CURRENCY_CACHE_PREFIX = "hs:shop_ccy:v1"
_CURRENCY_CACHE_TTL_SECONDS = 3600


def _cache_key(shop_domain: str) -> str:
    return f"{_CURRENCY_CACHE_PREFIX}:{shop_domain}"


def _cache_enabled() -> bool:
    """Skip the cache under APP_ENV=test. The test suite uses SAVEPOINT
    rollback for per-test hermeticity; a Redis cache that outlives the
    SAVEPOINT would leak state between tests that share a shop_domain
    (merchant fixtures reuse 'test-shop-a.myshopify.com' across tests).
    Prod + staging read & write normally."""
    import os
    return os.environ.get("APP_ENV") != "test"


def _cache_get(shop_domain: str) -> str | None:
    """Best-effort Redis read. Never raises — a Redis outage falls
    through to the authoritative DB lookup. Observed via
    record_silent_return so cache-degradation is visible in metrics."""
    if not _cache_enabled():
        return None
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("revenue_metrics.currency_cache.get.no_client")
            return None
        raw = rc.get(_cache_key(shop_domain))
        if not raw:
            # Genuine cache miss — expected on first read per shop per TTL.
            # Don't count as a failure; caller proceeds to authoritative lookup.
            return None
        # redis-py returns bytes by default; decode safely.
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except Exception:
        record_silent_return("revenue_metrics.currency_cache.get.exception")
        return None


def _cache_set(shop_domain: str, currency: str) -> None:
    """Best-effort Redis write. Never raises — cache write is fire-and-forget
    optimization, observed via record_silent_return on failure."""
    if not _cache_enabled():
        return
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("revenue_metrics.currency_cache.set.no_client")
            return
        rc.setex(_cache_key(shop_domain), _CURRENCY_CACHE_TTL_SECONDS, currency)
    except Exception:
        record_silent_return("revenue_metrics.currency_cache.set.exception")


def invalidate_shop_currency_cache(shop_domain: str) -> None:
    """Public hook — call when a merchant's primary_currency changes
    (uninstall/reinstall, manual currency switch). No-op on Redis miss.
    The 1h TTL is the upper bound on staleness even when this fails."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("revenue_metrics.currency_cache.invalidate.no_client")
            return
        rc.delete(_cache_key(shop_domain))
    except Exception:
        record_silent_return("revenue_metrics.currency_cache.invalidate.exception")


def get_shop_currency(db: Session, shop_domain: str) -> str | None:
    """
    Return the shop's primary currency.

    Lookup order:
    1. Redis cache (1h TTL) — avoids ~25 DB round-trips per dashboard paint
    2. merchant.primary_currency (populated from Shopify shop.json at install)
    3. MODE() over shop_orders.currency (expensive fallback for pre-migration merchants)

    Returns ISO 4217 code (e.g. "USD") or None if no data available.
    """
    cached = _cache_get(shop_domain)
    if cached:
        return cached

    try:
        from app.models.merchant import Merchant
        row = db.query(Merchant.primary_currency).filter(
            Merchant.shop_domain == shop_domain
        ).first()
        if row and row[0]:
            currency = str(row[0])
            _cache_set(shop_domain, currency)
            return currency
    except Exception as exc:
        log.warning("revenue_metrics: primary_currency lookup failed for shop=%s: %s", shop_domain, exc)

    # Fallback: derive from order history (pre-migration merchants)
    try:
        result = db.execute(
            text("""
                SELECT MODE() WITHIN GROUP (ORDER BY currency) AS dominant_currency
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND currency IS NOT NULL
            """),
            {"shop": shop_domain},
        )
        row = result.fetchone()
        if row and row[0]:
            currency = str(row[0])
            _cache_set(shop_domain, currency)
            return currency
        return None
    except Exception as exc:
        log.warning(
            "revenue_metrics: failed to resolve currency for shop=%s: %s",
            shop_domain, exc,
        )
        return None


def get_shop_timezone(db: Session, shop_domain: str) -> str:
    """Return the shop's IANA timezone (e.g. 'America/New_York').

    Falls back to 'UTC' for merchants installed before the timezone field
    was added, or if the Shopify API didn't return a timezone.
    """
    try:
        from app.models.merchant import Merchant
        row = db.query(Merchant.iana_timezone).filter(
            Merchant.shop_domain == shop_domain
        ).first()
        if row and row[0]:
            return str(row[0])
    except Exception as exc:
        log.warning("revenue_metrics: iana_timezone lookup failed for shop=%s: %s", shop_domain, exc)
    return "UTC"


def get_shop_aov(
    db: Session,
    shop_domain: str,
    currency: str | None = None,
) -> float:
    """
    Compute the real average order value for a shop from shop_orders.

    Parameters
    ----------
    db          Active SQLAlchemy session.
    shop_domain Merchant shop domain (e.g. "example.myshopify.com").
    currency    ISO 4217 code to filter (e.g. "USD").  When None, all
                orders are included (blended AOV — correct for single-currency
                shops, distorted for multi-currency shops).

    Returns
    -------
    float — real AOV if orders exist, FALLBACK_AOV otherwise.
            Always returns a positive float.  Never raises.

    Logging
    -------
    Logs at WARNING when the fallback path is taken so operators can
    identify shops that have not yet had order webhooks processed.
    Logs at DEBUG when the real AOV is resolved so log analysis can
    track revenue context over time.
    """
    try:
        if currency:
            result = db.execute(
                text("""
                    SELECT AVG(total_price) AS avg_aov
                    FROM shop_orders
                    WHERE shop_domain = :shop
                      AND currency = :currency
                      AND total_price > 0
                """),
                {"shop": shop_domain, "currency": currency},
            )
        else:
            result = db.execute(
                text("""
                    SELECT AVG(total_price) AS avg_aov
                    FROM shop_orders
                    WHERE shop_domain = :shop
                      AND total_price > 0
                """),
                {"shop": shop_domain},
            )

        row = result.fetchone()
        if row is None or row[0] is None:
            log.warning(
                "revenue_metrics: no orders found for shop=%s currency=%s — "
                "using fallback AOV=%.2f",
                shop_domain, currency or "any", FALLBACK_AOV,
            )
            return FALLBACK_AOV

        aov = float(row[0])
        if aov <= 0:
            log.warning(
                "revenue_metrics: computed AOV=%.2f <= 0 for shop=%s currency=%s — "
                "using fallback AOV=%.2f",
                aov, shop_domain, currency or "any", FALLBACK_AOV,
            )
            return FALLBACK_AOV

        log.debug(
            "revenue_metrics: resolved AOV=%.2f for shop=%s currency=%s",
            aov, shop_domain, currency or "blended",
        )
        return aov

    except Exception as exc:
        log.error(
            "revenue_metrics: error computing AOV for shop=%s currency=%s: %s — "
            "using fallback AOV=%.2f",
            shop_domain, currency or "any", exc, FALLBACK_AOV,
        )
        return FALLBACK_AOV
