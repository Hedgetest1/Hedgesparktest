"""
roi_hero.py — THE dashboard headline endpoint.

Returns the single big number that makes the merchant feel HedgeSpark is
worth every cent: holdout-proven revenue saved this month, this week,
all-time. Plus the trend arrow + composition breakdown.

Endpoint
--------
GET /pro/roi-hero
    Returns:
      total_saved_eur_30d       : float
      total_saved_eur_7d        : float
      total_saved_eur_all_time  : float
      delta_7d_vs_prior_pct     : float | None  (+X% more than prior 7d)
      breakdown: [
        { source: str, amount_eur: float, description: str, icon: str }
      ]
      top_win: { title, amount_eur, narrative, when } | None
      plan_cost_eur_monthly     : float  (for ROI ratio computation)
      roi_ratio                 : float  (savings / plan_cost, >1 means profitable)
      headline_message          : str    (human-readable tagline)

No LLM. Deterministic. 5-minute Redis cache (per shop).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["roi_hero"])




class ROIBreakdownItem(BaseModel):
    source: str
    amount_eur: float
    description: str
    icon: str


class ROITopWin(BaseModel):
    title: str
    amount_eur: float
    narrative: str
    when: str  # ISO string


class ROIHeroResponse(BaseModel):
    shop_domain: str
    total_saved_eur_30d: float
    total_saved_eur_7d: float
    total_saved_eur_all_time: float
    delta_7d_vs_prior_pct: float | None
    breakdown: list[ROIBreakdownItem]
    top_win: ROITopWin | None
    plan_cost_eur_monthly: float
    roi_ratio: float
    headline_message: str
    generated_at: str


_CACHE_TTL_S = 300  # 5 minutes
_CACHE_PREFIX = "hs:roi_hero"


def _cache_get(shop: str) -> dict | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("roi_hero.cache_read")
            return None
        raw = rc.get(f"{_CACHE_PREFIX}:{shop}")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        log.warning("roi_hero: cache read failed: %s", exc)
        return None


def _cache_set(shop: str, data: dict) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("roi_hero.cache_write")
            return
        rc.setex(f"{_CACHE_PREFIX}:{shop}", _CACHE_TTL_S, json.dumps(data, default=str))
    except Exception as exc:
        log.warning("roi_hero: cache write failed: %s", exc)


def _compute_roi_hero(db: Session, shop: str) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    c_7d = now - timedelta(days=7)
    c_14d = now - timedelta(days=14)
    c_30d = now - timedelta(days=30)
    currency = get_shop_currency(db, shop)

    breakdown: list[dict] = []

    # --- 1. Nudge lift from autonomous_actions (holdout-measured) ---
    # autonomous_actions is the real source for holdout-measured nudge
    # lift: treatment_cvr vs control_cvr → lift_pct → revenue impact.
    # (The former query scanned action_outcomes for revenue_delta_eur
    # columns that don't exist there — that was a ghost table schema.)
    saved_30d_actions = 0.0
    try:
        row = db.execute(
            sql_text(
                """
                SELECT
                    COALESCE(SUM(
                        CASE
                            WHEN treatment_cvr IS NOT NULL
                             AND control_cvr IS NOT NULL
                             AND control_cvr > 0
                             AND visitors_measured > 0
                            THEN (treatment_cvr - control_cvr) * visitors_measured
                            ELSE 0
                        END
                    ), 0) AS cvr_lift_visitors
                FROM autonomous_actions
                WHERE shop_domain = :shop
                  AND outcome IN ('win', 'measured')
                  AND measurement_end >= :cutoff
                """
            ),
            {"shop": shop, "cutoff": c_30d},
        ).fetchone()
        # Multiply by shop AOV to get €; fall back to 50 if unknown
        lift_visitors = float(row[0] or 0) if row else 0.0
        if lift_visitors > 0:
            try:
                aov_row = db.execute(
                    sql_text(
                        """
                        SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 50)
                        FROM shop_orders
                        WHERE shop_domain = :shop
                          AND created_at >= NOW() - INTERVAL '30 days'
                          AND (:currency IS NULL OR currency = :currency)
                        """
                    ),
                    {"shop": shop, "currency": currency},
                ).scalar()
                aov = float(aov_row or 50)
            except Exception as exc:
                log.warning("roi_hero: aov lookup failed: %s", exc)
                aov = 50.0
            saved_30d_actions = round(lift_visitors * aov, 2)
    except Exception as exc:
        log.warning("roi_hero: nudge lift query failed: %s", exc)
        saved_30d_actions = 0.0

    if saved_30d_actions > 0:
        breakdown.append(
            {
                "source": "nudge_lift",
                "amount_eur": saved_30d_actions,
                "description": "Revenue lift from holdout-measured nudges",
                "icon": "💬",
            }
        )

    # --- 2. Trust Contract auto-executions ---
    try:
        saved_30d_trust = float(
            db.execute(
                sql_text(
                    """
                    SELECT COALESCE(SUM(revenue_delta_eur), 0)
                    FROM trust_execution_log
                    WHERE shop_domain = :shop
                      AND executed_at >= :cutoff
                      AND revenue_delta_eur > 0
                    """
                ),
                {"shop": shop, "cutoff": c_30d},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("roi_hero: trust contract query failed: %s", exc)
        saved_30d_trust = 0.0

    if saved_30d_trust > 0:
        breakdown.append(
            {
                "source": "delegated_autonomy",
                "amount_eur": saved_30d_trust,
                "description": "Revenue from autonomous actions under trust contracts",
                "icon": "🛡️",
            }
        )

    # --- 3. Bugfix + self-heal savings (system-wide prevention) ---
    try:
        from app.services.fix_holdout_measurement import get_weekly_proven_savings
        weekly_system = get_weekly_proven_savings(week_offset=0)
    except Exception as exc:
        log.warning("roi_hero: weekly proven savings failed: %s", exc)
        weekly_system = 0.0

    # --- 4. RARS prevented — from RARS history ---
    prevented_rars_30d = 0.0
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(f"hs:rars_history:v1:{shop}")
            if raw:
                history = json.loads(raw)
                prevented_entries = [
                    h for h in history if h.get("prevented_eur_this_month")
                ]
                if prevented_entries:
                    prevented_rars_30d = float(
                        prevented_entries[-1].get("prevented_eur_this_month") or 0
                    )
    except Exception as exc:
        log.warning("roi_hero: rars history lookup failed: %s", exc)

    if prevented_rars_30d > 0:
        breakdown.append(
            {
                "source": "rars_prevented",
                "amount_eur": prevented_rars_30d,
                "description": "Losses prevented by early risk detection",
                "icon": "🎯",
            }
        )

    # --- 5. 7d savings (current vs previous week, for delta trend) ---
    # Re-use the same autonomous_actions → CVR lift → € formula for two
    # windows so the delta badge reflects real week-over-week momentum.
    def _cvr_lift_eur_window(c_start, c_end=None):
        try:
            where = "AND measurement_end >= :start"
            params = {"shop": shop, "start": c_start}
            if c_end is not None:
                where += " AND measurement_end < :end"
                params["end"] = c_end
            row = db.execute(
                sql_text(
                    f"""
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN treatment_cvr IS NOT NULL
                             AND control_cvr IS NOT NULL
                             AND control_cvr > 0
                             AND visitors_measured > 0
                            THEN (treatment_cvr - control_cvr) * visitors_measured
                            ELSE 0
                        END
                    ), 0)
                    FROM autonomous_actions
                    WHERE shop_domain = :shop
                      AND outcome IN ('win', 'measured')
                      {where}
                    """
                ),
                params,
            ).scalar()
            lift_visitors = float(row or 0)
            if lift_visitors <= 0:
                return 0.0
            aov = float(
                db.execute(
                    sql_text(
                        """
                        SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 50)
                        FROM shop_orders
                        WHERE shop_domain = :shop
                          AND created_at >= NOW() - INTERVAL '30 days'
                          AND (:currency IS NULL OR currency = :currency)
                        """
                    ),
                    {"shop": shop, "currency": currency},
                ).scalar()
                or 50
            )
            return round(lift_visitors * aov, 2)
        except Exception as exc:
            log.warning("roi_hero: cvr lift window query failed: %s", exc)
            return 0.0

    saved_7d = _cvr_lift_eur_window(c_7d)
    saved_prior_7d = _cvr_lift_eur_window(c_14d, c_7d)

    delta_pct: float | None = None
    if saved_prior_7d > 0:
        delta_pct = ((saved_7d - saved_prior_7d) / saved_prior_7d) * 100.0

    total_30d = saved_30d_actions + saved_30d_trust + prevented_rars_30d

    # --- 6. All-time total (observational — cheaper query) ---
    # autonomous_actions is the canonical measured-nudge-lift source.
    # The former query used action_outcomes.revenue_delta_eur which
    # never existed on that table.
    try:
        row = db.execute(
            sql_text(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN treatment_cvr IS NOT NULL
                         AND control_cvr IS NOT NULL
                         AND control_cvr > 0
                         AND visitors_measured > 0
                        THEN (treatment_cvr - control_cvr) * visitors_measured
                        ELSE 0
                    END
                ), 0)
                FROM autonomous_actions
                WHERE shop_domain = :shop
                  AND outcome IN ('win', 'measured')
                """
            ),
            {"shop": shop},
        ).scalar()
        lift_visitors_all = float(row or 0)
        # Re-use the 30d AOV heuristic for the conversion
        aov_all = 50.0
        try:
            aov_row = db.execute(
                sql_text("""
                    SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 50)
                    FROM shop_orders
                    WHERE shop_domain = :shop
                      AND (:currency IS NULL OR currency = :currency)
                """),
                {"shop": shop, "currency": currency},
            ).scalar()
            aov_all = float(aov_row or 50)
        except Exception as exc:
            log.warning("roi_hero: all-time aov lookup failed: %s", exc)
        total_all_time = round(lift_visitors_all * aov_all, 2)
    except Exception as exc:
        log.warning("roi_hero: all-time total query failed: %s", exc)
        total_all_time = total_30d

    # Trust-contract all-time on top
    try:
        total_all_time_trust = float(
            db.execute(
                sql_text(
                    """
                    SELECT COALESCE(SUM(revenue_delta_eur), 0)
                    FROM trust_execution_log
                    WHERE shop_domain = :shop
                      AND revenue_delta_eur > 0
                    """
                ),
                {"shop": shop},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("roi_hero: all-time trust query failed: %s", exc)
        total_all_time_trust = 0.0

    total_all_time = max(total_all_time + total_all_time_trust, total_30d)

    # --- 7. Top win (single biggest effective action in last 30d) ---
    # Source: autonomous_actions rows with the highest (treatment_cvr -
    # control_cvr) * visitors_measured product. AOV multiplier turns
    # the visitor-lift count into an € estimate.
    top_win_dict: dict | None = None
    try:
        row = db.execute(
            sql_text(
                """
                SELECT
                    action_type,
                    product_url,
                    measurement_end,
                    (treatment_cvr - control_cvr) * visitors_measured AS lift_visitors
                FROM autonomous_actions
                WHERE shop_domain = :shop
                  AND outcome IN ('win', 'measured')
                  AND measurement_end >= :c30
                  AND treatment_cvr IS NOT NULL
                  AND control_cvr IS NOT NULL
                  AND control_cvr > 0
                  AND visitors_measured > 0
                ORDER BY lift_visitors DESC
                LIMIT 1
                """
            ),
            {"shop": shop, "c30": c_30d},
        ).fetchone()
        if row and float(row[3] or 0) > 0:
            aov_for_top = 50.0
            try:
                aov_row = db.execute(
                    sql_text("""
                        SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 50)
                        FROM shop_orders
                        WHERE shop_domain = :shop
                          AND created_at >= NOW() - INTERVAL '30 days'
                          AND (:currency IS NULL OR currency = :currency)
                    """),
                    {"shop": shop, "currency": currency},
                ).scalar()
                aov_for_top = float(aov_row or 50)
            except Exception as exc:
                log.warning("roi_hero: top win aov lookup failed: %s", exc)
            product = (row[1] or "").replace("/products/", "") or "your store"
            top_win_dict = {
                "title": f"{row[0]} on {product[:40]}",
                "amount_eur": round(float(row[3]) * aov_for_top, 2),
                "narrative": "Biggest single win in the last 30 days",
                "when": row[2].isoformat() if row[2] else "",
            }
    except Exception as exc:
        log.warning("roi_hero: top win query failed: %s", exc)

    # --- 8. Plan cost + ROI ratio ---
    plan_cost = 49.0  # default — could look up merchant plan
    try:
        plan_row = db.execute(
            sql_text("SELECT plan FROM merchants WHERE shop_domain = :s LIMIT 1"),
            {"s": shop},
        ).fetchone()
        if plan_row and plan_row[0] == "pro":
            plan_cost = 99.0
    except Exception as exc:
        log.warning("roi_hero: plan cost lookup failed: %s", exc)

    roi_ratio = (total_30d / plan_cost) if plan_cost > 0 else 0.0

    # --- 9. Headline message ---
    if total_30d <= 0:
        headline = "HedgeSpark is collecting your data — savings start this week."
    elif roi_ratio >= 20:
        headline = f"You're getting {roi_ratio:.0f}× your HedgeSpark subscription back. Wild."
    elif roi_ratio >= 5:
        headline = f"HedgeSpark has saved you {roi_ratio:.1f}× its cost this month."
    elif roi_ratio >= 1:
        headline = f"You're already in the black: {roi_ratio:.1f}× your subscription."
    else:
        headline = "HedgeSpark is building the cash machine. Savings are coming online."

    return {
        "shop_domain": shop,
        "total_saved_eur_30d": round(total_30d, 2),
        "total_saved_eur_7d": round(saved_7d, 2),
        "total_saved_eur_all_time": round(total_all_time, 2),
        "delta_7d_vs_prior_pct": round(delta_pct, 1) if delta_pct is not None else None,
        "breakdown": breakdown,
        "top_win": top_win_dict,
        "plan_cost_eur_monthly": plan_cost,
        "roi_ratio": round(roi_ratio, 2),
        "headline_message": headline,
        "generated_at": now.isoformat(),
    }


@router.get("/roi-hero", response_model=ROIHeroResponse)
def get_roi_hero(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1 — analytics read path
):
    cached = _cache_get(shop)
    if cached is not None:
        return ROIHeroResponse(**cached)

    payload = _compute_roi_hero(db, shop)
    _cache_set(shop, payload)
    return ROIHeroResponse(**payload)
