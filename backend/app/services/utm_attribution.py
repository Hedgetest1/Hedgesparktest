"""
utm_attribution.py — UTM / source-to-revenue attribution analytics.

Uses existing data in the events table (source_type, referrer) and
shop_orders + visitor_purchase_sessions to build a full attribution picture:

    Traffic source → Product views → HOT visitors → Conversions → Revenue

This is behavioral attribution: we know which source brought HOT visitors
who then converted, not just which source drove click volume.

Public interface
----------------
    get_utm_attribution(db, shop_domain, days=30) -> dict
        Returns a per-source breakdown with:
        - visitors, page_views, hot_visitors, conversions, revenue
        - cvr (conversion rate), revenue_per_visitor, quality_score

    get_utm_top_products_by_source(db, shop_domain, days=30) -> list[dict]
        Returns top product+source combinations by revenue.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Source type display names — matches spark-tracker.js source_type values
SOURCE_DISPLAY_NAMES = {
    "direct":          "Direct",
    "google":          "Google",
    "google_shopping": "Google Shopping",
    "meta":            "Meta (Facebook/Instagram)",
    "facebook":        "Facebook",
    "instagram":       "Instagram",
    "tiktok":          "TikTok",
    "twitter":         "Twitter / X",
    "pinterest":       "Pinterest",
    "email":           "Email",
    "klaviyo":         "Klaviyo",
    "sms":             "SMS",
    "referral":        "Referral",
    "organic":         "Organic",
    "paid_social":     "Paid Social",
    "paid_search":     "Paid Search",
    "unknown":         "Unknown",
}


def _source_label(source_type: str) -> str:
    return SOURCE_DISPLAY_NAMES.get(source_type.lower(), source_type.replace("_", " ").title())


def get_utm_attribution(
    db: Session,
    shop_domain: str,
    days: int = 30,
) -> dict:
    """
    Compute source-level attribution: traffic → behavior → conversions → revenue.

    Attribution model:
        A conversion is attributed to the source that brought the visitor's
        FIRST tracked event.  Last-click or multi-touch attribution requires
        additional data not currently stored — this is first-touch behavioral
        attribution (conservative, non-inflationary).

    Returns:
        {
            "window_days": int,
            "generated_at": str,
            "sources": [
                {
                    "source_type":         str,
                    "source_label":        str,
                    "visitors":            int,
                    "page_views":          int,
                    "hot_visitors":        int,
                    "conversions":         int,
                    "revenue":             float,
                    "cvr":                 float,
                    "revenue_per_visitor": float,
                    "hot_visitor_rate":    float,
                    "quality_score":       float,
                }
            ],
            "totals": {
                "visitors": int, "conversions": int, "revenue": float
            },
        }
    """
    days = max(1, min(days, 90))
    since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

    try:
        rows = db.execute(
            text("""
                WITH visitor_sources AS (
                    -- First source per visitor (first-touch attribution)
                    SELECT DISTINCT ON (shop_domain, visitor_id)
                           shop_domain,
                           visitor_id,
                           COALESCE(source_type, 'unknown') AS source_type
                    FROM events
                    WHERE shop_domain = :shop
                      AND timestamp   >= :since_ms
                      AND visitor_id  IS NOT NULL
                    ORDER BY shop_domain, visitor_id, timestamp ASC
                ),
                visitor_activity AS (
                    -- Page views and HOT scoring per visitor
                    SELECT
                        shop_domain,
                        visitor_id,
                        COUNT(*) FILTER (WHERE event_type = 'page_view') AS page_views,
                        -- HOT proxy: scroll > 60% or dwell > 45s or visit_count > 2
                        CASE WHEN
                            MAX(COALESCE(max_scroll_depth, 0)) > 60 OR
                            MAX(COALESCE(dwell_seconds, 0))    > 45 OR
                            COUNT(*) FILTER (WHERE event_type = 'product_view') > 2
                        THEN 1 ELSE 0 END AS is_hot
                    FROM events
                    WHERE shop_domain = :shop
                      AND timestamp   >= :since_ms
                      AND visitor_id  IS NOT NULL
                    GROUP BY shop_domain, visitor_id
                ),
                converted_visitors AS (
                    SELECT DISTINCT vps.visitor_id, so.total_price
                    FROM visitor_purchase_sessions vps
                    JOIN shop_orders so
                        ON so.shopify_order_id = vps.shopify_order_id
                       AND so.shop_domain      = vps.shop_domain
                    WHERE vps.shop_domain = :shop
                      AND vps.confirmed_at >= (NOW() - INTERVAL '1 second' * :days_secs)
                )
                SELECT
                    vs.source_type,
                    COUNT(DISTINCT vs.visitor_id)                          AS visitors,
                    COALESCE(SUM(va.page_views), 0)                        AS page_views,
                    COALESCE(SUM(va.is_hot), 0)                            AS hot_visitors,
                    COUNT(DISTINCT cv.visitor_id)                          AS conversions,
                    COALESCE(SUM(cv.total_price), 0)                       AS revenue
                FROM visitor_sources vs
                LEFT JOIN visitor_activity va
                    ON va.visitor_id  = vs.visitor_id
                   AND va.shop_domain = vs.shop_domain
                LEFT JOIN converted_visitors cv
                    ON cv.visitor_id = vs.visitor_id
                GROUP BY vs.source_type
                ORDER BY revenue DESC, visitors DESC
            """),
            {
                "shop":      shop_domain,
                "since_ms":  since_ms,
                "days_secs": days * 86400,
            },
        ).fetchall()

    except Exception as exc:
        log.error("utm_attribution: query failed shop=%s: %s", shop_domain, exc)
        return {
            "window_days":  days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "sources":      [],
            "totals":       {"visitors": 0, "conversions": 0, "revenue": 0.0},
        }

    sources = []
    total_visitors = 0
    total_conversions = 0
    total_revenue = 0.0

    for row in rows:
        source_type  = str(row[0] or "unknown")
        visitors     = int(row[1] or 0)
        page_views   = int(row[2] or 0)
        hot_visitors = int(row[3] or 0)
        conversions  = int(row[4] or 0)
        revenue      = float(row[5] or 0)

        cvr                  = round(conversions / visitors, 4) if visitors > 0 else 0.0
        revenue_per_visitor  = round(revenue / visitors, 2) if visitors > 0 else 0.0
        hot_visitor_rate     = round(hot_visitors / visitors, 4) if visitors > 0 else 0.0

        # Quality score: composite of CVR, hot rate, and revenue density (0–100)
        quality_score = round(
            min(100.0, (cvr * 2000) + (hot_visitor_rate * 40) + (revenue_per_visitor * 2)),
            1,
        )

        total_visitors    += visitors
        total_conversions += conversions
        total_revenue     += revenue

        sources.append({
            "source_type":         source_type,
            "source_label":        _source_label(source_type),
            "visitors":            visitors,
            "page_views":          page_views,
            "hot_visitors":        hot_visitors,
            "conversions":         conversions,
            "revenue":             round(revenue, 2),
            "cvr":                 cvr,
            "revenue_per_visitor": revenue_per_visitor,
            "hot_visitor_rate":    hot_visitor_rate,
            "quality_score":       quality_score,
        })

    return {
        "window_days":  days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sources":      sources,
        "totals": {
            "visitors":    total_visitors,
            "conversions": total_conversions,
            "revenue":     round(total_revenue, 2),
        },
    }


def get_utm_top_products_by_source(
    db: Session,
    shop_domain: str,
    days: int = 30,
) -> list[dict]:
    """
    Return top (source, product_url) combinations by visitor volume.

    Useful for understanding which traffic sources drive interest in
    which products — the behavioral attribution angle.

    Returns list of:
        {"source_type", "source_label", "product_url", "visitors", "hot_visitors", "conversions"}
    """
    days = max(1, min(days, 90))
    since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

    try:
        rows = db.execute(
            text("""
                SELECT
                    COALESCE(source_type, 'unknown') AS source_type,
                    product_url,
                    COUNT(DISTINCT visitor_id) AS visitors,
                    COUNT(DISTINCT visitor_id) FILTER (
                        WHERE max_scroll_depth > 60 OR dwell_seconds > 45
                    ) AS hot_visitors
                FROM events
                WHERE shop_domain = :shop
                  AND timestamp   >= :since_ms
                  AND product_url  IS NOT NULL
                  AND event_type  IN ('product_view', 'add_to_cart', 'dwell_time', 'scroll')
                GROUP BY source_type, product_url
                ORDER BY visitors DESC
                LIMIT 20
            """),
            {"shop": shop_domain, "since_ms": since_ms},
        ).fetchall()

        return [
            {
                "source_type":  str(r[0]),
                "source_label": _source_label(str(r[0])),
                "product_url":  str(r[1]),
                "visitors":     int(r[2] or 0),
                "hot_visitors": int(r[3] or 0),
            }
            for r in rows
        ]

    except Exception as exc:
        log.error(
            "utm_attribution: top_products query failed shop=%s: %s",
            shop_domain, exc,
        )
        return []
