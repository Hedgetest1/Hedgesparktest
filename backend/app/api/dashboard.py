from __future__ import annotations

from pathlib import Path
import json

SANDBOX_PATH = Path("/opt/wishspark/sandbox")

def _build_ai_analysis():
    runs = []

    if not SANDBOX_PATH.exists():
        return runs

    for path in sorted(SANDBOX_PATH.iterdir(), reverse=True):

        analysis_file = path / "analysis.json"

        if not analysis_file.exists():
            continue

        try:
            analysis = json.loads(analysis_file.read_text())
            analysis["run_id"] = path.name
            runs.append(analysis)
        except Exception:
            continue

    return runs[:10]




from typing import Any

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import engine
from app.external_lookup_engine import infer_external_lookup
from app.api.decision_engine import compute_decision

try:
    from app.core.database import SessionLocal  # type: ignore
except Exception:
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


def _rows(query: str, db: Session) -> list[dict[str, Any]]:
    try:
        result = db.execute(text(query))
        return [_to_dict(row) for row in result.fetchall()]
    except Exception:
        return []


def _row(query: str, db: Session) -> dict[str, Any]:
    try:
        result = db.execute(text(query))
        row = result.fetchone()
        return _to_dict(row) if row else {}
    except Exception:
        return {}


def _table_exists(db: Session, table_name: str) -> bool:
    result = _row(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = '{table_name}'
        ) AS exists
        """,
        db,
    )
    return _safe_bool(result.get("exists"), False)


def _columns(db: Session, table_name: str) -> set[str]:
    if not _table_exists(db, table_name):
        return set()
    rows = _rows(
        f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = '{table_name}'
        """,
        db,
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


def _build_summary(db: Session) -> dict[str, Any]:
    total_visitors = 0
    total_sessions = 0
    total_events = 0
    hot_visitors = 0
    warm_visitors = 0
    cold_visitors = 0
    wishlist_adds = 0
    avg_intent_score = 0
    conversion_ready_products = 0

    if _table_exists(db, "events"):
        event_cols = _columns(db, "events")
        visitor_col = _pick(event_cols, "visitor_id")
        session_col = _pick(event_cols, "session_id")
        event_type_col = _pick(event_cols, "event_type")

        if visitor_col:
            total_visitors = int(
                _safe_number(
                    _row(
                        f"SELECT COUNT(DISTINCT {visitor_col}) AS value FROM events",
                        db,
                    ).get("value"),
                    0,
                )
            )

        if session_col:
            total_sessions = int(
                _safe_number(
                    _row(
                        f"SELECT COUNT(DISTINCT {session_col}) AS value FROM events",
                        db,
                    ).get("value"),
                    0,
                )
            )

        total_events = int(
            _safe_number(_row("SELECT COUNT(*) AS value FROM events", db).get("value"), 0)
        )

        if event_type_col:
            wishlist_adds = int(
                _safe_number(
                    _row(
                        f"""
                        SELECT COUNT(*) AS value
                        FROM events
                        WHERE {event_type_col} = 'wishlist_add'
                        """,
                        db,
                    ).get("value"),
                    0,
                )
            )

    if _table_exists(db, "visitor_product_state"):
        cols = _columns(db, "visitor_product_state")
        intent_level_col = _pick(cols, "intent_level")
        intent_score_col = _pick(cols, "intent_score")

        if intent_level_col:
            counts = _row(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN UPPER({intent_level_col}) = 'HOT' THEN 1 ELSE 0 END), 0) AS hot,
                    COALESCE(SUM(CASE WHEN UPPER({intent_level_col}) = 'WARM' THEN 1 ELSE 0 END), 0) AS warm,
                    COALESCE(SUM(CASE WHEN UPPER({intent_level_col}) = 'COLD' THEN 1 ELSE 0 END), 0) AS cold
                FROM visitor_product_state
                """,
                db,
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
                    """,
                    db,
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
                        """,
                        db,
                    ).get("value"),
                    0,
                )
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

    where_clause = f"WHERE {intent_score_col} >= 80"
    if intent_level_col:
        where_clause = f"WHERE UPPER(COALESCE({intent_level_col}, '')) = 'HOT'"

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


def _build_top_products(db: Session) -> list[dict[str, Any]]:
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
        GROUP BY {product_col}
        ORDER BY {avg_intent_sql} DESC, {total_views_sql} DESC
        LIMIT 10
        """,
        db,
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


def _build_product_opportunities(db: Session) -> list[dict[str, Any]]:
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
        ORDER BY COALESCE({_pick(cols, "priority_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
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


def _build_price_intelligence(db: Session) -> list[dict[str, Any]]:
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
        ORDER BY COALESCE({_pick(cols, "confidence_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
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


def _build_market_lookup(db: Session) -> list[dict[str, Any]]:
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
        ORDER BY COALESCE({_pick(cols, "lookup_confidence", "confidence_score") or '0'}, 0) DESC
        LIMIT 10
        """,
        db,
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


@router.get("/overview")
def get_dashboard_overview():
    db = SessionLocal()
    try:
        summary = _build_summary(db)
        top_hot_visitors = _build_top_hot_visitors(db)
        top_products = _build_top_products(db)
        product_opportunities = _build_product_opportunities(db)
        price_intelligence = _build_price_intelligence(db)
        market_lookup = _build_market_lookup(db)
        ai_recommended_actions = _build_ai_recommended_actions(
            top_products=top_products,
            price_intelligence=price_intelligence,
            market_lookup=market_lookup,
        )

        return {
            "summary": summary,
            "top_hot_visitors": top_hot_visitors,
            "top_products": top_products,
            "product_opportunities": product_opportunities,
            "price_intelligence": price_intelligence,
            "market_lookup": market_lookup,
            "ai_recommended_actions": ai_recommended_actions,
        }
    finally:
        db.close()

from pathlib import Path

SANDBOX_PATH = Path("/opt/wishspark/sandbox")

def _build_sandbox_runs():
    runs = []

    if not SANDBOX_PATH.exists():
        return runs

    for path in sorted(SANDBOX_PATH.iterdir(), reverse=True):

        if not path.is_dir():
            continue

        status_file = path / "status.txt"

        status = "unknown"
        if status_file.exists():
            status = status_file.read_text().strip()

        runs.append({
            "run_id": path.name,
            "status": status,
            "sandbox_path": str(path)
        })

    return runs[:10]
