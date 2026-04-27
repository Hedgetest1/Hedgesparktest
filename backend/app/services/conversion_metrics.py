"""
conversion_metrics.py — Real product-level conversion signals from ingested orders.

Public interface
----------------
    get_real_product_conversion_map(db: Session, shop_domain: str)
        -> dict[str, dict]

        Returns a mapping:
            { product_url: { "purchases": int, "revenue": float } }

        Built from shop_orders.line_items where each item has a populated
        product_url field.  Returns an empty dict (not an error) when no
        matchable line items exist — the caller must fall back to inferred
        conversion in that case.

Design: matching strategy and known limitations
-----------------------------------------------
All behavioral tables in WishSpark use product_url (/products/{handle}) as the
primary product key.  Shopify order line_items carry product_id (an integer),
NOT the handle.  A direct join between the two is impossible without one of:

  Option A  — A product catalog table mapping product_id → handle (requires
              Shopify Product API or Storefront API call at webhook time).
              This is the target architecture; not yet implemented.

  Option B  — Tracker-side product_id capture: if spark-tracker.js reads
              window.ShopifyAnalytics.meta.product.id on product pages and
              sends it as part of the track event, we can build the mapping
              from the events table.  Not yet implemented.

  Option C  — Title slugging: "Best Seller" → "best-seller" → /products/best-seller.
              Intentionally NOT implemented.  Shopify handles can be manually
              customized by merchants and may not match the title slug.
              Forcing this match would produce silently wrong conversion data.

Current behaviour (v1)
----------------------
When order_ingestion.py parses a line item, it stores product_url from
the payload if present (a forward-compatible field).  In the current Shopify
webhook shape, this field is absent, so product_url in line_items is None.

As a result, get_real_product_conversion_map() returns an empty dict for all
existing shops.  The callers fall back to inferred conversion correctly.

When either Option A or Option B is implemented, product_url will be populated
in line_items and this function will begin returning real data automatically —
no further changes required in the action engine or revenue radar.

Activation path
---------------
1.  Add product_id capture to spark-tracker.js (simplest path):
    On product pages: attach product_id = ShopifyAnalytics.meta.product.id
    to track events.  Store in events.product_id (new column).  Build mapping:
      SELECT DISTINCT product_url, product_id FROM events WHERE product_id IS NOT NULL

2.  At order_ingestion time: look up handle from the above mapping, set
    product_url = /products/{handle} in the line item before persisting.

3.  get_real_product_conversion_map() returns real data automatically.

This file sets up the interface correctly so that all callers are already
wired and ready — only the data source needs to be populated.

Activation path — now implemented
----------------------------------
spark-tracker.js captures window.ShopifyAnalytics.meta.product.id on product
pages and sends it as product_id in track events (since migration o1a2b3c4d5e6).

At order ingestion time, order_ingestion.upsert_order() calls
build_product_id_to_url_map() to resolve Shopify product_id → product_url,
then enriches each line item before persisting.

Once any product page has been visited by ANY visitor after the tracker update,
the mapping is available and real conversion flows automatically for that product.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def build_product_id_to_url_map(
    db: Session,
    shop_domain: str,
) -> dict[str, str]:
    """
    Build a mapping { shopify_product_id: product_url } from the events table.

    Uses rows where both product_id and product_url are populated — guaranteed
    to be on-product-page events from spark-tracker.js (since migration
    o1a2b3c4d5e6).

    Called by order_ingestion.upsert_order() to enrich line_items before
    persisting each order.  An empty dict means no product pages have been
    visited since the tracker update — all enrichments are skipped and the
    system falls back to inferred conversion correctly.

    Deduplication: a single (product_id, product_url) pair is sufficient.
    If a product_id maps to multiple product_urls (edge case: merchant changed
    the handle), the first row returned by the DB is used — acceptable since
    both old and new handles are valid canonical paths.

    Returns
    -------
    dict  { str(product_id): product_url }   — may be empty
    """
    try:
        rows = db.execute(
            text(
                """
                SELECT DISTINCT product_id, product_url
                FROM events
                WHERE shop_domain = :shop
                  AND product_id IS NOT NULL
                  AND product_url IS NOT NULL
                """
            ),
            {"shop": shop_domain},
        ).fetchall()
    except Exception as exc:
        log.error(
            "conversion_metrics: build_product_id_to_url_map DB error shop=%s: %s",
            shop_domain, exc,
        )
        return {}

    result: dict[str, str] = {}
    for row in rows:
        pid = str(row[0]).strip()
        url = str(row[1]).strip().lower().rstrip("/")
        if pid and url and pid not in result:
            result[pid] = url

    log.debug(
        "conversion_metrics: product_id_map shop=%s entries=%d",
        shop_domain, len(result),
    )
    return result


def get_real_product_conversion_map(
    db: Session,
    shop_domain: str,
) -> dict[str, dict[str, Any]]:
    """
    Build a real conversion map from ingested Shopify orders.

    Returns
    -------
    dict  mapping  product_url → { "purchases": int, "revenue": float }

    An empty dict is returned (not an error) when:
      - No orders have been ingested for this shop, or
      - No line items contain a product_url (current state for most shops).

    The caller is responsible for falling back to inferred conversion when
    this dict does not contain an entry for a given product_url.
    """
    try:
        rows = db.execute(
            text(
                """
                SELECT line_items
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND jsonb_typeof(line_items) = 'array'
                  AND jsonb_array_length(line_items) > 0
                """
            ),
            {"shop": shop_domain},
        ).fetchall()
    except Exception as exc:
        log.error(
            "conversion_metrics: DB error for shop=%s: %s — returning empty map",
            shop_domain, exc,
        )
        return {}

    result: dict[str, dict[str, Any]] = {}
    matched   = 0
    unmatched = 0

    for row in rows:
        items: list[dict] = row[0] or []
        for item in items:
            product_url = item.get("product_url")
            if not product_url:
                # No product_url in this line item — cannot match to system records.
                # This is expected until tracker or Product API enrichment is in place.
                unmatched += 1
                continue

            product_url = product_url.strip().lower().rstrip("/")
            if product_url not in result:
                result[product_url] = {"purchases": 0, "revenue": 0.0}

            quantity = int(item.get("quantity") or 1)
            price    = float(item.get("price") or 0.0)

            result[product_url]["purchases"] += quantity
            result[product_url]["revenue"]   += price * quantity
            matched += 1

    log.debug(
        "conversion_metrics: shop=%s matched_line_items=%d unmatched=%d products_with_real_data=%d",
        shop_domain, matched, unmatched, len(result),
    )

    if matched == 0 and unmatched > 0:
        log.info(
            "conversion_metrics: shop=%s — %d line items found but none have product_url. "
            "Real conversion unavailable until tracker or Product API enrichment is added. "
            "Falling back to inferred conversion for all products.",
            shop_domain, unmatched,
        )

    return result


def compute_real_conversion_probability(
    product_url: str,
    conv_map: dict[str, dict[str, Any]],
    views_24h: int,
    views_7d: int,
) -> float | None:
    """
    Derive a real conversion probability for a product from actual order data.

    Uses purchases from conv_map divided by a view window to produce a rate.
    Returns None if no real conversion data is available (caller should fall
    back to inferred conversion).

    Rate uses views_7d as the denominator for statistical stability — a
    single purchase against a single view would produce a misleadingly high
    rate.  Requires at least 20 total views for the rate to be used.

    Parameters
    ----------
    product_url  Canonical product URL, e.g. /products/best-seller.
    conv_map     Output of get_real_product_conversion_map().
    views_24h    24-hour view count from product_metrics.
    views_7d     7-day view count from product_metrics (denominator).

    Returns
    -------
    float in [0.001, 1.0] — real rate — or None if insufficient data.
    """
    data = conv_map.get(product_url)
    if not data or data["purchases"] == 0:
        return None

    total_views = max(views_7d, views_24h, 1)
    if total_views < 20:
        # Too few views for a statistically meaningful rate.
        # Returning None forces fallback to inferred conversion.
        log.debug(
            "conversion_metrics: %s has real purchases but only %d views — "
            "insufficient for reliable rate, using inferred fallback",
            product_url, total_views,
        )
        return None

    rate = data["purchases"] / total_views
    # Clamp: at most 100% probability; at least 0.1% (non-zero signal)
    clamped = max(0.001, min(rate, 1.0))

    log.debug(
        "conversion_metrics: %s real_cvr=%.4f (purchases=%d / views=%d)",
        product_url, clamped, data["purchases"], total_views,
    )
    return clamped
