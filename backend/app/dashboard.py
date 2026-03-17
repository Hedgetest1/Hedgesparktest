from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

# Fallback imports to tolerate small project-structure differences
try:
    from db import get_db
except ImportError:
    try:
        from database import get_db
    except ImportError:
        try:
            from app.db import get_db
        except ImportError:
            from app.database import get_db  # type: ignore


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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
    value_str = str(value).strip().lower()
    if value_str in {"true", "1", "yes", "y", "on"}:
        return True
    if value_str in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _rows(query: str, db: Session, fallback: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if fallback is None:
        fallback = []
    try:
        result = db.execute(text(query))
        return [_to_dict(row) for row in result.fetchall()]
    except Exception:
        return fallback


def _row(query: str, db: Session, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if fallback is None:
        fallback = {}
    try:
        result = db.execute(text(query))
        row = result.fetchone()
        return _to_dict(row) if row else fallback
    except Exception:
        return fallback


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        result = db.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        row = result.fetchone()
        if not row:
            return False
        first_value = list(_to_dict(row).values())[0]
        return _safe_bool(first_value, False)
    except Exception:
        return False


def _build_summary(db: Session) -> dict[str, Any]:
    has_events = _table_exists(db, "events")
    has_state = _table_exists(db, "visitor_product_state")
    has_opportunities = _table_exists(db, "product_opportunities")

    total_visitors = 0
    total_sessions = 0
    total_events = 0
    wishlist_adds = 0
    hot_visitors = 0
    warm_visitors = 0
    cold_visitors = 0
    avg_intent_score = 0
    conversion_ready_products = 0

    if has_events:
        total_visitors_row = _row(
            """
            SELECT COUNT(DISTINCT visitor_id) AS total_visitors
            FROM events
            """,
            db,
            {"total_visitors": 0},
        )
        total_visitors = int(_safe_number(total_visitors_row.get("total_visitors"), 0))

        total_sessions_row = _row(
            """
            SELECT COUNT(DISTINCT session_id) AS total_sessions
            FROM events
            """,
            db,
            {"total_sessions": 0},
        )
        total_sessions = int(_safe_number(total_sessions_row.get("total_sessions"), 0))

        total_events_row = _row(
            """
            SELECT COUNT(*) AS total_events
            FROM events
            """,
            db,
            {"total_events": 0},
        )
        total_events = int(_safe_number(total_events_row.get("total_events"), 0))

        wishlist_adds_row = _row(
            """
            SELECT COUNT(*) AS wishlist_adds
            FROM events
            WHERE event_type = 'wishlist_add'
            """,
            db,
            {"wishlist_adds": 0},
        )
        wishlist_adds = int(_safe_number(wishlist_adds_row.get("wishlist_adds"), 0))

    if has_state:
        intent_counts = _row(
            """
            SELECT
                COALESCE(SUM(CASE WHEN UPPER(intent_level) = 'HOT' THEN 1 ELSE 0 END), 0) AS hot_visitors,
                COALESCE(SUM(CASE WHEN UPPER(intent_level) = 'WARM' THEN 1 ELSE 0 END), 0) AS warm_visitors,
                COALESCE(SUM(CASE WHEN UPPER(intent_level) = 'COLD' THEN 1 ELSE 0 END), 0) AS cold_visitors,
                COALESCE(ROUND(AVG(intent_score), 2), 0) AS avg_intent_score
            FROM visitor_product_state
            """,
            db,
            {
                "hot_visitors": 0,
                "warm_visitors": 0,
                "cold_visitors": 0,
                "avg_intent_score": 0,
            },
        )
        hot_visitors = int(_safe_number(intent_counts.get("hot_visitors"), 0))
        warm_visitors = int(_safe_number(intent_counts.get("warm_visitors"), 0))
        cold_visitors = int(_safe_number(intent_counts.get("cold_visitors"), 0))
        avg_intent_score = _safe_number(intent_counts.get("avg_intent_score"), 0)

    if has_opportunities:
        conversion_ready_row = _row(
            """
            SELECT COUNT(DISTINCT product_id) AS conversion_ready_products
            FROM product_opportunities
            """,
            db,
            {"conversion_ready_products": 0},
        )
        conversion_ready_products = int(
            _safe_number(conversion_ready_row.get("conversion_ready_products"), 0)
        )

    return {
        "total_visitors": total_visitors,
        "total_sessions": total_sessions,
        "total_events": total_events,
        "hot_visitors": hot_visitors,
        "warm_visitors": warm_visitors,
        "cold_visitors": cold_visitors,
        "wishlist_adds": wishlist_adds,
        "avg_intent_score": avg_intent_score,
        "conversion_ready_products": conversion_ready_products,
    }


def _build_top_hot_visitors(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "visitor_product_state"):
        return []

    rows = _rows(
        """
        SELECT
            visitor_id,
            session_id,
            product_id,
            COALESCE(total_views, 0) AS total_views,
            COALESCE(total_dwell_seconds, 0) AS total_dwell_seconds,
            COALESCE(max_scroll_depth, 0) AS max_scroll_depth,
            COALESCE(wishlist_added, FALSE) AS wishlist_added,
            COALESCE(intent_score, 0) AS intent_score,
            COALESCE(intent_level, 'COLD') AS intent_level
        FROM visitor_product_state
        WHERE UPPER(COALESCE(intent_level, '')) = 'HOT'
        ORDER BY
            COALESCE(intent_score, 0) DESC,
            COALESCE(total_dwell_seconds, 0) DESC,
            COALESCE(total_views, 0) DESC
        LIMIT 10
        """,
        db,
    )

    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                "visitor_id": row.get("visitor_id"),
                "session_id": row.get("session_id"),
                "product_id": row.get("product_id"),
                "total_views": int(_safe_number(row.get("total_views"), 0)),
                "total_dwell_seconds": int(_safe_number(row.get("total_dwell_seconds"), 0)),
                "max_scroll_depth": float(_safe_number(row.get("max_scroll_depth"), 0)),
                "wishlist_added": _safe_bool(row.get("wishlist_added"), False),
                "intent_score": float(_safe_number(row.get("intent_score"), 0)),
                "intent_level": row.get("intent_level") or "COLD",
            }
        )
    return cleaned


def _build_top_products(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "visitor_product_state"):
        return []

    rows = _rows(
        """
        SELECT
            product_id,
            MIN(product_id) AS product_name,
            COALESCE(SUM(total_views), 0) AS total_views,
            COUNT(DISTINCT visitor_id) AS unique_visitors,
            COALESCE(SUM(CASE WHEN COALESCE(wishlist_added, FALSE) THEN 1 ELSE 0 END), 0) AS wishlist_adds,
            COALESCE(ROUND(AVG(intent_score), 2), 0) AS avg_intent_score,
            CASE
                WHEN COALESCE(MAX(intent_score), 0) >= 80 THEN 'HOT'
                WHEN COALESCE(MAX(intent_score), 0) >= 45 THEN 'WARM'
                ELSE 'COLD'
            END AS intent_level
        FROM visitor_product_state
        GROUP BY product_id
        ORDER BY
            COALESCE(ROUND(AVG(intent_score), 2), 0) DESC,
            COALESCE(SUM(total_views), 0) DESC,
            COUNT(DISTINCT visitor_id) DESC
        LIMIT 10
        """,
        db,
    )

    cleaned: list[dict[str, Any]] = []
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


def _build_product_opportunities(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "product_opportunities"):
        return []

    rows = _rows(
        """
        SELECT
            product_id,
            COALESCE(product_name, product_id) AS product_name,
            signal_type,
            COALESCE(priority_score, 0) AS priority_score,
            recommended_action,
            explanation,
            COALESCE(plan_required, 'pro') AS plan_required,
            COALESCE(locked_for_lite, TRUE) AS locked_for_lite
        FROM product_opportunities
        ORDER BY
            COALESCE(priority_score, 0) DESC,
            product_id ASC
        LIMIT 10
        """,
        db,
    )

    cleaned: list[dict[str, Any]] = []
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


def _build_price_intelligence(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "price_intelligence"):
        return []

    rows = _rows(
        """
        SELECT
            product_id,
            COALESCE(product_name, product_id) AS product_name,
            market_status,
            price_position,
            price_opportunity,
            recommended_price_action,
            intelligence_explanation,
            COALESCE(confidence_score, 0) AS confidence_score,
            COALESCE(plan_required, 'pro') AS plan_required,
            COALESCE(locked_for_lite, TRUE) AS locked_for_lite
        FROM price_intelligence
        ORDER BY
            COALESCE(confidence_score, 0) DESC,
            product_id ASC
        LIMIT 10
        """,
        db,
    )

    cleaned: list[dict[str, Any]] = []
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


def _build_market_lookup(db: Session) -> list[dict[str, Any]]:
    source_table = None
    if _table_exists(db, "market_lookup"):
        source_table = "market_lookup"
    elif _table_exists(db, "unique_product_detection"):
        source_table = "unique_product_detection"

    if source_table is None:
        return []

    if source_table == "market_lookup":
        rows = _rows(
            """
            SELECT
                product_id,
                COALESCE(product_name, product_id) AS product_name,
                lookup_status,
                comparable_presence,
                uniqueness_hint,
                COALESCE(lookup_confidence, 0) AS lookup_confidence,
                market_summary,
                recommended_next_step,
                COALESCE(plan_required, 'pro') AS plan_required,
                COALESCE(locked_for_lite, TRUE) AS locked_for_lite
            FROM market_lookup
            ORDER BY
                COALESCE(lookup_confidence, 0) DESC,
                product_id ASC
            LIMIT 10
            """,
            db,
        )
    else:
        rows = _rows(
            """
            SELECT
                product_id,
                COALESCE(product_name, product_id) AS product_name,
                COALESCE(detection_status, 'INFERRED_INTERNAL') AS lookup_status,
                COALESCE(comparable_presence, 'NOT_FOUND_YET') AS comparable_presence,
                COALESCE(uniqueness_hint, 'LIKELY_UNIQUE') AS uniqueness_hint,
                COALESCE(confidence_score, 0) AS lookup_confidence,
                COALESCE(summary, 'Internal uniqueness inference available.') AS market_summary,
                COALESCE(recommended_next_step, 'CHECK_EXTERNAL_MATCHES_AND_STORYTELLING') AS recommended_next_step,
                'pro' AS plan_required,
                TRUE AS locked_for_lite
            FROM unique_product_detection
            ORDER BY
                COALESCE(confidence_score, 0) DESC,
                product_id ASC
            LIMIT 10
            """,
            db,
        )

    cleaned: list[dict[str, Any]] = []
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


@router.get("/overview")
def get_dashboard_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """
    Stable dashboard endpoint for the Next.js UI.
    Always returns the same top-level shape, even if some tables are empty
    or one intelligence block temporarily fails.
    """
    response = {
        "summary": {},
        "top_hot_visitors": [],
        "top_products": [],
        "product_opportunities": [],
        "price_intelligence": [],
        "market_lookup": [],
    }

    try:
        response["summary"] = _build_summary(db)
    except Exception:
        response["summary"] = {
            "total_visitors": 0,
            "total_sessions": 0,
            "total_events": 0,
            "hot_visitors": 0,
            "warm_visitors": 0,
            "cold_visitors": 0,
            "wishlist_adds": 0,
            "avg_intent_score": 0,
            "conversion_ready_products": 0,
        }

    try:
        response["top_hot_visitors"] = _build_top_hot_visitors(db)
    except Exception:
        response["top_hot_visitors"] = []

    try:
        response["top_products"] = _build_top_products(db)
    except Exception:
        response["top_products"] = []

    try:
        response["product_opportunities"] = _build_product_opportunities(db)
    except Exception:
        response["product_opportunities"] = []

    try:
        response["price_intelligence"] = _build_price_intelligence(db)
    except Exception:
        response["price_intelligence"] = []

    try:
        response["market_lookup"] = _build_market_lookup(db)
    except Exception:
        response["market_lookup"] = []

    return response


@router.get("/debug/schema")
def get_dashboard_schema_debug(db: Session = Depends(get_db)) -> dict[str, Any]:
    """
    Small diagnostic endpoint useful while stabilizing the VPS deployment.
    """
    table_names = [
        "events",
        "visitors",
        "visitor_product_state",
        "product_opportunities",
        "price_intelligence",
        "unique_product_detection",
        "market_lookup",
    ]

    return {
        "tables": {table_name: _table_exists(db, table_name) for table_name in table_names}
    }
