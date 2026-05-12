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
    # Shop's native currency — all `_eur`-suffixed money fields above
    # are in this currency (USD/EUR/GBP/…). Historical name.
    currency: str = "USD"
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


# ---------------------------------------------------------------------------
# Z-test SQL fragments — extracted to avoid copy-paste across all CVR-lift
# computations. The math: for each autonomous_action with valid holdout,
# compute pooled CVR p_pool, then two-tailed z-test for proportions; only
# include actions with z > 1.96 (p < 0.05). Visitor-lift = (treatment_cvr
# - control_cvr) × total_measured.
# ---------------------------------------------------------------------------
_SIG_CTE_COLUMNS = """
    treatment_cvr, control_cvr, visitors_measured,
    GREATEST(visitors_measured * holdout_pct / 100.0, 1) AS n_ctrl,
    GREATEST(visitors_measured * (100 - holdout_pct) / 100.0, 1) AS n_treat,
    (treatment_cvr * visitors_measured * (100 - holdout_pct) / 100.0
     + control_cvr * visitors_measured * holdout_pct / 100.0)
    / GREATEST(visitors_measured, 1) AS p_pool
"""

_SIG_BASE_WHERE = """
    shop_domain = :shop
    AND outcome IN ('win', 'measured')
    AND treatment_cvr IS NOT NULL AND control_cvr IS NOT NULL
    AND control_cvr > 0 AND visitors_measured > 0
    AND holdout_pct > 0 AND holdout_pct < 100
"""

_SIG_Z_FILTER = """
    p_pool > 0 AND p_pool < 1
    AND ABS(treatment_cvr - control_cvr)
        / SQRT(p_pool * (1.0 - p_pool) * (1.0/n_treat + 1.0/n_ctrl))
        > 1.96
"""


def _window_clause(since, until, params: dict) -> str:
    """Compose an SQL fragment for measurement_end window. Mutates
    `params` in place with `:since` / `:until` bindings as needed."""
    parts = []
    if since is not None:
        parts.append("AND measurement_end >= :since")
        params["since"] = since
    if until is not None:
        parts.append("AND measurement_end <  :until")
        params["until"] = until
    return " ".join(parts)


def _significant_cvr_lift_visitors(
    db: Session, shop: str, *, since=None, until=None
) -> float:
    """Sum of (treatment_cvr - control_cvr) × measured_visitors across all
    autonomous_actions with statistically significant lift (p < 0.05 via
    2-tailed z-test). Window-restricted by `since`/`until` on
    measurement_end if provided; all-time otherwise."""
    params: dict = {"shop": shop}
    window = _window_clause(since, until, params)
    try:
        row = db.execute(
            sql_text(f"""
                WITH sig AS (
                    SELECT {_SIG_CTE_COLUMNS}
                    FROM autonomous_actions
                    WHERE {_SIG_BASE_WHERE}
                      {window}
                )
                SELECT COALESCE(SUM(
                    (treatment_cvr - control_cvr) * (n_treat + n_ctrl)
                ), 0)
                FROM sig WHERE {_SIG_Z_FILTER}
            """),
            params,
        ).scalar()
        return float(row or 0)
    except Exception as exc:
        log.warning("roi_hero: cvr lift query failed: %s", exc)
        return 0.0


def _aov_for_shop(
    db: Session, shop: str, currency: str | None, *, days: int | None = 30
) -> float:
    """Average shop_orders.total_price for `shop`, optionally restricted
    to the last `days` of orders. Returns 50.0 if no data — same
    fallback the four prior copy-pasted blocks used."""
    if days is not None:
        where_window = "AND created_at >= NOW() - (:days || ' days')::INTERVAL"
        params = {"shop": shop, "currency": currency, "days": str(days)}
    else:
        where_window = ""
        params = {"shop": shop, "currency": currency}
    try:
        row = db.execute(
            sql_text(f"""
                SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 50)
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND (:currency IS NULL OR currency = :currency)
                  {where_window}
            """),
            params,
        ).scalar()
        return float(row or 50)
    except Exception as exc:
        log.warning("roi_hero: aov lookup failed: %s", exc)
        return 50.0


def _eur_from_lift(
    db: Session, shop: str, currency: str | None, *,
    since=None, until=None, aov_days: int | None = 30,
) -> float:
    """Visitor-lift × shop AOV → € savings. Composes the two helpers."""
    visitors = _significant_cvr_lift_visitors(db, shop, since=since, until=until)
    if visitors <= 0:
        return 0.0
    aov = _aov_for_shop(db, shop, currency, days=aov_days)
    return round(visitors * aov, 2)


def _trust_savings(db: Session, shop: str, *, since=None) -> float:
    """Sum of trust_execution_log.revenue_delta_eur > 0 for `shop`,
    optionally restricted to executed_at >= since."""
    where_window = "AND executed_at >= :since" if since is not None else ""
    params = {"shop": shop}
    if since is not None:
        params["since"] = since
    try:
        row = db.execute(
            sql_text(f"""
                SELECT COALESCE(SUM(revenue_delta_eur), 0)
                FROM trust_execution_log
                WHERE shop_domain = :shop
                  AND revenue_delta_eur > 0
                  {where_window}
            """),
            params,
        ).scalar()
        return float(row or 0)
    except Exception as exc:
        log.warning("roi_hero: trust contract query failed: %s", exc)
        return 0.0


def _rars_prevented_recent(shop: str) -> float:
    """Most-recent prevented_eur_this_month from RARS history (Redis).
    Returns 0.0 if no history or no prevented entries."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("roi_hero.rars_history")
            return 0.0
        raw = rc.get(f"hs:rars_history:v1:{shop}")
        if not raw:
            return 0.0
        history = json.loads(raw)
        prevented = [h for h in history if h.get("prevented_eur_this_month")]
        if not prevented:
            return 0.0
        return float(prevented[-1].get("prevented_eur_this_month") or 0)
    except Exception as exc:
        log.warning("roi_hero: rars history lookup failed: %s", exc)
        return 0.0


def _top_win(
    db: Session, shop: str, currency: str | None, *, since
) -> dict | None:
    """Single biggest holdout-significant action since `since`.
    AOV-converted to €. Returns None if no significant action."""
    params: dict = {"shop": shop}
    window = _window_clause(since, None, params)
    try:
        row = db.execute(
            sql_text(f"""
                WITH sig AS (
                    SELECT
                        action_type, product_url, measurement_end,
                        {_SIG_CTE_COLUMNS}
                    FROM autonomous_actions
                    WHERE {_SIG_BASE_WHERE}
                      {window}
                )
                SELECT
                    action_type, product_url, measurement_end,
                    (treatment_cvr - control_cvr) * (n_treat + n_ctrl) AS lift_visitors
                FROM sig WHERE {_SIG_Z_FILTER}
                ORDER BY lift_visitors DESC
                LIMIT 1
            """),
            params,
        ).fetchone()
        if not row or float(row[3] or 0) <= 0:
            return None
        aov = _aov_for_shop(db, shop, currency, days=30)
        product = (row[1] or "").replace("/products/", "") or "your store"
        return {
            "title": f"{row[0]} on {product[:40]}",
            "amount_eur": round(float(row[3]) * aov, 2),
            "narrative": "Biggest single win in the last 30 days",
            "when": row[2].isoformat() if row[2] else "",
        }
    except Exception as exc:
        log.warning("roi_hero: top win query failed: %s", exc)
        return None


def _plan_cost_monthly(db: Session, shop: str) -> float:
    """Plan price: 49 for Lite (default), 99 for Pro."""
    try:
        row = db.execute(
            sql_text("SELECT plan FROM merchants WHERE shop_domain = :s LIMIT 1"),
            {"s": shop},
        ).fetchone()
        if row and row[0] == "pro":
            return 99.0
    except Exception as exc:
        log.warning("roi_hero: plan cost lookup failed: %s", exc)
    return 49.0


def _build_breakdown(
    saved_actions: float, saved_trust: float, prevented_rars: float
) -> list[dict]:
    """Compose the breakdown list — only include non-zero sources."""
    items: list[dict] = []
    if saved_actions > 0:
        items.append({
            "source": "nudge_lift",
            "amount_eur": saved_actions,
            "description": "Revenue lift from holdout-measured nudges",
            "icon": "💬",
        })
    if saved_trust > 0:
        items.append({
            "source": "delegated_autonomy",
            "amount_eur": saved_trust,
            "description": "Revenue from autonomous actions under trust contracts",
            "icon": "🛡️",
        })
    if prevented_rars > 0:
        items.append({
            "source": "rars_prevented",
            "amount_eur": prevented_rars,
            "description": "Losses prevented by early risk detection",
            "icon": "🎯",
        })
    return items


def _headline_message(total_30d: float, roi_ratio: float) -> str:
    """Human-readable tagline based on ROI ratio bands."""
    if total_30d <= 0:
        return "HedgeSpark is collecting your data — savings start this week."
    if roi_ratio >= 20:
        return f"You're getting {roi_ratio:.0f}× your HedgeSpark subscription back. Wild."
    if roi_ratio >= 5:
        return f"HedgeSpark has saved you {roi_ratio:.1f}× its cost this month."
    if roi_ratio >= 1:
        return f"You're already in the black: {roi_ratio:.1f}× your subscription."
    return "HedgeSpark is building the cash machine. Savings are coming online."


def _compute_roi_hero(db: Session, shop: str) -> dict:
    """Compose the ROI hero payload from extracted helpers.

    Refactored 2026-05-12 (A3 close): the original 413-LOC function
    inlined the same CVR z-test SQL 4 times + the same AOV lookup 4
    times. This composer drives each section as a single call to a
    pure helper; adding/removing a source = edit the composer, not
    a 413-LOC body.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    c_7d = now - timedelta(days=7)
    c_14d = now - timedelta(days=14)
    c_30d = now - timedelta(days=30)
    currency = get_shop_currency(db, shop)

    # 30d sources
    saved_30d_actions = _eur_from_lift(db, shop, currency, since=c_30d)
    saved_30d_trust = _trust_savings(db, shop, since=c_30d)
    prevented_rars_30d = _rars_prevented_recent(shop)
    total_30d = saved_30d_actions + saved_30d_trust + prevented_rars_30d

    # 7d momentum (current vs prior week)
    saved_7d = _eur_from_lift(db, shop, currency, since=c_7d)
    saved_prior_7d = _eur_from_lift(db, shop, currency, since=c_14d, until=c_7d)
    delta_pct: float | None = None
    if saved_prior_7d > 0:
        delta_pct = ((saved_7d - saved_prior_7d) / saved_prior_7d) * 100.0

    # All-time observational (uses all-orders AOV, not 30d-window)
    saved_all_time_actions = _eur_from_lift(db, shop, currency, aov_days=None)
    saved_all_time_trust = _trust_savings(db, shop)
    total_all_time = max(saved_all_time_actions + saved_all_time_trust, total_30d)

    plan_cost = _plan_cost_monthly(db, shop)
    roi_ratio = (total_30d / plan_cost) if plan_cost > 0 else 0.0

    return {
        "shop_domain": shop,
        "total_saved_eur_30d": round(total_30d, 2),
        "total_saved_eur_7d": round(saved_7d, 2),
        "total_saved_eur_all_time": round(total_all_time, 2),
        "delta_7d_vs_prior_pct": round(delta_pct, 1) if delta_pct is not None else None,
        "breakdown": _build_breakdown(saved_30d_actions, saved_30d_trust, prevented_rars_30d),
        "top_win": _top_win(db, shop, currency, since=c_30d),
        "plan_cost_eur_monthly": plan_cost,
        "roi_ratio": round(roi_ratio, 2),
        "headline_message": _headline_message(total_30d, roi_ratio),
        "currency": currency or "USD",
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
