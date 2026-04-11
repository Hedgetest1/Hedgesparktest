from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session
from app.services.external_lookup_service import infer_external_lookup

log = logging.getLogger("dashboard_api")

SANDBOX_PATH = Path("/opt/wishspark/sandbox")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except Exception:
        return {}


def _safe_number(value: Any, default: int | float = 0) -> int | float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        if "." in str(value):
            return float(value)
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y", "on"}:
        return True
    if lowered in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _rows(query: str, db: Session, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Safe row list fetcher — returns [] on SQL error (intentional soft-fail
    behavior for dashboard widgets that should never break the whole page).
    Errors are logged so silent failures are observable in logs + Sentry.
    """
    try:
        result = db.execute(text(query), params or {})
        return [_to_dict(row) for row in result.fetchall()]
    except Exception as exc:
        log.warning(
            "dashboard._rows: SQL failed (%s): %s",
            type(exc).__name__, str(exc)[:200],
        )
        return []


def _row(query: str, db: Session, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Safe single-row fetcher — returns {} on SQL error. Same soft-fail contract
    as _rows(); errors logged so they're not invisible.
    """
    try:
        result = db.execute(text(query), params or {})
        row = result.fetchone()
        return _to_dict(row) if row else {}
    except Exception as exc:
        log.warning(
            "dashboard._row: SQL failed (%s): %s",
            type(exc).__name__, str(exc)[:200],
        )
        return {}


def _table_exists(db: Session, table_name: str) -> bool:
    result = _row(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = :table_name
        ) AS exists
        """,
        db,
        {"table_name": table_name},
    )
    return _safe_bool(result.get("exists"), False)


def _columns(db: Session, table_name: str) -> set[str]:
    if not _table_exists(db, table_name):
        return set()
    rows = _rows(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
        """,
        db,
        {"table_name": table_name},
    )
    return {str(row.get("column_name")) for row in rows if row.get("column_name")}


def _pick(cols: set[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in cols:
            return candidate
    return None


def _sql_value(column_name: str | None, alias: str, default_sql: str = "NULL") -> str:
    if column_name:
        return f"{column_name} AS {alias}"
    return f"{default_sql} AS {alias}"


# ---------------------------------------------------------------------------
# Dashboard section builders (each scoped to a single shop_domain)
# ---------------------------------------------------------------------------

def _build_summary(db: Session, shop_domain: str) -> dict[str, Any]:
    """
    Build the KPI summary for a shop.

    Time windows:
    - total_visitors_24h / total_events_24h — last 24 hours (truthful recency)
    - total_visitors_all / total_events_all — all-time (labeled separately)
    - hot/warm/cold — COUNT DISTINCT visitor_id (unique people, not pairs)

    Removed:
    - total_sessions — events table has no session_id column; was always 0
    """
    p = {"shop_domain": shop_domain}

    total_visitors_24h = 0
    total_visitors_all = 0
    total_events_24h = 0
    total_events_all = 0
    hot_visitors = 0
    warm_visitors = 0
    cold_visitors = 0
    wishlist_adds = 0
    avg_intent_score = 0
    conversion_ready_products = 0

    if _table_exists(db, "events"):
        # 24h window: epoch ms cutoff
        result_24h = _row(
            """
            SELECT
                COUNT(DISTINCT visitor_id)  AS visitors,
                COUNT(*)                    AS events
            FROM events
            WHERE shop_domain = :shop_domain
              AND timestamp > (EXTRACT(EPOCH FROM NOW()) * 1000 - 86400000)
            """,
            db, p,
        )
        total_visitors_24h = int(_safe_number(result_24h.get("visitors"), 0))
        total_events_24h = int(_safe_number(result_24h.get("events"), 0))

        # All-time (for context)
        result_all = _row(
            """
            SELECT
                COUNT(DISTINCT visitor_id)  AS visitors,
                COUNT(*)                    AS events,
                COALESCE(SUM(CASE WHEN event_type = 'wishlist_add' THEN 1 ELSE 0 END), 0) AS wishlist
            FROM events
            WHERE shop_domain = :shop_domain
            """,
            db, p,
        )
        total_visitors_all = int(_safe_number(result_all.get("visitors"), 0))
        total_events_all = int(_safe_number(result_all.get("events"), 0))
        wishlist_adds = int(_safe_number(result_all.get("wishlist"), 0))

    # Hot/warm/cold: COUNT DISTINCT visitor_id (unique people, not pairs)
    if _table_exists(db, "visitor_product_state"):
        cols = _columns(db, "visitor_product_state")
        intent_level_col = _pick(cols, "intent_level")
        intent_score_col = _pick(cols, "intent_score")

        if intent_level_col:
            counts = _row(
                f"""
                SELECT
                    COUNT(DISTINCT CASE WHEN UPPER({intent_level_col}) = 'HOT'  THEN visitor_id END) AS hot,
                    COUNT(DISTINCT CASE WHEN UPPER({intent_level_col}) = 'WARM' THEN visitor_id END) AS warm,
                    COUNT(DISTINCT CASE WHEN UPPER({intent_level_col}) = 'COLD' THEN visitor_id END) AS cold
                FROM visitor_product_state
                WHERE shop_domain = :shop_domain
                """,
                db, p,
            )
            hot_visitors = int(_safe_number(counts.get("hot"), 0))
            warm_visitors = int(_safe_number(counts.get("warm"), 0))
            cold_visitors = int(_safe_number(counts.get("cold"), 0))

        if intent_score_col:
            avg_intent_score = _safe_number(
                _row(
                    f"""
                    SELECT COALESCE(ROUND(AVG({intent_score_col}), 2), 0) AS value
                    FROM visitor_product_state
                    WHERE shop_domain = :shop_domain
                    """,
                    db, p,
                ).get("value"),
                0,
            )

    if _table_exists(db, "product_opportunities"):
        opp_cols = _columns(db, "product_opportunities")
        product_key_col = _pick(opp_cols, "product_id", "product_url", "product_name", "name")
        if product_key_col:
            conversion_ready_products = int(
                _safe_number(
                    _row(
                        f"""
                        SELECT COUNT(DISTINCT {product_key_col}) AS value
                        FROM product_opportunities
                        WHERE shop_domain = :shop_domain
                        """,
                        db, p,
                    ).get("value"),
                    0,
                )
            )

    return {
        "total_visitors": total_visitors_24h,
        "total_visitors_24h": total_visitors_24h,
        "total_visitors_all": total_visitors_all,
        "total_events": total_events_24h,
        "total_events_24h": total_events_24h,
        "total_events_all": total_events_all,
        "hot_visitors": hot_visitors,
        "warm_visitors": warm_visitors,
        "cold_visitors": cold_visitors,
        "wishlist_adds": wishlist_adds,
        "avg_intent_score": avg_intent_score,
        "conversion_ready_products": conversion_ready_products,
        "visitor_metric_note": "hot/warm/cold counts are unique visitors with any product in that intent tier",
    }


def _build_top_hot_visitors(db: Session, shop_domain: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "visitor_product_state"):
        return []

    cols = _columns(db, "visitor_product_state")
    visitor_col = _pick(cols, "visitor_id")
    session_col = _pick(cols, "session_id")
    product_col = _pick(cols, "product_id", "product_url")
    views_col = _pick(cols, "total_views", "records")
    dwell_col = _pick(cols, "total_dwell_seconds", "avg_dwell_seconds")
    scroll_col = _pick(cols, "max_scroll_depth", "avg_scroll_depth")
    wishlist_col = _pick(cols, "wishlist_added")
    intent_score_col = _pick(cols, "intent_score")
    intent_level_col = _pick(cols, "intent_level")

    if not visitor_col or not intent_score_col:
        return []

    if intent_level_col:
        where_clause = f"WHERE UPPER(COALESCE({intent_level_col}, '')) = 'HOT' AND shop_domain = :shop_domain"
    else:
        where_clause = f"WHERE {intent_score_col} >= 80 AND shop_domain = :shop_domain"

    rows = _rows(
        f"""
        SELECT
            {_sql_value(visitor_col, "visitor_id", "'unknown'")},
            {_sql_value(session_col, "session_id")},
            {_sql_value(product_col, "product_id")},
            {_sql_value(views_col, "total_views", "0")},
            {_sql_value(dwell_col, "total_dwell_seconds", "0")},
            {_sql_value(scroll_col, "max_scroll_depth", "0")},
            {_sql_value(wishlist_col, "wishlist_added", "FALSE")},
            {_sql_value(intent_score_col, "intent_score", "0")},
            {_sql_value(intent_level_col, "intent_level", "'HOT'")}
        FROM visitor_product_state
        {where_clause}
        ORDER BY COALESCE({intent_score_col}, 0) DESC
        LIMIT 10
        """,
        db,
        {"shop_domain": shop_domain},
    )

    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "visitor_id": row.get("visitor_id"),
                "session_id": row.get("session_id"),
                "product_id": row.get("product_id"),
                "total_views": int(_safe_number(row.get("total_views"), 0)),
                "total_dwell_seconds": float(_safe_number(row.get("total_dwell_seconds"), 0)),
                "max_scroll_depth": float(_safe_number(row.get("max_scroll_depth"), 0)),
                "wishlist_added": _safe_bool(row.get("wishlist_added"), False),
                "intent_score": float(_safe_number(row.get("intent_score"), 0)),
                "intent_level": row.get("intent_level") or "HOT",
            }
        )
    return cleaned


def _build_top_products(db: Session, shop_domain: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "visitor_product_state"):
        return []

    cols = _columns(db, "visitor_product_state")
    product_col = _pick(cols, "product_id", "product_url")
    visitor_col = _pick(cols, "visitor_id")
    views_col = _pick(cols, "total_views", "records")
    wishlist_col = _pick(cols, "wishlist_added")
    intent_score_col = _pick(cols, "intent_score")

    if not product_col:
        return []

    unique_visitors_sql = f"COUNT(DISTINCT {visitor_col})" if visitor_col else "0"
    total_views_sql = f"COALESCE(SUM({views_col}), 0)" if views_col else "0"
    wishlist_sql = (
        f"COALESCE(SUM(CASE WHEN COALESCE({wishlist_col}, FALSE) THEN 1 ELSE 0 END), 0)"
        if wishlist_col
        else "0"
    )
    avg_intent_sql = f"COALESCE(ROUND(AVG({intent_score_col}), 2), 0)" if intent_score_col else "0"
    max_intent_sql = f"COALESCE(MAX({intent_score_col}), 0)" if intent_score_col else "0"

    rows = _rows(
        f"""
        SELECT
            {product_col} AS product_id,
            {product_col} AS product_name,
            {total_views_sql} AS total_views,
            {unique_visitors_sql} AS unique_visitors,
            {wishlist_sql} AS wishlist_adds,
            {avg_intent_sql} AS avg_intent_score,
            CASE
                WHEN {max_intent_sql} >= 80 THEN 'HOT'
                WHEN {max_intent_sql} >= 45 THEN 'WARM'
                ELSE 'COLD'
            END AS intent_level
        FROM visitor_product_state
        WHERE shop_domain = :shop_domain
        GROUP BY {product_col}
        ORDER BY {avg_intent_sql} DESC, {total_views_sql} DESC
        LIMIT 10
        """,
        db,
        {"shop_domain": shop_domain},
    )

    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "product_id": row.get("product_id"),
                "product_name": row.get("product_name") or row.get("product_id"),
                "total_views": int(_safe_number(row.get("total_views"), 0)),
                "unique_visitors": int(_safe_number(row.get("unique_visitors"), 0)),
                "wishlist_adds": int(_safe_number(row.get("wishlist_adds"), 0)),
                "avg_intent_score": float(_safe_number(row.get("avg_intent_score"), 0)),
                "intent_level": row.get("intent_level") or "COLD",
            }
        )
    return cleaned


def _build_product_opportunities(db: Session, shop_domain: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "product_opportunities"):
        return []

    cols = _columns(db, "product_opportunities")
    product_col = _pick(cols, "product_id", "product_url")
    product_name_col = _pick(cols, "product_name", "name", "product_id", "product_url")
    signal_col = _pick(cols, "signal_type", "opportunity_type")
    priority_col = _pick(cols, "priority_score")
    action_col = _pick(cols, "recommended_action")
    explanation_col = _pick(cols, "explanation")
    plan_col = _pick(cols, "plan_required")
    lock_col = _pick(cols, "locked_for_lite")

    rows = _rows(
        f"""
        SELECT
            {_sql_value(product_col, "product_id")},
            {_sql_value(product_name_col or product_col, "product_name")},
            {_sql_value(signal_col, "signal_type")},
            {_sql_value(priority_col, "priority_score", "0")},
            {_sql_value(action_col, "recommended_action")},
            {_sql_value(explanation_col, "explanation")},
            {_sql_value(plan_col, "plan_required", "'pro'")},
            {_sql_value(lock_col, "locked_for_lite", "TRUE")}
        FROM product_opportunities
        WHERE shop_domain = :shop_domain
        ORDER BY COALESCE({_pick(cols, "priority_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
        {"shop_domain": shop_domain},
    )

    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "product_id": row.get("product_id"),
                "product_name": row.get("product_name") or row.get("product_id"),
                "signal_type": row.get("signal_type"),
                "priority_score": float(_safe_number(row.get("priority_score"), 0)),
                "recommended_action": row.get("recommended_action"),
                "explanation": row.get("explanation"),
                "plan_required": row.get("plan_required") or "pro",
                "locked_for_lite": _safe_bool(row.get("locked_for_lite"), True),
            }
        )
    return cleaned


def _build_price_intelligence(db: Session, shop_domain: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "price_intelligence"):
        return []

    cols = _columns(db, "price_intelligence")
    product_col = _pick(cols, "product_id", "product_url")
    product_name_col = _pick(cols, "product_name", "name", "product_id", "product_url")
    market_status_col = _pick(cols, "market_status")
    price_position_col = _pick(cols, "price_position")
    opp_col = _pick(cols, "price_opportunity")
    action_col = _pick(cols, "recommended_price_action")
    explanation_col = _pick(cols, "intelligence_explanation")
    confidence_col = _pick(cols, "confidence_score")
    plan_col = _pick(cols, "plan_required")
    lock_col = _pick(cols, "locked_for_lite")

    rows = _rows(
        f"""
        SELECT
            {_sql_value(product_col, "product_id")},
            {_sql_value(product_name_col or product_col, "product_name")},
            {_sql_value(market_status_col, "market_status")},
            {_sql_value(price_position_col, "price_position")},
            {_sql_value(opp_col, "price_opportunity")},
            {_sql_value(action_col, "recommended_price_action")},
            {_sql_value(explanation_col, "intelligence_explanation")},
            {_sql_value(confidence_col, "confidence_score", "0")},
            {_sql_value(plan_col, "plan_required", "'pro'")},
            {_sql_value(lock_col, "locked_for_lite", "TRUE")}
        FROM price_intelligence
        WHERE shop_domain = :shop_domain
        ORDER BY COALESCE({_pick(cols, "confidence_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
        {"shop_domain": shop_domain},
    )

    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "product_id": row.get("product_id"),
                "product_name": row.get("product_name") or row.get("product_id"),
                "market_status": row.get("market_status"),
                "price_position": row.get("price_position"),
                "price_opportunity": row.get("price_opportunity"),
                "recommended_price_action": row.get("recommended_price_action"),
                "intelligence_explanation": row.get("intelligence_explanation"),
                "confidence_score": float(_safe_number(row.get("confidence_score"), 0)),
                "plan_required": row.get("plan_required") or "pro",
                "locked_for_lite": _safe_bool(row.get("locked_for_lite"), True),
            }
        )
    return cleaned


def _build_market_lookup(db: Session, shop_domain: str) -> list[dict[str, Any]]:
    table_name = None
    if _table_exists(db, "market_lookup"):
        table_name = "market_lookup"
    elif _table_exists(db, "unique_product_detection"):
        table_name = "unique_product_detection"

    if not table_name:
        return []

    cols = _columns(db, table_name)
    product_col = _pick(cols, "product_id", "product_url")
    product_name_col = _pick(cols, "product_name", "name", "product_id", "product_url")
    lookup_status_col = _pick(cols, "lookup_status", "detection_status")
    comparable_col = _pick(cols, "comparable_presence")
    unique_col = _pick(cols, "uniqueness_hint")
    confidence_col = _pick(cols, "lookup_confidence", "confidence_score")
    summary_col = _pick(cols, "market_summary", "summary")
    next_step_col = _pick(cols, "recommended_next_step")
    plan_col = _pick(cols, "plan_required")
    lock_col = _pick(cols, "locked_for_lite")

    rows = _rows(
        f"""
        SELECT
            {_sql_value(product_col, "product_id")},
            {_sql_value(product_name_col or product_col, "product_name")},
            {_sql_value(lookup_status_col, "lookup_status", "'INFERRED_INTERNAL'")},
            {_sql_value(comparable_col, "comparable_presence", "'NOT_FOUND_YET'")},
            {_sql_value(unique_col, "uniqueness_hint", "'UNCLEAR'")},
            {_sql_value(confidence_col, "lookup_confidence", "0")},
            {_sql_value(summary_col, "market_summary", "'No summary available.'")},
            {_sql_value(next_step_col, "recommended_next_step", "'RUN_EXTERNAL_SEARCH'")},
            {_sql_value(plan_col, "plan_required", "'pro'")},
            {_sql_value(lock_col, "locked_for_lite", "TRUE")}
        FROM {table_name}
        WHERE shop_domain = :shop_domain
        ORDER BY COALESCE({_pick(cols, "lookup_confidence", "confidence_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
        {"shop_domain": shop_domain},
    )

    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "product_id": row.get("product_id"),
                "product_name": row.get("product_name") or row.get("product_id"),
                "lookup_status": row.get("lookup_status"),
                "comparable_presence": row.get("comparable_presence"),
                "uniqueness_hint": row.get("uniqueness_hint"),
                "lookup_confidence": float(_safe_number(row.get("lookup_confidence"), 0)),
                "market_summary": row.get("market_summary"),
                "recommended_next_step": row.get("recommended_next_step"),
                "plan_required": row.get("plan_required") or "pro",
                "locked_for_lite": _safe_bool(row.get("locked_for_lite"), True),
            }
        )
    return cleaned


def _product_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _build_ai_recommended_actions(
    top_products: list[dict[str, Any]],
    price_intelligence: list[dict[str, Any]],
    market_lookup: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    price_map: dict[str, dict[str, Any]] = {}
    market_map: dict[str, dict[str, Any]] = {}

    for item in price_intelligence:
        key = _product_key(item.get("product_id") or item.get("product_name"))
        if key:
            price_map[key] = item

    for item in market_lookup:
        key = _product_key(item.get("product_id") or item.get("product_name"))
        if key:
            market_map[key] = item

    results: list[dict[str, Any]] = []

    for product in top_products[:6]:
        product_id = product.get("product_id")
        product_name = product.get("product_name") or product_id or "Unnamed product"
        key = _product_key(product_id or product_name)

        price_item = price_map.get(key)
        market_item = market_map.get(key)

        inferred_uniqueness = None
        if not market_item:
            inferred_uniqueness = infer_external_lookup(
                product_id=product_id,
                product_name=product_name,
                description=None,
            )

        uniqueness_hint = (
            (market_item or {}).get("uniqueness_hint")
            or (inferred_uniqueness or {}).get("uniqueness_hint")
            or "UNCLEAR"
        )

        price_opportunity = (price_item or {}).get("price_opportunity")

        decision = compute_decision(
            intent_score=float(_safe_number(product.get("avg_intent_score"), 0)),
            uniqueness_hint=str(uniqueness_hint),
            price_opportunity=price_opportunity,
        )

        results.append(
            {
                "product_id": product_id,
                "product_name": product_name,
                "recommended_action": decision.get("recommended_action"),
                "reason": decision.get("reason"),
                "confidence": int(_safe_number(decision.get("confidence"), 0)),
                "intent_score": float(_safe_number(product.get("avg_intent_score"), 0)),
                "intent_level": product.get("intent_level") or "COLD",
                "uniqueness_hint": uniqueness_hint,
                "price_opportunity": price_opportunity,
                "plan_required": "pro",
                "locked_for_lite": True,
            }
        )

    return results


def _build_revenue_window_tease(db: Session, shop_domain: str) -> dict:
    """
    Lite-facing revenue window tease — total dollar amount, no breakdown.

    Reads from active_nudges (already computed by segment_monitor_worker)
    so this is a fast query without re-running segment calculations.
    Shows Lite merchants there is revenue at risk without revealing which
    products or how to act on it.
    """
    total_window = 0.0
    active_nudge_count = 0

    if _table_exists(db, "active_nudges"):
        result = _row(
            """
            SELECT
                COALESCE(SUM(estimated_revenue_window), 0) AS total_window,
                COUNT(*) AS nudge_count
            FROM active_nudges
            WHERE shop_domain = :shop
              AND status      = 'active'
              AND expires_at  > NOW()
            """,
            db,
            {"shop": shop_domain},
        )
        total_window        = float(_safe_number(result.get("total_window"), 0))
        active_nudge_count  = int(_safe_number(result.get("nudge_count"), 0))

    return {
        "estimated_revenue_at_risk": round(total_window, 2),
        "active_opportunity_count":  active_nudge_count,
        "note":                      "Upgrade to Pro to see which products and segments are at risk.",
    }


def _build_revenue_windows(db: Session, shop_domain: str) -> dict:
    """
    Pro revenue windows — per-product breakdown with visitor segments.

    Returns the top 5 active nudge opportunities ranked by estimated revenue.
    Reads from active_nudges to avoid expensive per-product segment recomputation
    at query time.  Segment data is always fresh (segment_monitor_worker runs every 5 min).
    """
    total_window = 0.0
    opportunities = []

    if _table_exists(db, "active_nudges"):
        rows = _rows(
            """
            SELECT
                product_url,
                action_type,
                visitor_count,
                estimated_revenue_window,
                calibration_state,
                expires_at
            FROM active_nudges
            WHERE shop_domain = :shop
              AND status      = 'active'
              AND expires_at  > NOW()
            ORDER BY estimated_revenue_window DESC
            LIMIT 5
            """,
            db,
            {"shop": shop_domain},
        )

        for row in rows:
            window = float(_safe_number(row.get("estimated_revenue_window"), 0))
            total_window += window
            opportunities.append({
                "product_url":       row.get("product_url"),
                "action_type":       row.get("action_type"),
                "visitor_count":     int(_safe_number(row.get("visitor_count"), 0)),
                "revenue_window":    round(window, 2),
                "calibration_state": row.get("calibration_state"),
                "expires_at":        str(row.get("expires_at")) if row.get("expires_at") else None,
            })

    # Resolve real currency from shop_orders — never hardcode
    shop_currency = None
    try:
        from app.services.revenue_metrics import get_shop_currency
        shop_currency = get_shop_currency(db, shop_domain)
    except Exception:
        pass

    return {
        "total_revenue_at_risk": round(total_window, 2),
        "opportunities":         opportunities,
        "currency":              shop_currency or "USD",
        "currency_is_real":      shop_currency is not None,
    }


def _get_calibration_summary(db: Session, shop_domain: str) -> dict[str, Any]:
    """
    Return calibration quality summary for the shop.

    Tells the frontend whether conversion estimates are empirical
    (based on real data) or fallback (industry defaults).
    """
    try:
        if not _table_exists(db, "shop_conversion_calibrations"):
            return {"state": "no_data", "is_empirical": False, "label": "Estimated (no order data)"}

        row = _row(
            """
            SELECT is_empirical, sample_size, converter_count, base_cvr, trained_at
            FROM shop_conversion_calibrations
            WHERE shop_domain = :shop
            ORDER BY trained_at DESC NULLS LAST
            LIMIT 1
            """,
            db,
            {"shop": shop_domain},
        )
        if not row:
            return {"state": "no_data", "is_empirical": False, "label": "Estimated (no order data)"}

        is_empirical = _safe_bool(row.get("is_empirical"), False)
        sample_size = int(_safe_number(row.get("sample_size"), 0))
        converter_count = int(_safe_number(row.get("converter_count"), 0))

        if is_empirical:
            return {
                "state": "empirical",
                "is_empirical": True,
                "sample_size": sample_size,
                "converter_count": converter_count,
                "label": f"Based on your data ({converter_count} orders)",
            }
        else:
            return {
                "state": "fallback",
                "is_empirical": False,
                "sample_size": sample_size,
                "converter_count": converter_count,
                "label": "Estimated (low data)" if sample_size > 0 else "Estimated (no order data)",
            }
    except Exception:
        return {"state": "error", "is_empirical": False, "label": "Estimated"}


def _build_sandbox_runs() -> list[dict]:
    runs = []
    if not SANDBOX_PATH.exists():
        return runs
    for path in sorted(SANDBOX_PATH.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        status_file = path / "status.txt"
        status = status_file.read_text().strip() if status_file.exists() else "unknown"
        runs.append({"run_id": path.name, "status": status, "sandbox_path": str(path)})
    return runs[:10]


# ---------------------------------------------------------------------------
# Routes
#
# Product boundary
# ----------------
# Lite route  GET /dashboard/overview
#   Returns only Lite-safe sections: summary (aggregate counts) and
#   top_products (behavioral observations).  Pro-only builders are not
#   called — this saves the DB queries and the infer_external_lookup() call
#   that _build_ai_recommended_actions() makes for every top product.
#
# Pro route   GET /dashboard/overview/pro
#   Returns the full payload including all Pro-only sections.
#   Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).
#
# Section classification
# ----------------------
# Lite-safe:  summary          — aggregate counts and intent segmentation
#             top_products     — product list with behavioral metrics
#
# Pro-only:   price_intelligence    — plan_required="pro" in every row;
#                                     prescriptive pricing actions
#             market_lookup         — plan_required="pro" in every row;
#                                     competitor analysis and next steps
#             product_opportunities — plan_required="pro" in every row;
#                                     opportunity signals with recommended_action
#             top_hot_visitors      — individual visitor intent records;
#                                     currently dead state in the frontend but
#                                     included in Pro for completeness
#             ai_recommended_actions — hardcoded plan_required="pro"; cross-
#                                      signal prescriptive actions, requires
#                                      price_intelligence + market_lookup data
#
# Why no field-level stripping on this endpoint
# ---------------------------------------------
# Unlike opportunities (explanation Lite / human_action Pro) or alerts
# (message Lite / action Pro), the Pro-only sections here have NO Lite-safe
# subset worth exposing.  Every meaningful field in price_intelligence,
# market_lookup, and ai_recommended_actions is prescriptive or derived from
# a Pro-tier proprietary analysis.  The correct boundary is the whole section.
# ---------------------------------------------------------------------------

@router.get("/overview")
def get_dashboard_overview(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Lite dashboard overview — summary, top_products, real AOV/currency.

    Cached in Redis for 60 seconds.  Dashboard data changes on aggregation
    worker cycles (5 min), so 60s staleness is imperceptible to merchants.
    """
    from app.core.redis_client import cache_get, cache_set, KEY_DASHBOARD, TTL_DASHBOARD
    cache_key = KEY_DASHBOARD.format(shop=shop) + ":lite"

    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Resolve real AOV and currency for truthful revenue estimates
    from app.services.revenue_metrics import get_shop_aov, get_shop_currency, FALLBACK_AOV
    shop_currency = get_shop_currency(db, shop)
    real_aov = get_shop_aov(db, shop, currency=shop_currency)
    aov_is_real = real_aov != FALLBACK_AOV

    # Generate intelligence brief (cached separately with 5-min TTL)
    store_brief = None
    try:
        from app.core.redis_client import cache_get, cache_set as cs
        brief_key = f"hs:brief:{shop}"
        cached_brief = cache_get(brief_key)
        if cached_brief is not None:
            store_brief = cached_brief
        else:
            from app.services.store_insight_engine import generate_store_brief
            brief = generate_store_brief(db, shop)
            if brief:
                store_brief = brief.to_dict()
                cs(brief_key, store_brief, 300)  # 5-min cache
    except Exception:
        pass

    result = {
        "summary":              _build_summary(db, shop),
        "top_products":         _build_top_products(db, shop),
        "revenue_window_tease": _build_revenue_window_tease(db, shop),
        "shop_aov":             round(real_aov, 2),
        "shop_currency":        shop_currency or "USD",
        "aov_is_real":          aov_is_real,
        "calibration":          _get_calibration_summary(db, shop),
        "intelligence":         store_brief,
    }
    cache_set(cache_key, result, TTL_DASHBOARD)
    return result


@router.get("/intelligence")
def get_dashboard_intelligence(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Store intelligence brief — multi-signal synthesis with priority action.

    Returns the StoreBrief: signal trends, diagnosis, priority insight, raw data.
    Cached for 5 minutes. This is the "decision-first" data for the dashboard hero.
    """
    from app.core.redis_client import cache_get, cache_set
    brief_key = f"hs:brief:{shop}"

    cached = cache_get(brief_key)
    if cached is not None:
        return cached

    from app.services.store_insight_engine import generate_store_brief
    brief = generate_store_brief(db, shop)
    if not brief:
        return {"status": "insufficient_data", "message": "Not enough data yet for intelligence analysis."}

    result = brief.to_dict()
    cache_set(brief_key, result, 300)
    return result


@router.get("/overview/pro")
def get_dashboard_overview_pro(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Pro dashboard overview — Lite data + price intelligence + market lookup +
    revenue windows.

    Removed from Pro computation (audit fix — frontend ignores these):
    - top_hot_visitors (dead data, never rendered)
    - product_opportunities (fetched separately via /opportunities/pro)
    - ai_recommended_actions (fetched separately via /ai/actions, expensive)

    Cached in Redis for 60 seconds.
    """
    from app.core.redis_client import cache_get, cache_set, KEY_DASHBOARD, TTL_DASHBOARD
    cache_key = KEY_DASHBOARD.format(shop=shop) + ":pro"

    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from app.services.revenue_metrics import get_shop_aov, get_shop_currency, FALLBACK_AOV
    shop_currency = get_shop_currency(db, shop)
    real_aov = get_shop_aov(db, shop, currency=shop_currency)
    aov_is_real = real_aov != FALLBACK_AOV

    result = {
        "summary":            _build_summary(db, shop),
        "top_products":       _build_top_products(db, shop),
        "price_intelligence": _build_price_intelligence(db, shop),
        "market_lookup":      _build_market_lookup(db, shop),
        "revenue_windows":    _build_revenue_windows(db, shop),
        "shop_aov":           round(real_aov, 2),
        "shop_currency":      shop_currency or "USD",
        "aov_is_real":        aov_is_real,
        "calibration":        _get_calibration_summary(db, shop),
    }
    cache_set(cache_key, result, TTL_DASHBOARD)
    return result
