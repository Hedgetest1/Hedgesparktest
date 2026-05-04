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


def _compute_narrative(db: Session, shop: str) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    currency = get_shop_currency(db, shop)
    # events.timestamp is epoch milliseconds (NOT a `ts` column)
    start_of_day_ms = int(start_of_day.timestamp() * 1000)

    # --- Visitors today ---
    try:
        visitors_today = int(
            db.execute(
                sql_text(
                    """
                    SELECT COUNT(DISTINCT visitor_id) FROM events
                    WHERE shop_domain = :shop AND timestamp >= :c_ms
                    """
                ),
                {"shop": shop, "c_ms": start_of_day_ms},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: visitors query failed: %s", exc)
        visitors_today = 0

    # --- Intent signals today ---
    # opportunity_signals has `detected_at` (NOT created_at)
    try:
        intent_count = int(
            db.execute(
                sql_text(
                    """
                    SELECT COUNT(*) FROM opportunity_signals
                    WHERE shop_domain = :shop AND detected_at >= :c
                      AND signal_type IN (
                        'HIGH_ENGAGEMENT_NO_ACTION',
                        'SCROLL_HIGH_NO_CLICK',
                        'HIGH_RETURN_LOW_CONVERSION',
                        'RETURN_VISITOR_INTEREST'
                      )
                    """
                ),
                {"shop": shop, "c": start_of_day},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: intent signals query failed: %s", exc)
        intent_count = 0

    # --- Nudges fired today ---
    # nudge_events uses `created_at` (NOT ts)
    try:
        nudges_fired = int(
            db.execute(
                sql_text(
                    """
                    SELECT COUNT(*) FROM nudge_events
                    WHERE shop_domain = :shop AND created_at >= :c
                      AND event_type = 'nudge_impression'
                    """
                ),
                {"shop": shop, "c": start_of_day},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: nudges fired query failed: %s", exc)
        nudges_fired = 0

    # --- Orders today ---
    try:
        orders_today = int(
            db.execute(
                sql_text(
                    """
                    SELECT COUNT(*) FROM shop_orders
                    WHERE shop_domain = :shop AND created_at >= :c
                    """
                ),
                {"shop": shop, "c": start_of_day},
            ).scalar()
            or 0
        )
        revenue_today = float(
            db.execute(
                sql_text(
                    """
                    SELECT COALESCE(SUM(total_price), 0) FROM shop_orders
                    WHERE shop_domain = :shop AND created_at >= :c
                      AND (:currency IS NULL OR currency = :currency)
                    """
                ),
                {"shop": shop, "c": start_of_day, "currency": currency},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("daily_narrative: orders query failed: %s", exc)
        orders_today = 0
        revenue_today = 0.0

    # --- Top next action (highest-priority untaken) ---
    # Columns: detected_at (not created_at), signal_strength (not strength)
    top_action: str | None = None
    try:
        row = db.execute(
            sql_text(
                """
                SELECT product_url, signal_type FROM opportunity_signals
                WHERE shop_domain = :shop AND detected_at >= :c
                ORDER BY signal_strength DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"shop": shop, "c": start_of_day - timedelta(days=1)},
        ).fetchone()
        if row:
            product_url = (row[0] or "").replace("/products/", "")[:60] or "your top product"
            stype = row[1] or "signal"
            stype_human = stype.replace("_", " ").lower()
            top_action = f"{product_url} is showing {stype_human}"
    except Exception as exc:
        log.warning("daily_narrative: top action query failed: %s", exc)

    # --- Compose narrative (deterministic, human-voiced) ---
    def _plural(n: int, singular: str, plural: str) -> str:
        return singular if n == 1 else plural

    p1 = (
        f"So far today, {visitors_today} "
        f"{_plural(visitors_today, 'person has visited', 'people have visited')} your store."
        if visitors_today > 0
        else "Today is quiet — no visitors logged yet. Your tracker is listening."
    )

    if intent_count > 0:
        pct_intent = (intent_count / max(visitors_today, 1)) * 100
        p2 = (
            f"{intent_count} of them showed real purchase intent "
            f"({pct_intent:.0f}% of traffic)."
        )
    else:
        p2 = "No high-intent signals have surfaced yet — those usually pick up in the afternoon."

    if nudges_fired > 0 and orders_today > 0:
        p3 = (
            f"HedgeSpark has fired {nudges_fired} "
            f"{_plural(nudges_fired, 'nudge', 'nudges')}, "
            f"and you've already closed {orders_today} "
            f"{_plural(orders_today, 'order', 'orders')} "
            f"({format_money(revenue_today, currency, compact=True)})."
        )
    elif nudges_fired > 0:
        p3 = (
            f"HedgeSpark has fired {nudges_fired} "
            f"{_plural(nudges_fired, 'nudge', 'nudges')} to recover the ones nearly lost."
        )
    elif orders_today > 0:
        p3 = (
            f"You've closed {orders_today} "
            f"{_plural(orders_today, 'order', 'orders')} today "
            f"({format_money(revenue_today, currency, compact=True)})."
        )
    else:
        p3 = "No conversions yet today — HedgeSpark is watching for the right moment to act."

    paragraphs = [p1, p2, p3]

    # --- Phase Ω: layer in causal explainer + fusion alerts ---
    why_block: dict | None = None
    fusion_alerts_top: list[dict] = []
    try:
        from app.services.causal_explainer import explain
        causal = explain(db, shop)
        if causal.get("hypotheses"):
            top = causal["hypotheses"][0]
            why_block = {
                "label": top.get("label"),
                "confidence": top.get("confidence"),
                "narrative": top.get("narrative"),
                "next_action": causal.get("next_action"),
                "vertical": causal.get("vertical"),
            }
            # Append a fourth paragraph that names the leading cause
            paragraphs.append(
                f"Why: {top.get('narrative')} "
                f"Next step — {causal.get('next_action')}"
            )
        fusion_alerts_top = (causal.get("fusion_alerts") or [])[:3]
    except Exception as exc:
        # Never block the digest on a causal failure
        log.warning("daily_narrative: causal explainer failed: %s", exc)

    headline = f"Here's your store today · {now.strftime('%A %d %b')}"

    return {
        "shop_domain": shop,
        "headline": headline,
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
