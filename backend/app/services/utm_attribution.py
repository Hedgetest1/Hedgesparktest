"""
utm_attribution.py — UTM / source-to-revenue attribution analytics.

Uses existing data in the events table (source_type, referrer, utm_*) and
shop_orders + visitor_purchase_sessions to build a full attribution picture:

    Traffic source → Product views → HOT visitors → Conversions → Revenue

Supports both first-touch and last-touch attribution models via the
first_source / last_source columns on visitor_purchase_sessions.

Public interface
----------------
    get_utm_attribution(db, shop_domain, days=30, model="first_touch") -> dict
        Returns a per-source breakdown with:
        - visitors, page_views, hot_visitors, conversions, revenue
        - cvr (conversion rate), revenue_per_visitor, quality_score

    get_utm_top_products_by_source(db, shop_domain, days=30) -> list[dict]
        Returns top product+source combinations by revenue.

    get_attribution_summary(db, shop_domain, days=30) -> dict
        Returns attribution overview with attributed/unattributed order counts,
        top sources, top campaigns, first-touch vs last-touch breakdown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

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
    model: str = "first_touch",
) -> dict:
    """
    Compute source-level attribution: traffic → behavior → conversions → revenue.

    Attribution models:
        "first_touch" — conversion attributed to source of visitor's FIRST event (default)
        "last_touch" — conversion attributed to source of visitor's LAST event before purchase

    Returns:
        {
            "window_days": int,
            "model": str,
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
    since_ms = int((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).timestamp() * 1000)
    currency = get_shop_currency(db, shop_domain)

    # Choose source ordering: ASC for first-touch, DESC for last-touch
    source_order = "ASC" if model == "first_touch" else "DESC"

    try:
        # elite-hardening-allowed: "ASC"/"DESC" from ternary on whitelisted `model` value (no user input enters the SQL)
        rows = db.execute(
            text(f"""
                WITH visitor_sources AS (
                    -- Source per visitor (first-touch or last-touch)
                    SELECT DISTINCT ON (shop_domain, visitor_id)
                           shop_domain,
                           visitor_id,
                           COALESCE(source_type, 'unknown') AS source_type
                    FROM events
                    WHERE shop_domain = :shop
                      AND timestamp   >= :since_ms
                      AND visitor_id  IS NOT NULL
                    ORDER BY shop_domain, visitor_id, timestamp {source_order}
                ),
                visitor_activity AS (
                    -- Page views and HOT scoring per visitor
                    SELECT
                        shop_domain,
                        visitor_id,
                        COUNT(*) FILTER (WHERE event_type = 'page_view') AS page_views,
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
                      AND (:currency IS NULL OR so.currency = :currency)
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
                "currency":  currency,
            },
        ).fetchall()

    except Exception as exc:
        log.error("utm_attribution: query failed shop=%s: %s", shop_domain, exc)
        return {
            "window_days":  days,
            "model":        model,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
        "model":        model,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
    since_ms = int((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).timestamp() * 1000)

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


def get_attribution_summary(
    db: Session,
    shop_domain: str,
    days: int = 30,
) -> dict:
    """
    Return attribution overview: attributed vs unattributed orders,
    top sources, top campaigns, first-touch vs last-touch breakdown.

    This is the evidence-based attribution summary for merchants.
    Every number is backed by real data — no modeled/probabilistic attribution.

    Returns:
        {
            "window_days": int,
            "generated_at": str,
            "orders_total": int,
            "orders_attributed": int,
            "orders_unattributed": int,
            "attribution_rate": float,
            "top_sources_first_touch": [...],
            "top_sources_last_touch": [...],
            "top_campaigns": [...],
            "first_vs_last_match_rate": float,
        }
    """
    days = max(1, min(days, 90))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    currency = get_shop_currency(db, shop_domain)
    result = {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "orders_total": 0,
        "orders_attributed": 0,
        "orders_unattributed": 0,
        "attribution_rate": 0.0,
        "top_sources_first_touch": [],
        "top_sources_last_touch": [],
        "top_campaigns": [],
        "first_vs_last_match_rate": 0.0,
    }

    try:
        # Total orders in window
        total_row = db.execute(text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :cutoff
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchone()
        result["orders_total"] = total_row[0] if total_row else 0

        # Attributed orders (have a visitor_purchase_session with first_source)
        attr_row = db.execute(text("""
            SELECT COUNT(DISTINCT vps.shopify_order_id)
            FROM visitor_purchase_sessions vps
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
              AND vps.first_source IS NOT NULL
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchone()
        result["orders_attributed"] = attr_row[0] if attr_row else 0

        # Orders with VPS but no source (visitor tracked but no source_type on events)
        partial_row = db.execute(text("""
            SELECT COUNT(DISTINCT vps.shopify_order_id)
            FROM visitor_purchase_sessions vps
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
              AND vps.first_source IS NULL
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchone()
        partial = partial_row[0] if partial_row else 0

        result["orders_unattributed"] = result["orders_total"] - result["orders_attributed"] - partial
        if result["orders_unattributed"] < 0:
            result["orders_unattributed"] = result["orders_total"] - result["orders_attributed"]

        if result["orders_total"] > 0:
            result["attribution_rate"] = round(result["orders_attributed"] / result["orders_total"], 3)

        # Top sources by first-touch
        ft_rows = db.execute(text("""
            SELECT vps.first_source, COUNT(*) AS cnt,
                   COALESCE(SUM(so.total_price), 0) AS revenue
            FROM visitor_purchase_sessions vps
            JOIN shop_orders so ON so.shopify_order_id = vps.shopify_order_id
                               AND so.shop_domain = vps.shop_domain
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
              AND vps.first_source IS NOT NULL
              AND (:currency IS NULL OR so.currency = :currency)
            GROUP BY vps.first_source
            ORDER BY cnt DESC
            LIMIT 10
        """), {"shop": shop_domain, "cutoff": cutoff, "currency": currency}).fetchall()
        result["top_sources_first_touch"] = [
            {"source": r[0], "label": _source_label(r[0]), "orders": r[1], "revenue": round(float(r[2]), 2)}
            for r in ft_rows
        ]

        # Top sources by last-touch
        lt_rows = db.execute(text("""
            SELECT vps.last_source, COUNT(*) AS cnt,
                   COALESCE(SUM(so.total_price), 0) AS revenue
            FROM visitor_purchase_sessions vps
            JOIN shop_orders so ON so.shopify_order_id = vps.shopify_order_id
                               AND so.shop_domain = vps.shop_domain
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
              AND vps.last_source IS NOT NULL
              AND (:currency IS NULL OR so.currency = :currency)
            GROUP BY vps.last_source
            ORDER BY cnt DESC
            LIMIT 10
        """), {"shop": shop_domain, "cutoff": cutoff, "currency": currency}).fetchall()
        result["top_sources_last_touch"] = [
            {"source": r[0], "label": _source_label(r[0]), "orders": r[1], "revenue": round(float(r[2]), 2)}
            for r in lt_rows
        ]

        # Top campaigns (from first_campaign — most actionable for merchants)
        camp_rows = db.execute(text("""
            SELECT vps.first_campaign, COUNT(*) AS cnt,
                   COALESCE(SUM(so.total_price), 0) AS revenue
            FROM visitor_purchase_sessions vps
            JOIN shop_orders so ON so.shopify_order_id = vps.shopify_order_id
                               AND so.shop_domain = vps.shop_domain
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
              AND vps.first_campaign IS NOT NULL
              AND (:currency IS NULL OR so.currency = :currency)
            GROUP BY vps.first_campaign
            ORDER BY revenue DESC
            LIMIT 10
        """), {"shop": shop_domain, "cutoff": cutoff, "currency": currency}).fetchall()
        result["top_campaigns"] = [
            {"campaign": r[0], "orders": r[1], "revenue": round(float(r[2]), 2)}
            for r in camp_rows
        ]

        # First vs last touch match rate: how often do they agree?
        match_row = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE first_source = last_source) AS matched,
                COUNT(*) AS total
            FROM visitor_purchase_sessions
            WHERE shop_domain = :shop
              AND confirmed_at >= :cutoff
              AND first_source IS NOT NULL
              AND last_source IS NOT NULL
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchone()
        if match_row and match_row[1] > 0:
            result["first_vs_last_match_rate"] = round(match_row[0] / match_row[1], 3)

    except Exception as exc:
        log.error("attribution_summary: query failed shop=%s: %s", shop_domain, exc)

    return result
