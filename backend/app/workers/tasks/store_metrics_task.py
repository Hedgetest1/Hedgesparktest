"""
store_metrics_task.py — Per-shop store_metrics computation + upsert.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Owns:

    compute_store_metrics(conn, shop_domain) -> dict
    upsert_store_metrics(conn, metrics) -> None

Computes two pieces of store intelligence per shop:
  1. Co-viewed product pairs (top 15 products, shared visitors)
  2. Cohort snapshot (new vs returning visitors, 7d window)

The orchestrator passes a `log` callback so task output lands in the
same structured worker log as the main loop.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

_log = logging.getLogger("worker.aggregation.store_metrics")


def compute_store_metrics(conn, shop_domain: str) -> dict:
    """
    Compute store-level intelligence for one shop:
    1. Co-viewed product pairs (bounded: top 15 products, >= 3 shared visitors, top 10 pairs)
    2. Cohort snapshot (new vs returning visitors, 7d window)

    Both queries use existing indexes. Total cost is bounded and predictable.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_7d = now_ms - 604_800_000

    co_viewed = []
    try:
        top_result = conn.execute(
            text("""
                SELECT product_url, views_7d
                FROM product_metrics
                WHERE shop_domain = :shop AND views_7d > 0
                ORDER BY views_7d DESC
                LIMIT 15
            """),
            {"shop": shop_domain},
        )
        top_rows = top_result.fetchall()
        top_urls = [r[0] for r in top_rows]
        view_map = {r[0]: int(r[1] or 0) for r in top_rows}

        if len(top_urls) >= 2:
            pair_result = conn.execute(
                text("""
                    WITH visitor_products AS (
                        SELECT DISTINCT visitor_id, product_url
                        FROM events
                        WHERE shop_domain = :shop
                          AND product_url = ANY(:urls)
                          AND event_type IN ('page_view', 'product_view')
                          AND timestamp >= :cutoff_7d
                    ),
                    pairs AS (
                        SELECT
                            a.product_url AS product_a,
                            b.product_url AS product_b,
                            COUNT(DISTINCT a.visitor_id) AS shared_visitors
                        FROM visitor_products a
                        INNER JOIN visitor_products b
                            ON a.visitor_id = b.visitor_id
                           AND a.product_url < b.product_url
                        GROUP BY a.product_url, b.product_url
                        HAVING COUNT(DISTINCT a.visitor_id) >= 3
                        ORDER BY shared_visitors DESC
                        LIMIT 10
                    )
                    SELECT * FROM pairs
                """),
                {"shop": shop_domain, "urls": top_urls, "cutoff_7d": cutoff_7d},
            )
            for r in pair_result.fetchall():
                co_viewed.append({
                    "product_a": r[0],
                    "product_b": r[1],
                    "shared_visitors": int(r[2]),
                    "a_views": view_map.get(r[0], 0),
                    "b_views": view_map.get(r[1], 0),
                })
    except Exception as exc:
        _log.warning("store_metrics co_viewed error for %s (non-fatal): %s", shop_domain, exc)

    new_v, new_c, ret_v, ret_c = 0, 0, 0, 0
    try:
        cohort_result = conn.execute(
            text("""
                WITH visitor_status AS (
                    SELECT
                        v.visitor_id,
                        CASE WHEN v.first_seen >= NOW() - INTERVAL '7 days'
                             THEN 'new' ELSE 'returning' END AS cohort
                    FROM visitors v
                    WHERE v.shop_domain = :shop
                      AND v.last_seen >= NOW() - INTERVAL '7 days'
                ),
                visitor_carts AS (
                    SELECT DISTINCT visitor_id
                    FROM events
                    WHERE shop_domain = :shop
                      AND timestamp >= :cutoff_7d
                      AND (url LIKE '%%/cart%%' OR url LIKE '%%/checkout%%'
                           OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart'))
                )
                SELECT
                    vs.cohort,
                    COUNT(DISTINCT vs.visitor_id) AS visitors,
                    COUNT(DISTINCT vc.visitor_id) AS carters
                FROM visitor_status vs
                LEFT JOIN visitor_carts vc ON vc.visitor_id = vs.visitor_id
                GROUP BY vs.cohort
            """),
            {"shop": shop_domain, "cutoff_7d": cutoff_7d},
        )
        for r in cohort_result.fetchall():
            if r[0] == "new":
                new_v, new_c = int(r[1]), int(r[2])
            elif r[0] == "returning":
                ret_v, ret_c = int(r[1]), int(r[2])
    except Exception as exc:
        _log.warning("store_metrics cohort error for %s (non-fatal): %s", shop_domain, exc)

    return {
        "shop_domain": shop_domain,
        "co_viewed_pairs": co_viewed,
        "new_visitors_7d": new_v,
        "returning_visitors_7d": ret_v,
        "new_visitor_cart_rate": round(new_c / new_v, 4) if new_v > 0 else None,
        "returning_visitor_cart_rate": round(ret_c / ret_v, 4) if ret_v > 0 else None,
    }


def upsert_store_metrics(conn, metrics: dict) -> None:
    """Upsert one store_metrics row. Execution opportunities are in their own table."""
    conn.execute(
        text("""
            INSERT INTO store_metrics (
                shop_domain, co_viewed_pairs,
                new_visitors_7d, returning_visitors_7d,
                new_visitor_cart_rate, returning_visitor_cart_rate,
                updated_at
            ) VALUES (
                :shop_domain, CAST(:co_viewed_pairs AS jsonb),
                :new_visitors_7d, :returning_visitors_7d,
                :new_visitor_cart_rate, :returning_visitor_cart_rate,
                now()
            )
            ON CONFLICT (shop_domain) DO UPDATE SET
                co_viewed_pairs            = CAST(:co_viewed_pairs AS jsonb),
                new_visitors_7d            = EXCLUDED.new_visitors_7d,
                returning_visitors_7d      = EXCLUDED.returning_visitors_7d,
                new_visitor_cart_rate       = EXCLUDED.new_visitor_cart_rate,
                returning_visitor_cart_rate = EXCLUDED.returning_visitor_cart_rate,
                updated_at                 = now()
        """),
        {
            **{k: v for k, v in metrics.items() if k != "co_viewed_pairs"},
            "co_viewed_pairs": _json.dumps(metrics.get("co_viewed_pairs", [])),
        },
    )
