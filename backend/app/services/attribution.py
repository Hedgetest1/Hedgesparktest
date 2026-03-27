"""
attribution.py — Visitor-to-order attribution query layer.

Public interface
----------------
    get_converted_visitors(db, shop_domain, product_url, days) -> list[str]
        Return visitor_ids that purchased after viewing a given product URL
        within a lookback window.  Foundation for retargeting and segment
        analysis.

    get_visitor_behavior_before_purchase(db, shop_domain, visitor_id, shopify_order_id)
        -> dict
        Return the behavioral profile of a visitor on the product pages they
        visited before placing a specific order.  Foundation for empirical
        conversion profiling.

    get_product_conversion_profile(db, shop_domain, product_url, days) -> dict
        Return the aggregated behavioral profile of ALL visitors who converted
        on a given product vs those who did not.  This is the core empirical
        conversion intelligence output — what behavioral pattern predicts purchase?

Design intent
-------------
These functions are intentionally read-only and produce plain dicts.  They
do not cache, do not write, and do not raise.  All errors return empty/default
data with a log entry so callers never need to guard against exceptions.

Current state vs future state
------------------------------
v1 (now): these functions return real data when visitor_purchase_sessions rows
    exist.  For new shops, they return empty/default data gracefully.

v2 (next): the empirical conversion profiles from get_product_conversion_profile()
    replace the hand-crafted weights in conversion_service.py.  The model
    becomes self-calibrating per shop.

v3 (future): action agents use get_converted_visitors() to build retargeting
    audiences.  The attribution table becomes the feedback loop for measuring
    whether agent-executed actions produced real purchases.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def get_converted_visitors(
    db: Session,
    shop_domain: str,
    product_url: str,
    days: int = 30,
) -> list[str]:
    """
    Return visitor_ids that purchased after viewing a given product URL.

    Join path:
        visitor_purchase_sessions → shop_orders.line_items (where product_url matches)

    Only visitors with a confirmed purchase attribution AND whose order contained
    the given product_url in line_items are returned.

    Parameters
    ----------
    db          Active SQLAlchemy session.
    shop_domain Merchant shop domain.
    product_url Canonical product path, e.g. /products/ceramic-vase.
    days        Lookback window in days (default 30).

    Returns
    -------
    list[str]  — visitor_ids; empty list if no converted visitors found.
                 Never raises.
    """
    since = datetime.utcnow() - timedelta(days=days)
    try:
        rows = db.execute(
            text(
                """
                SELECT DISTINCT vps.visitor_id
                FROM visitor_purchase_sessions vps
                JOIN shop_orders so
                  ON so.shopify_order_id = vps.shopify_order_id
                WHERE vps.shop_domain  = :shop
                  AND vps.confirmed_at >= :since
                  AND EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(so.line_items) AS item
                      WHERE item->>'product_url' = :product_url
                  )
                """
            ),
            {"shop": shop_domain, "since": since, "product_url": product_url},
        ).fetchall()
        return [str(r[0]) for r in rows]

    except Exception as exc:
        log.error(
            "attribution.get_converted_visitors: shop=%s product=%s: %s",
            shop_domain, product_url, exc,
        )
        return []


def get_visitor_behavior_before_purchase(
    db: Session,
    shop_domain: str,
    visitor_id: str,
    shopify_order_id: str,
) -> dict[str, Any]:
    """
    Return the behavioral profile of a visitor on all product pages they
    visited before placing a specific order.

    Uses events WHERE timestamp < confirmed_at to isolate pre-purchase behavior.
    Returns behavioral metrics aggregated per product_url.

    Parameters
    ----------
    db                Active SQLAlchemy session.
    shop_domain       Merchant shop domain.
    visitor_id        The visitor's persistent UUID.
    shopify_order_id  The Shopify order ID to look up the purchase timestamp.

    Returns
    -------
    dict with keys:
        visitor_id        str
        shopify_order_id  str
        pre_purchase_views list[dict]  — one dict per product URL visited before purchase:
            {
                product_url:     str,
                visit_count:     int,
                max_scroll:      float,   # 0–100
                avg_dwell_secs:  float,
                last_visit_ms:   int,     # epoch ms of final visit before purchase
            }
        Never raises — returns empty pre_purchase_views on any error.
    """
    base = {"visitor_id": visitor_id, "shopify_order_id": shopify_order_id, "pre_purchase_views": []}
    try:
        # Resolve the confirmed_at timestamp for this attribution
        vps_row = db.execute(
            text(
                """
                SELECT confirmed_at
                FROM visitor_purchase_sessions
                WHERE shop_domain     = :shop
                  AND visitor_id      = :visitor_id
                  AND shopify_order_id = :order_id
                LIMIT 1
                """
            ),
            {"shop": shop_domain, "visitor_id": visitor_id, "order_id": shopify_order_id},
        ).fetchone()

        if not vps_row:
            return base

        confirmed_at = vps_row[0]
        # Convert to epoch ms for comparison with events.timestamp (bigint)
        confirmed_ms = int(confirmed_at.timestamp() * 1000)

        product_rows = db.execute(
            text(
                """
                SELECT
                    product_url,
                    COUNT(*)                             AS visit_count,
                    COALESCE(MAX(max_scroll_depth), 0)   AS max_scroll,
                    COALESCE(AVG(dwell_seconds), 0)      AS avg_dwell_secs,
                    MAX(timestamp)                       AS last_visit_ms
                FROM events
                WHERE shop_domain  = :shop
                  AND visitor_id   = :visitor_id
                  AND product_url  IS NOT NULL
                  AND timestamp    < :confirmed_ms
                GROUP BY product_url
                ORDER BY last_visit_ms DESC
                """
            ),
            {
                "shop":         shop_domain,
                "visitor_id":   visitor_id,
                "confirmed_ms": confirmed_ms,
            },
        ).fetchall()

        pre_purchase_views = [
            {
                "product_url":    r[0],
                "visit_count":    int(r[1]),
                "max_scroll":     round(float(r[2] or 0), 1),
                "avg_dwell_secs": round(float(r[3] or 0), 1),
                "last_visit_ms":  int(r[4] or 0),
            }
            for r in product_rows
            if r[0]   # exclude rows without product_url
        ]

        return {**base, "pre_purchase_views": pre_purchase_views}

    except Exception as exc:
        log.error(
            "attribution.get_visitor_behavior_before_purchase: visitor=%s order=%s shop=%s: %s",
            visitor_id, shopify_order_id, shop_domain, exc,
        )
        return base


def get_product_conversion_profile(
    db: Session,
    shop_domain: str,
    product_url: str,
    days: int = 30,
) -> dict[str, Any]:
    """
    Compare the behavioral profile of converting vs non-converting visitors
    for a specific product.

    This is the core empirical conversion intelligence function.  It answers:
    "What behavioral pattern separates visitors who bought from those who did not?"

    Returns two profiles: converters (visitors who purchased) and
    non-converters (visitors who viewed but did not purchase in the window).

    Parameters
    ----------
    db          Active SQLAlchemy session.
    shop_domain Merchant shop domain.
    product_url Canonical product path, e.g. /products/ceramic-vase.
    days        Lookback window in days (default 30).

    Returns
    -------
    dict with keys:
        product_url       str
        lookback_days     int
        converter_count   int   — unique visitors who purchased
        non_converter_count int — unique visitors who viewed but did not purchase
        converters        dict  — avg behavioral metrics for purchasing visitors
        non_converters    dict  — avg behavioral metrics for non-purchasing visitors
        behavioral_gap    dict  — converters minus non-converters for each metric

    Converters/non_converters dicts contain:
        avg_scroll_depth  float   (0–100)
        avg_dwell_secs    float
        avg_visit_count   float   (how many times they viewed the product page)

    Never raises — returns empty profiles on any error or insufficient data.
    """
    empty = {
        "product_url":         product_url,
        "lookback_days":       days,
        "converter_count":     0,
        "non_converter_count": 0,
        "converters":          {"avg_scroll_depth": None, "avg_dwell_secs": None, "avg_visit_count": None},
        "non_converters":      {"avg_scroll_depth": None, "avg_dwell_secs": None, "avg_visit_count": None},
        "behavioral_gap":      {"avg_scroll_depth": None, "avg_dwell_secs": None, "avg_visit_count": None},
    }

    since = datetime.utcnow() - timedelta(days=days)

    try:
        # Converter profile: visitors who purchased and had events on this product_url
        conv_rows = db.execute(
            text(
                """
                SELECT
                    e.visitor_id,
                    COALESCE(MAX(e.max_scroll_depth), 0)  AS max_scroll,
                    COALESCE(AVG(e.dwell_seconds), 0)     AS avg_dwell,
                    COUNT(*)                              AS visit_count
                FROM events e
                INNER JOIN visitor_purchase_sessions vps
                    ON vps.visitor_id  = e.visitor_id
                   AND vps.shop_domain = e.shop_domain
                WHERE e.shop_domain  = :shop
                  AND e.product_url  = :product_url
                  AND e.event_type  IN ('product_view', 'dwell_time')
                  AND vps.confirmed_at >= :since
                GROUP BY e.visitor_id
                """
            ),
            {"shop": shop_domain, "product_url": product_url, "since": since},
        ).fetchall()

        # Non-converter profile: visitors who viewed this product but did NOT purchase
        non_conv_rows = db.execute(
            text(
                """
                SELECT
                    e.visitor_id,
                    COALESCE(MAX(e.max_scroll_depth), 0)  AS max_scroll,
                    COALESCE(AVG(e.dwell_seconds), 0)     AS avg_dwell,
                    COUNT(*)                              AS visit_count
                FROM events e
                WHERE e.shop_domain  = :shop
                  AND e.product_url  = :product_url
                  AND e.event_type  IN ('product_view', 'dwell_time')
                  AND e.timestamp   >= :since_ms
                  AND e.visitor_id NOT IN (
                      SELECT vps.visitor_id
                      FROM visitor_purchase_sessions vps
                      WHERE vps.shop_domain = :shop
                        AND vps.confirmed_at >= :since
                  )
                GROUP BY e.visitor_id
                """
            ),
            {
                "shop":         shop_domain,
                "product_url":  product_url,
                "since":        since,
                "since_ms":     int(since.timestamp() * 1000),
            },
        ).fetchall()

    except Exception as exc:
        log.error(
            "attribution.get_product_conversion_profile: product=%s shop=%s: %s",
            product_url, shop_domain, exc,
        )
        return empty

    def _avg(rows: list, col: int) -> float | None:
        vals = [float(r[col] or 0) for r in rows]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _profile(rows: list) -> dict[str, Any]:
        return {
            "avg_scroll_depth": _avg(rows, 1),
            "avg_dwell_secs":   _avg(rows, 2),
            "avg_visit_count":  _avg(rows, 3),
        }

    def _gap(conv: dict, non_conv: dict) -> dict[str, Any]:
        result = {}
        for key in conv:
            c, n = conv[key], non_conv[key]
            result[key] = round(c - n, 2) if (c is not None and n is not None) else None
        return result

    conv_profile     = _profile(conv_rows)
    non_conv_profile = _profile(non_conv_rows)

    log.debug(
        "attribution.get_product_conversion_profile: shop=%s product=%s converters=%d non_converters=%d",
        shop_domain, product_url, len(conv_rows), len(non_conv_rows),
    )

    return {
        "product_url":         product_url,
        "lookback_days":       days,
        "converter_count":     len(conv_rows),
        "non_converter_count": len(non_conv_rows),
        "converters":          conv_profile,
        "non_converters":      non_conv_profile,
        "behavioral_gap":      _gap(conv_profile, non_conv_profile),
    }
