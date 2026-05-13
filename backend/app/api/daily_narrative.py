"""
daily_narrative.py — The storytelling block for the dashboard.

Tells the merchant a 3-sentence story of what happened in their store
today, in plain language, using deterministic composition over existing
metrics (no LLM — per the llm_usage_principle memory).

Format:
  "Today your store had X visitors.
   Y showed real intent — Z we're already actioning.
   Here's what matters most right now: {top_signal}."

Data sources:
  - product_metrics (visitor count today)
  - opportunity_signals (intent signals today)
  - nudge_events (how many nudges fired today)
  - top action candidate (highest-ranked unused opportunity)

Endpoint: GET /pro/daily-narrative
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.core.currency import format_money
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["daily_narrative"])




class DailyNarrativeResponse(BaseModel):
    shop_domain: str
    headline: str
    paragraphs: list[str]
    stats: dict
    top_next_action: str | None
    # Phase Ω causal layer — explains *why* the day looks like it does.
    # Optional so older clients keep working.
    why: dict | None = None
    fusion_alerts: list[dict] = []
    # Shop's native currency (USD/EUR/GBP/…) — dashboard renders
    # `stats.revenue_today_eur` with the matching symbol.
    currency: str = "USD"
    generated_at: str


# ---------------------------------------------------------------------------
# _compute_narrative — stage helpers
# Refactor 2026-05-13 (A3 close): 213-LOC god function → composer + 10
# pure stage helpers (4 fetchers + 3 paragraph builders + causal overlay +
# plural helper). Contract preserved byte-identical. SQL hoisted to
# module constants. Silent exception fallbacks now observe via
# record_silent_return — "default 0 on query failure" remains the
# documented behavior, but spike in failures surfaces in metrics.
# ---------------------------------------------------------------------------


_VISITORS_SQL = sql_text("""
    SELECT COUNT(DISTINCT visitor_id) FROM events
    WHERE shop_domain = :shop AND timestamp >= :c_ms
""")


_INTENT_SQL = sql_text("""
    SELECT COUNT(*) FROM opportunity_signals
    WHERE shop_domain = :shop AND detected_at >= :c
      AND signal_type IN (
        'HIGH_ENGAGEMENT_NO_ACTION',
        'SCROLL_HIGH_NO_CLICK',
        'HIGH_RETURN_LOW_CONVERSION',
        'RETURN_VISITOR_INTEREST'
      )
""")


_NUDGES_SQL = sql_text("""
    SELECT COUNT(*) FROM nudge_events
    WHERE shop_domain = :shop AND created_at >= :c
      AND event_type = 'nudge_impression'
""")


_ORDERS_COUNT_SQL = sql_text("""
    SELECT COUNT(*) FROM shop_orders
    WHERE shop_domain = :shop AND created_at >= :c
""")


_ORDERS_REVENUE_SQL = sql_text("""
    SELECT COALESCE(SUM(total_price), 0) FROM shop_orders
    WHERE shop_domain = :shop AND created_at >= :c
      AND (:currency IS NULL OR currency = :currency)
""")


_TOP_ACTION_SQL = sql_text("""
    SELECT product_url, signal_type FROM opportunity_signals
    WHERE shop_domain = :shop AND detected_at >= :c
    ORDER BY signal_strength DESC NULLS LAST
    LIMIT 1
""")


def _record_query_fail(query: str) -> None:
    """Observe a 'query failed → return 0/None default' fallback."""
    from app.core.silent_fallback import record_silent_return
    record_silent_return(f"daily_narrative.{query}_fail")


def _fetch_visitors_today(db: Session, shop: str, start_of_day_ms: int) -> int:
    try:
        return int(
            db.execute(_VISITORS_SQL, {"shop": shop, "c_ms": start_of_day_ms}).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: visitors query failed: %s", exc)
        _record_query_fail("visitors")
        return 0


def _fetch_intent_count(db: Session, shop: str, start_of_day: datetime) -> int:
    try:
        return int(
            db.execute(_INTENT_SQL, {"shop": shop, "c": start_of_day}).scalar() or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: intent signals query failed: %s", exc)
        _record_query_fail("intent")
        return 0


def _fetch_nudges_fired(db: Session, shop: str, start_of_day: datetime) -> int:
    try:
        return int(
            db.execute(_NUDGES_SQL, {"shop": shop, "c": start_of_day}).scalar() or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: nudges fired query failed: %s", exc)
        _record_query_fail("nudges")
        return 0


def _fetch_orders_today(
    db: Session, shop: str, currency: str | None, start_of_day: datetime,
) -> tuple[int, float]:
    """Returns (orders_count, revenue) — both default to 0 on failure
    (single try wraps both queries so an order/revenue mismatch is
    avoided when the table is partially available)."""
    try:
        orders = int(
            db.execute(_ORDERS_COUNT_SQL, {"shop": shop, "c": start_of_day}).scalar() or 0
        )
        revenue = float(
            db.execute(_ORDERS_REVENUE_SQL, {
                "shop": shop, "c": start_of_day, "currency": currency,
            }).scalar() or 0
        )
        return orders, revenue
    except Exception as exc:
        log.warning("daily_narrative: orders query failed: %s", exc)
        _record_query_fail("orders")
        return 0, 0.0


def _fetch_top_action(db: Session, shop: str, lookback_start: datetime) -> str | None:
    """Returns a humanized 'product is showing X' string or None."""
    try:
        row = db.execute(_TOP_ACTION_SQL, {"shop": shop, "c": lookback_start}).fetchone()
    except Exception as exc:
        log.warning("daily_narrative: top action query failed: %s", exc)
        _record_query_fail("top_action")
        return None
    if not row:
        return None
    product_url = (row[0] or "").replace("/products/", "")[:60] or "your top product"
    stype_human = (row[1] or "signal").replace("_", " ").lower()
    return f"{product_url} is showing {stype_human}"


def _plural(n: int, singular: str, plural: str) -> str:
    """English plural — singular when n==1, plural otherwise (0 IS plural)."""
    return singular if n == 1 else plural


def _compose_visitor_paragraph(visitors_today: int) -> str:
    if visitors_today <= 0:
        return "Today is quiet — no visitors logged yet. Your tracker is listening."
    return (
        f"So far today, {visitors_today} "
        f"{_plural(visitors_today, 'person has visited', 'people have visited')} your store."
    )


def _compose_intent_paragraph(intent_count: int, visitors_today: int) -> str:
    if intent_count <= 0:
        return "No high-intent signals have surfaced yet — those usually pick up in the afternoon."
    pct_intent = (intent_count / max(visitors_today, 1)) * 100
    return (
        f"{intent_count} of them showed real purchase intent "
        f"({pct_intent:.0f}% of traffic)."
    )


def _compose_action_paragraph(
    nudges_fired: int, orders_today: int, revenue_today: float, currency: str | None,
) -> str:
    """4-branch action paragraph: both / nudges-only / orders-only / neither."""
    if nudges_fired > 0 and orders_today > 0:
        return (
            f"HedgeSpark has fired {nudges_fired} "
            f"{_plural(nudges_fired, 'nudge', 'nudges')}, "
            f"and you've already closed {orders_today} "
            f"{_plural(orders_today, 'order', 'orders')} "
            f"({format_money(revenue_today, currency, compact=True)})."
        )
    if nudges_fired > 0:
        return (
            f"HedgeSpark has fired {nudges_fired} "
            f"{_plural(nudges_fired, 'nudge', 'nudges')} to recover the ones nearly lost."
        )
    if orders_today > 0:
        return (
            f"You've closed {orders_today} "
            f"{_plural(orders_today, 'order', 'orders')} today "
            f"({format_money(revenue_today, currency, compact=True)})."
        )
    return "No conversions yet today — HedgeSpark is watching for the right moment to act."


def _load_causal_overlay(db: Session, shop: str) -> tuple[dict | None, list[dict], str | None]:
    """Phase Ω causal explainer overlay — never raises (returns None
    on any failure). Returns (why_block, fusion_alerts_top, extra_paragraph)
    where extra_paragraph is the 4th narrative paragraph if a top
    hypothesis exists."""
    try:
        from app.services.causal_explainer import explain
        causal = explain(db, shop)
    except Exception as exc:
        log.warning("daily_narrative: causal explainer failed: %s", exc)
        _record_query_fail("causal")
        return None, [], None

    fusion_alerts_top = (causal.get("fusion_alerts") or [])[:3]
    hypotheses = causal.get("hypotheses") or []
    if not hypotheses:
        return None, fusion_alerts_top, None

    top = hypotheses[0]
    why_block = {
        "label": top.get("label"),
        "confidence": top.get("confidence"),
        "narrative": top.get("narrative"),
        "next_action": causal.get("next_action"),
        "vertical": causal.get("vertical"),
    }
    extra_paragraph = (
        f"Why: {top.get('narrative')} "
        f"Next step — {causal.get('next_action')}"
    )
    return why_block, fusion_alerts_top, extra_paragraph


def _compute_narrative(db: Session, shop: str) -> dict:
    """Compose the merchant's daily-narrative response.

    Refactored 2026-05-13 (A3 close): 213-LOC god function → 30-LOC
    composer + 10 pure helpers.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_ms = int(start_of_day.timestamp() * 1000)
    currency = get_shop_currency(db, shop)

    visitors_today = _fetch_visitors_today(db, shop, start_of_day_ms)
    intent_count = _fetch_intent_count(db, shop, start_of_day)
    nudges_fired = _fetch_nudges_fired(db, shop, start_of_day)
    orders_today, revenue_today = _fetch_orders_today(db, shop, currency, start_of_day)
    top_action = _fetch_top_action(db, shop, start_of_day - timedelta(days=1))

    paragraphs = [
        _compose_visitor_paragraph(visitors_today),
        _compose_intent_paragraph(intent_count, visitors_today),
        _compose_action_paragraph(nudges_fired, orders_today, revenue_today, currency),
    ]

    why_block, fusion_alerts_top, extra_paragraph = _load_causal_overlay(db, shop)
    if extra_paragraph:
        paragraphs.append(extra_paragraph)

    return {
        "shop_domain": shop,
        "headline": f"Here's your store today · {now.strftime('%A %d %b')}",
        "paragraphs": paragraphs,
        "stats": {
            "visitors_today": visitors_today,
            "intent_signals_today": intent_count,
            "nudges_fired_today": nudges_fired,
            "orders_today": orders_today,
            "revenue_today_eur": round(revenue_today, 2),
        },
        "top_next_action": top_action,
        "why": why_block,
        "fusion_alerts": fusion_alerts_top,
        "currency": currency or "USD",
        "generated_at": now.isoformat(),
    }


@router.get("/daily-narrative", response_model=DailyNarrativeResponse)
def get_daily_narrative(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),
):
    data = _compute_narrative(db, shop)
    return DailyNarrativeResponse(**data)
