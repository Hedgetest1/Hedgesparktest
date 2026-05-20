"""
product_metrics_task.py — Per-product metrics computation + upsert.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Owns:

    BATCH_SIZE                        — cycle cap on products per run
    find_active_products              — find pairs with events > watermark
    find_active_products_batch        — cursor-based paginated version
    compute_metrics                   — single CTE computing all metric columns
    compute_purchase_metrics          — purchase attribution via orders join
    upsert_metrics                    — one INSERT ... ON CONFLICT

The SQL is the same that shipped in aggregation_worker for months. The
move is byte-for-byte — any behavior change is a bug, not a feature.

The functions are also re-exported from aggregation_worker with leading
underscores for backward compatibility with the orchestrator's current
imports.
"""
from __future__ import annotations

import json as _json
import logging

from sqlalchemy import text

log = logging.getLogger("product_metrics_task")


BATCH_SIZE = 100  # products per cycle — prevents cycle overflow at scale


# ---------------------------------------------------------------------------
# Step A — find active products since watermark
# ---------------------------------------------------------------------------

def find_active_products(conn, last_watermark: int) -> list[tuple[str, str]]:
    """
    Return distinct (shop_domain, product_url) pairs that have at
    least one event with timestamp > last_watermark.

    DEPRECATED — use find_active_products_batch for cursor-based pagination.
    Kept for backward compatibility; callers should migrate.
    """
    # sql-ms-type: ok — `:watermark` bound to last_watermark (typed `int` epoch ms).
    result = conn.execute(
        text("""
            SELECT DISTINCT shop_domain, product_url
            FROM events
            WHERE product_url IS NOT NULL
              AND timestamp > :watermark
            ORDER BY shop_domain, product_url
        """),
        {"watermark": last_watermark},
    )
    return [(row.shop_domain, row.product_url) for row in result.fetchall()]


def find_active_products_batch(
    conn,
    last_watermark: int,
    cursor_shop: str | None = None,
    cursor_product: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[tuple[str, str]]:
    """Cursor-based batch fetch of active products."""
    if cursor_shop is not None and cursor_product is not None:
        # sql-ms-type: ok — `:watermark` bound to last_watermark (typed `int` epoch ms).
        result = conn.execute(
            text("""
                SELECT DISTINCT shop_domain, product_url
                FROM events
                WHERE product_url IS NOT NULL
                  AND timestamp > :watermark
                  AND (shop_domain, product_url) > (:cursor_shop, :cursor_product)
                ORDER BY shop_domain, product_url
                LIMIT :batch_size
            """),
            {
                "watermark": last_watermark,
                "cursor_shop": cursor_shop,
                "cursor_product": cursor_product,
                "batch_size": batch_size,
            },
        )
    else:
        # sql-ms-type: ok — `:watermark` bound to last_watermark (typed `int` epoch ms).
        result = conn.execute(
            text("""
                SELECT DISTINCT shop_domain, product_url
                FROM events
                WHERE product_url IS NOT NULL
                  AND timestamp > :watermark
                ORDER BY shop_domain, product_url
                LIMIT :batch_size
            """),
            {"watermark": last_watermark, "batch_size": batch_size},
        )
    return [(row.shop_domain, row.product_url) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Step B — compute all metrics for one (shop, product) pair
# ---------------------------------------------------------------------------
# Refactor 2026-05-12 (A3 close): 371-LOC god function → module-level SQL
# constant + 4 pure derive helpers + composer. SQL is atomic single-query
# CTE (NOT decomposable — splitting forces multiple round-trips); Python
# post-query logic is what gets factored.

_ZERO_PURCHASE_FIELDS = {
    "purchases_24h": 0, "purchases_7d": 0, "revenue_24h": 0.0,
    "purchases_mobile": 0, "purchases_desktop": 0,
    "purchases_paid": 0, "purchases_organic": 0, "purchases_direct": 0,
}


# sql-ms-type: ok — all `:cutoff_*` binds passed as int epoch ms (caller convention).
_PRODUCT_METRICS_SQL = text("""
    WITH product_events AS (
        SELECT
            visitor_id,
            event_type,
            timestamp,
            dwell_seconds,
            max_scroll_depth,
            device_type,
            source_type,
            utm_medium,
            DATE(to_timestamp(timestamp / 1000.0)) AS event_date
        FROM events
        WHERE shop_domain  = :shop_domain
          AND product_url  = :product_url
          AND timestamp   >= :cutoff_7d
    ),
    cart_visitors AS (
        SELECT visitor_id, MIN(timestamp) AS first_cart_at
        FROM events
        WHERE shop_domain = :shop_domain
          AND timestamp  >= :cutoff_24h
          AND (
              url        LIKE '%/cart%'
           OR url        LIKE '%/checkout%'
           OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart')
          )
        GROUP BY visitor_id
    ),
    cart_visitors_7d AS (
        SELECT visitor_id, MIN(timestamp) AS first_cart_at
        FROM events
        WHERE shop_domain = :shop_domain
          AND timestamp  >= :cutoff_7d
          AND (
              url        LIKE '%/cart%'
           OR url        LIKE '%/checkout%'
           OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart')
          )
        GROUP BY visitor_id
    ),
    return_visitors AS (
        SELECT visitor_id
        FROM product_events
        WHERE event_type IN ('page_view', 'product_view')
        GROUP BY visitor_id
        HAVING COUNT(DISTINCT event_date) >= 2
    ),
    visitor_source AS (
        SELECT DISTINCT ON (visitor_id)
            visitor_id,
            source_type,
            utm_medium
        FROM product_events
        ORDER BY visitor_id, timestamp ASC
    )
    SELECT
        COUNT(*) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
              AND timestamp >= :cutoff_1h
        )                                                   AS views_1h,

        COUNT(*) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
              AND timestamp >= :cutoff_24h
        )                                                   AS views_24h,

        COUNT(*) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
        )                                                   AS views_7d,

        COUNT(DISTINCT visitor_id) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
              AND timestamp >= :cutoff_24h
        )                                                   AS unique_visitors_24h,

        COUNT(DISTINCT visitor_id) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
        )                                                   AS unique_visitors_7d,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp  >= :cutoff_24h
              AND pe.timestamp  <  cv.first_cart_at
        )                                                   AS cart_conversions_24h,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors_7d cv ON cv.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp  <  cv.first_cart_at
        )                                                   AS cart_conversions_7d,

        (SELECT COUNT(*) FROM return_visitors)              AS return_visitor_count_7d,

        AVG(dwell_seconds) FILTER (
            WHERE event_type IN ('dwell_time', 'page_leave', 'product_view')
              AND dwell_seconds IS NOT NULL
              AND timestamp >= :cutoff_24h
        )                                                   AS avg_dwell_24h,

        AVG(max_scroll_depth) FILTER (
            WHERE event_type IN ('dwell_time', 'page_leave', 'product_view')
              AND max_scroll_depth IS NOT NULL
              AND timestamp >= :cutoff_24h
        )                                                   AS avg_scroll_24h,

        MAX(timestamp)                                      AS last_event_at,

        COUNT(*) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
              AND timestamp >= :cutoff_24h
              AND device_type = 'mobile'
        )                                                   AS views_mobile,

        COUNT(*) FILTER (
            WHERE event_type IN ('page_view', 'product_view')
              AND timestamp >= :cutoff_24h
              AND device_type = 'desktop'
        )                                                   AS views_desktop,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp  >= :cutoff_24h
              AND pe.timestamp  <  cv.first_cart_at
              AND pe.device_type = 'mobile'
        )                                                   AS carts_mobile,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp  >= :cutoff_24h
              AND pe.timestamp  <  cv.first_cart_at
              AND pe.device_type = 'desktop'
        )                                                   AS carts_desktop,

        (
            SELECT COUNT(*)
            FROM product_events pe
            INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp >= :cutoff_24h
              AND (vs.utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                      'retargeting', 'display', 'banner', 'shopping')
                   OR vs.source_type IN ('paid_search', 'paid_social', 'google_shopping'))
        )                                                   AS views_paid,

        (
            SELECT COUNT(*)
            FROM product_events pe
            INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp >= :cutoff_24h
              AND COALESCE(vs.source_type, 'unknown') IN ('direct', 'unknown')
              AND vs.utm_medium IS NULL
        )                                                   AS views_direct,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
            INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp >= :cutoff_24h
              AND pe.timestamp <  cv.first_cart_at
              AND (vs.utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                      'retargeting', 'display', 'banner', 'shopping')
                   OR vs.source_type IN ('paid_search', 'paid_social', 'google_shopping'))
        )                                                   AS carts_paid,

        (
            SELECT COUNT(DISTINCT pe.visitor_id)
            FROM product_events pe
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
            INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
            WHERE pe.event_type IN ('page_view', 'product_view')
              AND pe.timestamp >= :cutoff_24h
              AND pe.timestamp <  cv.first_cart_at
              AND COALESCE(vs.source_type, 'unknown') IN ('direct', 'unknown')
              AND vs.utm_medium IS NULL
        )                                                   AS carts_direct,

        (
            SELECT json_agg(json_build_object('blk', blk, 'v', v, 'c', c))
            FROM (
                SELECT
                    EXTRACT(HOUR FROM to_timestamp(pe2.timestamp / 1000.0))::int / 6 AS blk,
                    COUNT(*) FILTER (WHERE pe2.event_type IN ('page_view', 'product_view')) AS v,
                    COUNT(DISTINCT pe2.visitor_id) FILTER (
                        WHERE pe2.visitor_id IN (SELECT visitor_id FROM cart_visitors)
                          AND pe2.event_type IN ('page_view', 'product_view')
                    ) AS c
                FROM product_events pe2
                WHERE pe2.timestamp >= :cutoff_24h
                GROUP BY blk
            ) AS blocks
        )                                                   AS hourly_blocks,

        (
            SELECT COUNT(*)
            FROM product_events pe2
            WHERE pe2.event_type IN ('page_view', 'product_view')
              AND pe2.timestamp >= :cutoff_24h
              AND pe2.timestamp = (
                  SELECT MIN(e3.timestamp)
                  FROM events e3
                  WHERE e3.shop_domain = :shop_domain
                    AND e3.visitor_id = pe2.visitor_id
                    AND e3.timestamp >= :cutoff_24h
                    AND e3.event_type IN ('page_view', 'product_view')
              )
        )                                                   AS landing_views_24h,

        (
            SELECT COUNT(DISTINCT pe2.visitor_id)
            FROM product_events pe2
            INNER JOIN cart_visitors cv ON cv.visitor_id = pe2.visitor_id
            WHERE pe2.event_type IN ('page_view', 'product_view')
              AND pe2.timestamp >= :cutoff_24h
              AND pe2.timestamp < cv.first_cart_at
              AND pe2.timestamp = (
                  SELECT MIN(e3.timestamp)
                  FROM events e3
                  WHERE e3.shop_domain = :shop_domain
                    AND e3.visitor_id = pe2.visitor_id
                    AND e3.timestamp >= :cutoff_24h
                    AND e3.event_type IN ('page_view', 'product_view')
              )
        )                                                   AS landing_carts_24h

    FROM product_events
""")


def _cutoffs(now_ms: int) -> dict[str, int]:
    """Compute SQL cutoff timestamps for 1h / 24h / 7d windows."""
    return {
        "cutoff_1h": now_ms - 3_600_000,
        "cutoff_24h": now_ms - 86_400_000,
        "cutoff_7d": now_ms - 604_800_000,
    }


def _zero_metrics(shop_domain: str, product_url: str) -> dict:
    """All-zero baseline returned when no rows match the query window."""
    return {
        "shop_domain": shop_domain,
        "product_url": product_url,
        "views_1h": 0, "views_24h": 0, "views_7d": 0,
        "unique_visitors_24h": 0, "unique_visitors_7d": 0,
        "cart_conversions_24h": 0, "cart_conversions_7d": 0,
        "return_visitor_count_7d": 0,
        "avg_dwell_24h": None, "avg_scroll_24h": None,
        "last_event_at": None,
        "views_mobile": 0, "views_desktop": 0,
        "carts_mobile": 0, "carts_desktop": 0,
        "views_paid": 0, "views_organic": 0, "views_direct": 0,
        "carts_paid": 0, "carts_organic": 0, "carts_direct": 0,
        "peak_hour_views": 0, "peak_hour_carts": 0,
        "off_peak_hour_views": 0, "off_peak_hour_carts": 0,
        "landing_views_24h": 0, "browsing_views_24h": 0,
        "landing_carts_24h": 0, "browsing_carts_24h": 0,
        **_ZERO_PURCHASE_FIELDS,
    }


def _extract_base_counts(m: dict) -> dict:
    """Pull straightforward count/avg/timestamp columns from row mapping."""
    return {
        "views_1h": int(m["views_1h"] or 0),
        "views_24h": int(m["views_24h"] or 0),
        "views_7d": int(m["views_7d"] or 0),
        "unique_visitors_24h": int(m["unique_visitors_24h"] or 0),
        "unique_visitors_7d": int(m["unique_visitors_7d"] or 0),
        "cart_conversions_24h": int(m["cart_conversions_24h"] or 0),
        "cart_conversions_7d": int(m["cart_conversions_7d"] or 0),
        "return_visitor_count_7d": int(m["return_visitor_count_7d"] or 0),
        "avg_dwell_24h": float(m["avg_dwell_24h"]) if m["avg_dwell_24h"] is not None else None,
        "avg_scroll_24h": float(m["avg_scroll_24h"]) if m["avg_scroll_24h"] is not None else None,
        "last_event_at": int(m["last_event_at"]) if m["last_event_at"] is not None else None,
        "views_mobile": int(m["views_mobile"] or 0),
        "views_desktop": int(m["views_desktop"] or 0),
        "carts_mobile": int(m["carts_mobile"] or 0),
        "carts_desktop": int(m["carts_desktop"] or 0),
    }


def _derive_traffic_breakdown(m: dict, views_24h: int, cart_conversions_24h: int) -> dict:
    """Extract paid/direct from row; derive organic as residual (≥0)."""
    views_paid = int(m["views_paid"] or 0)
    views_direct = int(m["views_direct"] or 0)
    carts_paid = int(m["carts_paid"] or 0)
    carts_direct = int(m["carts_direct"] or 0)
    return {
        "views_paid": views_paid,
        "views_direct": views_direct,
        "views_organic": max(0, views_24h - views_paid - views_direct),
        "carts_paid": carts_paid,
        "carts_direct": carts_direct,
        "carts_organic": max(0, cart_conversions_24h - carts_paid - carts_direct),
    }


def _derive_hourly_breakdown(hourly_blocks_raw) -> dict:
    """Parse hourly_blocks JSON, identify peak 4h block, aggregate off-peak."""
    peak_hour_views = 0
    peak_hour_carts = 0
    off_peak_hour_views = 0
    off_peak_hour_carts = 0
    if hourly_blocks_raw:
        try:
            blocks = (
                hourly_blocks_raw
                if isinstance(hourly_blocks_raw, list)
                else _json.loads(hourly_blocks_raw)
            )
            if blocks:
                peak_block = max(blocks, key=lambda b: b.get("v", 0))
                peak_hour_views = int(peak_block.get("v", 0))
                peak_hour_carts = int(peak_block.get("c", 0))
                for b in blocks:
                    if b.get("blk") != peak_block.get("blk"):
                        off_peak_hour_views += int(b.get("v", 0))
                        off_peak_hour_carts += int(b.get("c", 0))
        except Exception as exc:
            log.warning("product_metrics_task: hourly blocks parse failed: %s", exc)
    return {
        "peak_hour_views": peak_hour_views,
        "peak_hour_carts": peak_hour_carts,
        "off_peak_hour_views": off_peak_hour_views,
        "off_peak_hour_carts": off_peak_hour_carts,
    }


def _derive_session_context(m: dict, views_24h: int, cart_conversions_24h: int) -> dict:
    """Split 24h views/carts into landing (first event) vs browsing (residual)."""
    landing_views = int(m.get("landing_views_24h") or 0)
    landing_carts = int(m.get("landing_carts_24h") or 0)
    return {
        "landing_views_24h": landing_views,
        "browsing_views_24h": max(0, views_24h - landing_views),
        "landing_carts_24h": landing_carts,
        "browsing_carts_24h": max(0, cart_conversions_24h - landing_carts),
    }


def compute_metrics(conn, shop_domain: str, product_url: str, now_ms: int) -> dict:
    """
    Run the atomic CTE query that computes all metric columns for the
    given (shop_domain, product_url) pair, then dispatch to 4 derive
    helpers for paid/organic split, hourly peak detection, and
    landing-vs-browsing partition. Merges in purchase attribution from
    `compute_purchase_metrics` and returns a flat dict ready for upsert.

    Pure — owns no mutable state, only reads events + shop_orders.

    Refactored 2026-05-12 (A3 close): 371-LOC god function → 25-LOC
    composer + module-level SQL constant + 4 pure derive helpers.
    Identical contract (signature, return shape, field semantics).
    """
    cutoffs = _cutoffs(now_ms)
    row = conn.execute(
        _PRODUCT_METRICS_SQL,
        {"shop_domain": shop_domain, "product_url": product_url, **cutoffs},
    ).fetchone()

    if row is None:
        return _zero_metrics(shop_domain, product_url)

    m = dict(row._mapping)
    base = _extract_base_counts(m)
    traffic = _derive_traffic_breakdown(m, base["views_24h"], base["cart_conversions_24h"])
    hourly = _derive_hourly_breakdown(m.get("hourly_blocks"))
    session = _derive_session_context(m, base["views_24h"], base["cart_conversions_24h"])

    result = {
        "shop_domain": shop_domain,
        "product_url": product_url,
        **base,
        **traffic,
        **hourly,
        **session,
        **_ZERO_PURCHASE_FIELDS,
    }
    result.update(compute_purchase_metrics(
        conn, shop_domain, product_url, cutoffs["cutoff_24h"], cutoffs["cutoff_7d"]
    ))
    return result


def compute_purchase_metrics(
    conn,
    shop_domain: str,
    product_url: str,
    cutoff_24h: int,
    cutoff_7d: int,
) -> dict:
    """
    Compute purchase-level attribution by joining:
    visitor_purchase_sessions → shop_orders (line_items JSONB) → events.
    """
    _ZERO = {
        "purchases_24h": 0, "purchases_7d": 0, "revenue_24h": 0.0,
        "purchases_mobile": 0, "purchases_desktop": 0,
        "purchases_paid": 0, "purchases_organic": 0, "purchases_direct": 0,
    }

    pid_result = conn.execute(
        text("""
            SELECT DISTINCT product_id
            FROM events
            WHERE shop_domain  = :shop_domain
              AND product_url  = :product_url
              AND product_id  IS NOT NULL
            LIMIT 10
        """),
        {"shop_domain": shop_domain, "product_url": product_url},
    )
    product_ids = [r[0] for r in pid_result.fetchall()]
    pid_array = product_ids if product_ids else ["__none__"]

    result = conn.execute(
        text("""
            WITH matched_orders AS (
                SELECT
                    vps.visitor_id,
                    vps.shopify_order_id,
                    EXTRACT(EPOCH FROM so.created_at) * 1000 AS order_ms,
                    EXTRACT(EPOCH FROM vps.confirmed_at) * 1000 AS confirmed_ms,
                    (
                        SELECT COALESCE(
                            SUM((li->>'price')::numeric * GREATEST((li->>'quantity')::int, 1)),
                            0
                        )
                        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS li
                        WHERE li->>'product_url' = :product_url
                           OR (li->>'product_url' IS NULL AND li->>'product_id' = ANY(:product_ids))
                    ) AS line_revenue
                FROM visitor_purchase_sessions vps
                INNER JOIN shop_orders so
                    ON so.shopify_order_id = vps.shopify_order_id
                WHERE vps.shop_domain = :shop_domain
                  AND so.shop_domain  = :shop_domain
                  AND EXTRACT(EPOCH FROM so.created_at) * 1000 >= :cutoff_7d
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                          WHERE item->>'product_url' = :product_url
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                          WHERE item->>'product_url' IS NULL
                            AND item->>'product_id' = ANY(:product_ids)
                      )
                  )
            ),
            purchaser_attrs AS (
                SELECT DISTINCT ON (mo.visitor_id, mo.shopify_order_id)
                    mo.visitor_id,
                    mo.shopify_order_id,
                    mo.line_revenue,
                    mo.order_ms,
                    e.device_type,
                    e.source_type,
                    e.utm_medium
                FROM matched_orders mo
                INNER JOIN events e
                    ON e.visitor_id  = mo.visitor_id
                   AND e.shop_domain = :shop_domain
                   AND e.timestamp   <= mo.confirmed_ms
                ORDER BY mo.visitor_id, mo.shopify_order_id, e.timestamp DESC
            )
            SELECT
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h)     AS purchases_24h,
                COUNT(*)                                            AS purchases_7d,
                COALESCE(SUM(line_revenue) FILTER (WHERE order_ms >= :cutoff_24h), 0) AS revenue_24h,
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h AND device_type = 'mobile')  AS purchases_mobile,
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h AND device_type = 'desktop') AS purchases_desktop,
                COUNT(*) FILTER (
                    WHERE order_ms >= :cutoff_24h
                      AND (utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                           'retargeting', 'display', 'banner', 'shopping')
                           OR source_type IN ('paid_search', 'paid_social', 'google_shopping'))
                )                                                   AS purchases_paid,
                COUNT(*) FILTER (
                    WHERE order_ms >= :cutoff_24h
                      AND COALESCE(source_type, 'unknown') IN ('direct', 'unknown')
                      AND utm_medium IS NULL
                )                                                   AS purchases_direct
            FROM purchaser_attrs
        """),
        {
            "shop_domain": shop_domain,
            "product_url": product_url,
            "product_ids": pid_array,
            "cutoff_24h": cutoff_24h,
            "cutoff_7d": cutoff_7d,
        },
    )
    row = result.fetchone()
    if row is None:
        return _ZERO

    pm = dict(row._mapping)
    p24 = int(pm["purchases_24h"] or 0)
    p_paid = int(pm["purchases_paid"] or 0)
    p_direct = int(pm["purchases_direct"] or 0)
    p_organic = max(0, p24 - p_paid - p_direct)

    return {
        "purchases_24h": p24,
        "purchases_7d": int(pm["purchases_7d"] or 0),
        "revenue_24h": round(float(pm["revenue_24h"] or 0), 2),
        "purchases_mobile": int(pm["purchases_mobile"] or 0),
        "purchases_desktop": int(pm["purchases_desktop"] or 0),
        "purchases_paid": p_paid,
        "purchases_organic": p_organic,
        "purchases_direct": p_direct,
    }


# ---------------------------------------------------------------------------
# Step C — upsert one metrics row
# ---------------------------------------------------------------------------

def upsert_metrics(conn, metrics: dict) -> None:
    """INSERT ... ON CONFLICT update of product_metrics for one pair."""
    conn.execute(
        text("""
            INSERT INTO product_metrics (
                shop_domain, product_url,
                views_1h, views_24h, views_7d,
                unique_visitors_24h, unique_visitors_7d,
                cart_conversions_24h, cart_conversions_7d,
                return_visitor_count_7d,
                avg_dwell_24h, avg_scroll_24h, last_event_at,
                views_mobile, views_desktop, carts_mobile, carts_desktop,
                views_paid, views_organic, views_direct,
                carts_paid, carts_organic, carts_direct,
                purchases_24h, purchases_7d, revenue_24h,
                purchases_mobile, purchases_desktop,
                purchases_paid, purchases_organic, purchases_direct,
                peak_hour_views, peak_hour_carts,
                off_peak_hour_views, off_peak_hour_carts,
                landing_views_24h, browsing_views_24h,
                landing_carts_24h, browsing_carts_24h,
                updated_at
            ) VALUES (
                :shop_domain, :product_url,
                :views_1h, :views_24h, :views_7d,
                :unique_visitors_24h, :unique_visitors_7d,
                :cart_conversions_24h, :cart_conversions_7d,
                :return_visitor_count_7d,
                :avg_dwell_24h, :avg_scroll_24h, :last_event_at,
                :views_mobile, :views_desktop, :carts_mobile, :carts_desktop,
                :views_paid, :views_organic, :views_direct,
                :carts_paid, :carts_organic, :carts_direct,
                :purchases_24h, :purchases_7d, :revenue_24h,
                :purchases_mobile, :purchases_desktop,
                :purchases_paid, :purchases_organic, :purchases_direct,
                :peak_hour_views, :peak_hour_carts,
                :off_peak_hour_views, :off_peak_hour_carts,
                :landing_views_24h, :browsing_views_24h,
                :landing_carts_24h, :browsing_carts_24h,
                now()
            )
            ON CONFLICT (shop_domain, product_url) DO UPDATE SET
                views_1h                = EXCLUDED.views_1h,
                views_24h               = EXCLUDED.views_24h,
                views_7d                = EXCLUDED.views_7d,
                unique_visitors_24h     = EXCLUDED.unique_visitors_24h,
                unique_visitors_7d      = EXCLUDED.unique_visitors_7d,
                cart_conversions_24h    = EXCLUDED.cart_conversions_24h,
                cart_conversions_7d     = EXCLUDED.cart_conversions_7d,
                return_visitor_count_7d = EXCLUDED.return_visitor_count_7d,
                avg_dwell_24h           = EXCLUDED.avg_dwell_24h,
                avg_scroll_24h          = EXCLUDED.avg_scroll_24h,
                last_event_at           = EXCLUDED.last_event_at,
                views_mobile            = EXCLUDED.views_mobile,
                views_desktop           = EXCLUDED.views_desktop,
                carts_mobile            = EXCLUDED.carts_mobile,
                carts_desktop           = EXCLUDED.carts_desktop,
                views_paid              = EXCLUDED.views_paid,
                views_organic           = EXCLUDED.views_organic,
                views_direct            = EXCLUDED.views_direct,
                carts_paid              = EXCLUDED.carts_paid,
                carts_organic           = EXCLUDED.carts_organic,
                carts_direct            = EXCLUDED.carts_direct,
                purchases_24h           = EXCLUDED.purchases_24h,
                purchases_7d            = EXCLUDED.purchases_7d,
                revenue_24h             = EXCLUDED.revenue_24h,
                purchases_mobile        = EXCLUDED.purchases_mobile,
                purchases_desktop       = EXCLUDED.purchases_desktop,
                purchases_paid          = EXCLUDED.purchases_paid,
                purchases_organic       = EXCLUDED.purchases_organic,
                purchases_direct        = EXCLUDED.purchases_direct,
                peak_hour_views         = EXCLUDED.peak_hour_views,
                peak_hour_carts         = EXCLUDED.peak_hour_carts,
                off_peak_hour_views     = EXCLUDED.off_peak_hour_views,
                off_peak_hour_carts     = EXCLUDED.off_peak_hour_carts,
                landing_views_24h       = EXCLUDED.landing_views_24h,
                browsing_views_24h      = EXCLUDED.browsing_views_24h,
                landing_carts_24h       = EXCLUDED.landing_carts_24h,
                browsing_carts_24h      = EXCLUDED.browsing_carts_24h,
                updated_at              = now()
        """),
        metrics,
    )
