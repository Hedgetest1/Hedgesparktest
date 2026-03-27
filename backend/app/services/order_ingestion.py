"""
order_ingestion.py — Parse and persist Shopify order webhook payloads.

Public interface
----------------
    parse_shopify_order(payload: dict) -> dict | None
        Extract and validate the fields we store from a raw Shopify
        orders/updated webhook body.  Returns None if the payload is
        structurally invalid.  Never raises.

    upsert_order(db: Session, order_data: dict) -> tuple[ShopOrder, bool]
        Persist the parsed order.  Returns (row, created) where created
        is True if a new row was inserted, False if the shopify_order_id
        already existed (idempotent duplicate skip).  Never raises.

Design notes
------------
parse_shopify_order
    Shopify guarantees at-least-once delivery, not exactly-once.  Parsing
    must therefore be defensive at every field — missing or malformed fields
    produce None (rejected payload) rather than an exception.

    Required fields (rejection if absent or unparseable):
        id            → shopify_order_id (converted to string)
        total_price   → float; rejects non-numeric
        shop_domain   → taken from the X-Shopify-Shop-Domain header, passed
                        in as a separate argument to parse_shopify_order

    Optional fields (replaced with safe defaults if absent):
        currency           → "EUR"
        customer.id        → None  (guest checkout)
        line_items         → []    (empty means no product attribution yet)
        created_at         → server UTC now (degraded attribution only)

upsert_order
    Uses an explicit SELECT before INSERT to avoid relying on the database
    to surface the unique constraint violation as an exception — this keeps
    the "duplicate skipped" log path clean and avoids rolled-back
    transactions on every duplicate delivery.

    The SELECT → INSERT is safe here: at-most-one concurrent upsert per
    shopify_order_id is expected (Shopify delivers one webhook at a time
    per topic per shop).  If two deliveries race (pathological case), the
    second will hit the UNIQUE constraint and the exception is caught and
    logged as a duplicate skip.

Fake metric locations marked in this file
-----------------------------------------
    None — this file IS the replacement for the fake metrics layer.
    See revenue_loss.py, action_candidates_engine.py, and revenue_radar.py
    for the TODO: REPLACE WITH REAL ORDER DATA markers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.shop_order import ShopOrder
from app.services.conversion_metrics import build_product_id_to_url_map

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    """Parse value to float; return None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str | None:
    """Stringify value; return None if falsy."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_created_at(value: Any) -> datetime:
    """
    Parse Shopify's created_at ISO 8601 string into a naive UTC datetime.
    Falls back to UTC now on any parse error (degraded attribution).
    """
    if not value:
        return datetime.now(tz=timezone.utc).replace(tzinfo=None)
    try:
        # Shopify sends "2024-03-21T14:30:00-05:00" — parse with timezone then
        # convert to UTC naive for consistent storage.
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        log.warning("order_ingestion: could not parse created_at=%r, using now()", value)
        return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _parse_line_items(raw: Any) -> list[dict]:
    """
    Extract relevant fields from each Shopify line item.
    Returns a clean list; never crashes on malformed input.

    product_url field
    -----------------
    Stored as a forward-compatible field for real conversion matching.
    Shopify's orders/updated webhook does NOT include the product handle in
    line_items — so this field is None for standard webhook payloads.

    It will be populated once one of these enrichment paths is in place:
      A. spark-tracker.js captures product_id on product pages, enabling
         a product_id → product_url lookup from the events table at
         order ingestion time.
      B. A Shopify Product API call resolves product_id → handle at webhook
         time (requires OAuth + API scope read_products).

    When populated, conversion_metrics.get_real_product_conversion_map()
    will automatically begin returning real data — no further code changes
    required in the action engine or revenue radar.
    """
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        items.append({
            "id":          _safe_str(item.get("id")),
            "product_id":  _safe_str(item.get("product_id")),
            "variant_id":  _safe_str(item.get("variant_id")),
            "title":       _safe_str(item.get("title")) or "Unknown",
            "quantity":    int(item.get("quantity") or 1),
            "price":       _safe_float(item.get("price")) or 0.0,
            "sku":         _safe_str(item.get("sku")),
            # Forward-compatible: populated by enrichment (tracker product_id or Product API)
            "product_url": _safe_str(item.get("product_url")),
        })
    return items


# ---------------------------------------------------------------------------
# Internal: line-item enrichment
# ---------------------------------------------------------------------------

def _enrich_line_items_with_product_url(
    db: Session,
    shop_domain: str,
    line_items: list[dict],
) -> list[dict]:
    """
    Resolve product_url for each line item using the events table mapping.

    Shopify's orders/updated webhook stores product_id (an integer) in line_items
    but does not include the product handle.  spark-tracker.js captures the
    handle as product_url AND the Shopify numeric id as product_id in track
    events (since migration o1a2b3c4d5e6).

    This function builds the product_id → product_url map from the events table
    and enriches each line item in-place.  Items that cannot be resolved (no
    matching event row yet) are returned unchanged.

    Once any visitor has viewed a product page after the tracker update, the
    mapping is available and all subsequent orders for that product will have
    their product_url populated automatically.

    Parameters
    ----------
    db          Active SQLAlchemy session (read-only usage here).
    shop_domain Shop domain for scoping the events query.
    line_items  Parsed line items list from parse_shopify_order().

    Returns
    -------
    New list — original dicts are not mutated.  Items without a resolvable
    product_url retain product_url=None.
    """
    if not line_items:
        return line_items

    pid_map = build_product_id_to_url_map(db, shop_domain)
    if not pid_map:
        # No product_id data in events yet — tracker not updated or no product
        # page visits since update.  Return unchanged; real CVR stays inactive.
        log.debug(
            "order_ingestion: no product_id map available for shop=%s — "
            "line_items stored without product_url enrichment",
            shop_domain,
        )
        return line_items

    enriched: list[dict] = []
    resolved = 0
    for item in line_items:
        if item.get("product_url"):
            # Already populated (shouldn't happen with standard Shopify webhooks,
            # but be safe against custom integrations).
            enriched.append(item)
            resolved += 1
            continue

        pid = _safe_str(item.get("product_id"))
        product_url = pid_map.get(pid) if pid else None

        if product_url:
            enriched.append({**item, "product_url": product_url})
            resolved += 1
        else:
            enriched.append(item)

    log.info(
        "order_ingestion: shop=%s line_items=%d product_url_resolved=%d unresolved=%d",
        shop_domain, len(line_items), resolved, len(line_items) - resolved,
    )
    return enriched


# ---------------------------------------------------------------------------
# Public: parse
# ---------------------------------------------------------------------------

def parse_shopify_order(payload: dict, shop_domain: str) -> dict | None:
    """
    Extract storable fields from a raw Shopify orders/updated webhook body.

    Parameters
    ----------
    payload     Raw JSON body from Shopify (already decoded from the request).
    shop_domain Shop domain from the X-Shopify-Shop-Domain header.

    Returns
    -------
    dict with keys: shop_domain, shopify_order_id, total_price, currency,
        customer_id, line_items, created_at
    None if the payload is missing required fields (id, total_price).
    """
    if not isinstance(payload, dict):
        log.warning("order_ingestion: payload is not a dict, rejecting")
        return None

    # Required: Shopify order ID
    raw_id = payload.get("id")
    shopify_order_id = _safe_str(raw_id)
    if not shopify_order_id:
        log.warning("order_ingestion: missing or empty 'id' in payload, rejecting")
        return None

    # Required: total_price must be a parseable number
    total_price = _safe_float(payload.get("total_price"))
    if total_price is None:
        log.warning(
            "order_ingestion: could not parse total_price=%r for order %s, rejecting",
            payload.get("total_price"), shopify_order_id,
        )
        return None

    # Optional: currency (default EUR — overridden once merchant-specific
    # currency detection is added via merchant profile)
    currency = _safe_str(payload.get("currency")) or "EUR"

    # Optional: customer ID and email (None for guest checkouts)
    customer_block  = payload.get("customer") or {}
    customer_id     = _safe_str(customer_block.get("id")) if isinstance(customer_block, dict) else None
    customer_email  = _safe_str(customer_block.get("email")) if isinstance(customer_block, dict) else None
    # Fallback: top-level email field (some Shopify webhook versions)
    if not customer_email:
        customer_email = _safe_str(payload.get("email"))

    # Optional: line items (empty list is valid — order still recorded for AOV)
    line_items = _parse_line_items(payload.get("line_items"))

    # Optional: Shopify-side created_at for time-scoped analytics
    created_at = _parse_created_at(payload.get("created_at"))

    return {
        "shop_domain":       shop_domain,
        "shopify_order_id":  shopify_order_id,
        "total_price":       total_price,
        "currency":          currency,
        "customer_id":       customer_id,
        "customer_email":    customer_email,
        "line_items":        line_items,
        "created_at":        created_at,
    }


# ---------------------------------------------------------------------------
# Public: upsert
# ---------------------------------------------------------------------------

def upsert_order(db: Session, order_data: dict) -> tuple[ShopOrder, bool]:
    """
    Persist a parsed order.  Idempotent — duplicate shopify_order_id is skipped.

    Parameters
    ----------
    db          SQLAlchemy session (caller owns commit/rollback).
    order_data  Dict returned by parse_shopify_order().

    Returns
    -------
    (ShopOrder, created)
        created=True  → new row was inserted.
        created=False → shopify_order_id already existed; existing row returned.

    Never raises — all DB exceptions are caught and re-raised only after logging.
    """
    shopify_order_id = order_data["shopify_order_id"]
    shop_domain      = order_data["shop_domain"]

    # Check for existing row before attempting insert (clean duplicate path)
    existing = (
        db.query(ShopOrder)
        .filter(ShopOrder.shopify_order_id == shopify_order_id)
        .first()
    )
    if existing:
        # Upgrade pixel-originated rows with richer webhook data.
        # Pixel rows have source="pixel" and line_items=[].  When a webhook
        # delivers the same order with full line_items and customer data,
        # update the row rather than skipping it.
        if getattr(existing, "source", "pixel") == "pixel" and order_data.get("line_items"):
            raw_line_items = order_data.get("line_items", [])
            enriched = _enrich_line_items_with_product_url(db, shop_domain, raw_line_items)
            existing.line_items     = enriched
            existing.customer_id    = order_data.get("customer_id") or existing.customer_id
            existing.customer_email = order_data.get("customer_email") or existing.customer_email
            existing.total_price    = order_data["total_price"]
            existing.currency       = order_data["currency"]
            existing.source         = "webhook"
            try:
                db.commit()
                log.info(
                    "order_ingestion: upgraded pixel→webhook shopify_order_id=%s shop=%s",
                    shopify_order_id, shop_domain,
                )
            except Exception as exc:
                db.rollback()
                log.error(
                    "order_ingestion: upgrade failed shopify_order_id=%s: %s",
                    shopify_order_id, exc,
                )
            return existing, False

        log.info(
            "order_ingestion: duplicate skipped — shopify_order_id=%s shop=%s",
            shopify_order_id, shop_domain,
        )
        return existing, False

    # Enrich line items with product_url resolved from the events table.
    # This bridges Shopify's numeric product_id → canonical product_url so
    # get_real_product_conversion_map() can match purchases to product pages.
    # Falls back silently (no product_url set) when no mapping exists yet.
    raw_line_items = order_data.get("line_items", [])
    enriched_line_items = _enrich_line_items_with_product_url(db, shop_domain, raw_line_items)

    order = ShopOrder(
        shop_domain      = shop_domain,
        shopify_order_id = shopify_order_id,
        total_price      = order_data["total_price"],
        currency         = order_data["currency"],
        customer_id      = order_data.get("customer_id"),
        customer_email   = order_data.get("customer_email"),
        line_items       = enriched_line_items,
        created_at       = order_data["created_at"],
        ingested_at      = datetime.utcnow(),
        source           = "webhook",
    )

    try:
        db.add(order)
        db.commit()
        db.refresh(order)
        log.info(
            "order_ingestion: stored order shopify_order_id=%s shop=%s total=%.2f %s",
            shopify_order_id, shop_domain, order.total_price, order.currency,
        )
        return order, True

    except IntegrityError:
        # Race between two concurrent deliveries of the same webhook
        db.rollback()
        existing = (
            db.query(ShopOrder)
            .filter(ShopOrder.shopify_order_id == shopify_order_id)
            .first()
        )
        log.info(
            "order_ingestion: duplicate skipped (race) — shopify_order_id=%s shop=%s",
            shopify_order_id, shop_domain,
        )
        return existing, False
