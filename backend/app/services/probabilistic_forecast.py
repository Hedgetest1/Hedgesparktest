"""
probabilistic_forecast.py — Revenue + churn forecasts with 80/95% CI.

Extends the α6 pattern (residual-std-error prediction intervals) to
shop-level revenue and churn metrics. Deterministic, no external ML.

Methods
-------
- Holt-Winters exponential smoothing (double, level + trend) for the
  point forecast
- Residual standard deviation of the fit for interval width
- Normal approximation for 80/95% quantiles
- Horizon inflation ∝ √h_days to widen bands with lookahead

Public API
----------
    forecast_revenue(db, shop, horizon_days=14, window_days=60) -> dict
    forecast_churn(db, shop, horizon_days=30, window_days=90) -> dict

Both return:
    {
        "shop_domain", "method", "horizon_days", "window_days",
        "fitted_values": [...], "observed_values": [...], "dates": [...],
        "forecast_point": float, "forecast_lower_80", "forecast_upper_80",
        "forecast_lower_95", "forecast_upper_95",
        "direction": "rising|falling|stable", "r2": float,
        "confidence": "low|medium|high", "headline": str,
    }

No LLM, no external services.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency, get_shop_timezone

log = logging.getLogger("probabilistic_forecast")

_MIN_POINTS_FOR_FORECAST = 7


# ---------------------------------------------------------------------------
# Holt's double exponential smoothing (level + trend)
# ---------------------------------------------------------------------------

def holt_forecast(
    series: Sequence[float],
    *,
    alpha: float = 0.5,
    beta: float = 0.3,
    horizon: int = 14,
) -> tuple[list[float], list[float]]:
    """Holt's double exponential smoothing.

    Returns (fitted_values, forecast_values). Fitted has len(series)
    values; forecast has `horizon` values.
    """
    n = len(series)
    if n == 0:
        return [], [0.0] * horizon
    if n == 1:
        return [series[0]], [series[0]] * horizon

    level = float(series[0])
    trend = float(series[1] - series[0])

    fitted = [level + trend]
    for t in range(1, n):
        prev_level = level
        level = alpha * series[t] + (1 - alpha) * (prev_level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        fitted.append(level + trend)

    forecast = [level + (h + 1) * trend for h in range(horizon)]
    return fitted, forecast


def _residual_std(observed: Sequence[float], fitted: Sequence[float]) -> float:
    if len(observed) < 2 or len(observed) != len(fitted):
        return 0.0
    residuals = [o - f for o, f in zip(observed, fitted)]
    mean_r = sum(residuals) / len(residuals)
    var = sum((r - mean_r) ** 2 for r in residuals) / max(1, len(residuals) - 1)
    return math.sqrt(max(0.0, var))


def _r_squared(observed: Sequence[float], fitted: Sequence[float]) -> float:
    if len(observed) < 2 or len(observed) != len(fitted):
        return 0.0
    mean_o = sum(observed) / len(observed)
    ss_tot = sum((o - mean_o) ** 2 for o in observed)
    ss_res = sum((o - f) ** 2 for o, f in zip(observed, fitted))
    if ss_tot == 0:
        return 0.0
    return max(0.0, min(1.0, 1 - ss_res / ss_tot))


def _confidence_label(n: int, r2: float) -> str:
    if n < _MIN_POINTS_FOR_FORECAST:
        return "insufficient"
    if n >= 30 and r2 >= 0.6:
        return "high"
    if n >= 14 and r2 >= 0.3:
        return "medium"
    return "low"


def _prediction_interval(
    point: float, sigma: float, horizon_days: int
) -> tuple[float, float, float, float]:
    horizon_factor = max(1.0, horizon_days**0.5)
    std_h = sigma * horizon_factor
    return (
        max(0.0, point - 1.28 * std_h),  # 80% lower
        point + 1.28 * std_h,             # 80% upper
        max(0.0, point - 1.96 * std_h),  # 95% lower
        point + 1.96 * std_h,             # 95% upper
    )


# ---------------------------------------------------------------------------
# Revenue forecast
# ---------------------------------------------------------------------------

def forecast_revenue(
    db: Session,
    shop_domain: str,
    *,
    horizon_days: int = 14,
    window_days: int = 60,
) -> dict:
    """Fetch daily revenue over the last `window_days`, fit Holt, forecast
    `horizon_days` ahead with 80/95% prediction intervals."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=window_days)
    currency = get_shop_currency(db, shop_domain)
    tz = get_shop_timezone(db, shop_domain)

    try:
        rows = db.execute(
            sql_text(
                """
                SELECT date_trunc('day', created_at AT TIME ZONE :tz)::date AS d,
                       COALESCE(SUM(total_price), 0) AS rev
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :since
                  AND (:currency IS NULL OR currency = :currency)
                GROUP BY date_trunc('day', created_at AT TIME ZONE :tz)::date
                ORDER BY d ASC
                """
            ),
            {"shop": shop_domain, "since": since, "currency": currency, "tz": tz},
        ).fetchall()
    except Exception as exc:
        log.warning("forecast: revenue query failed: %s", exc)
        return _empty_forecast(
            shop_domain, "revenue", horizon_days, window_days,
            currency=currency or "USD",
        )

    if len(rows) < _MIN_POINTS_FOR_FORECAST:
        return _empty_forecast(
            shop_domain, "revenue", horizon_days, window_days,
            reason=f"only {len(rows)} days of data",
            currency=currency or "USD",
        )

    dates = [str(r[0]) for r in rows]
    values = [float(r[1] or 0) for r in rows]

    fitted, forecast_vals = holt_forecast(values, horizon=horizon_days)
    sigma = _residual_std(values, fitted)
    r2 = _r_squared(values, fitted)

    # Use the mean of the forecast horizon as the "point estimate" for
    # the dashboard headline.
    point = sum(forecast_vals) / len(forecast_vals)
    l80, u80, l95, u95 = _prediction_interval(point, sigma, horizon_days)

    last_week_mean = sum(values[-7:]) / min(7, len(values))
    delta_pct = (
        ((point - last_week_mean) / last_week_mean * 100)
        if last_week_mean > 0
        else 0.0
    )

    from app.core.currency import format_money
    point_str = format_money(point, currency)
    if delta_pct > 5:
        direction = "rising"
        headline = (
            f"Revenue trending up: next {horizon_days} days projected at "
            f"{point_str}/day (+{delta_pct:.0f}% vs last week's average)."
        )
    elif delta_pct < -5:
        direction = "falling"
        headline = (
            f"Revenue cooling: next {horizon_days} days projected at "
            f"{point_str}/day ({delta_pct:.0f}% vs last week's average)."
        )
    else:
        direction = "stable"
        headline = f"Revenue stable around {point_str}/day."

    return {
        "shop_domain": shop_domain,
        "method": "holt_double_exp",
        "metric": "daily_revenue_eur",
        "horizon_days": horizon_days,
        "window_days": window_days,
        "dates": dates,
        "observed_values": [round(v, 2) for v in values],
        "fitted_values": [round(v, 2) for v in fitted],
        "forecast_values": [round(v, 2) for v in forecast_vals],
        "forecast_point": round(point, 2),
        "forecast_lower_80": round(l80, 2),
        "forecast_upper_80": round(u80, 2),
        "forecast_lower_95": round(l95, 2),
        "forecast_upper_95": round(u95, 2),
        "residual_std": round(sigma, 2),
        "r_squared": round(r2, 3),
        "direction": direction,
        "confidence": _confidence_label(len(values), r2),
        "headline": headline,
        # Shop's native currency — forecast_point + observed_values are
        # in this currency. Dashboard renders with the matching symbol.
        "currency": currency or "USD",
        "generated_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Churn forecast — daily "inactive customers" count
# ---------------------------------------------------------------------------

def forecast_churn(
    db: Session,
    shop_domain: str,
    *,
    horizon_days: int = 30,
    window_days: int = 90,
) -> dict:
    """Forecast how many customers will go silent (no order for 30d)
    in the next `horizon_days` based on recent inactivity trend."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=window_days)

    # Daily "newly silent" count: customers whose most-recent order
    # hits day D and exceeds 30 days of silence afterward.
    # Proxy: count customers whose last order is now in the "30-60 days
    # ago" bucket (they've just crossed the silence line).
    try:
        rows = db.execute(
            sql_text(
                """
                WITH customer_last_order AS (
                    SELECT customer_email, MAX(created_at) AS last_at
                    FROM shop_orders
                    WHERE shop_domain = :shop
                      AND customer_email IS NOT NULL AND customer_email <> ''
                    GROUP BY customer_email
                )
                SELECT DATE(last_at) AS d, COUNT(*) AS churned
                FROM customer_last_order
                WHERE last_at >= :since
                  AND last_at < :cutoff_30d
                GROUP BY DATE(last_at)
                ORDER BY d ASC
                """
            ),
            {
                "shop": shop_domain,
                "since": since,
                "cutoff_30d": now - timedelta(days=30),
            },
        ).fetchall()
    except Exception as exc:
        log.warning("forecast: churn query failed: %s", exc)
        return _empty_forecast(shop_domain, "churn", horizon_days, window_days)

    if len(rows) < _MIN_POINTS_FOR_FORECAST:
        return _empty_forecast(
            shop_domain, "churn", horizon_days, window_days,
            reason=f"only {len(rows)} days of data",
        )

    dates = [str(r[0]) for r in rows]
    values = [float(r[1] or 0) for r in rows]

    fitted, forecast_vals = holt_forecast(values, horizon=horizon_days)
    sigma = _residual_std(values, fitted)
    r2 = _r_squared(values, fitted)
    point = sum(forecast_vals) / len(forecast_vals)
    l80, u80, l95, u95 = _prediction_interval(point, sigma, horizon_days)

    # Clamp to non-negative — churn count can't go below zero
    point = max(0.0, point)
    forecast_vals = [max(0.0, v) for v in forecast_vals]
    total_projected = point * horizon_days
    last_week_mean = sum(values[-7:]) / min(7, len(values))
    delta_pct = (
        ((point - last_week_mean) / last_week_mean * 100)
        if last_week_mean > 0
        else 0.0
    )

    if delta_pct > 10:
        direction = "worsening"
        headline = (
            f"Churn rising: ~{round(total_projected)} customers "
            f"projected to go silent in the next {horizon_days} days "
            f"(+{delta_pct:.0f}% vs last week)."
        )
    elif delta_pct < -10:
        direction = "improving"
        headline = (
            f"Churn cooling: ~{round(total_projected)} customers "
            f"projected to go silent in the next {horizon_days} days "
            f"({delta_pct:.0f}% vs last week)."
        )
    else:
        direction = "stable"
        headline = (
            f"Churn stable: ~{round(total_projected)} customers projected "
            f"to go silent in the next {horizon_days} days."
        )

    return {
        "shop_domain": shop_domain,
        "method": "holt_double_exp",
        "metric": "daily_newly_silent_customers",
        "horizon_days": horizon_days,
        "window_days": window_days,
        "dates": dates,
        "observed_values": [round(v, 2) for v in values],
        "fitted_values": [round(v, 2) for v in fitted],
        "forecast_values": [round(v, 2) for v in forecast_vals],
        "forecast_point": round(point, 2),
        "forecast_lower_80": round(l80, 2),
        "forecast_upper_80": round(u80, 2),
        "forecast_lower_95": round(l95, 2),
        "forecast_upper_95": round(u95, 2),
        "residual_std": round(sigma, 2),
        "r_squared": round(r2, 3),
        "direction": direction,
        "confidence": _confidence_label(len(values), r2),
        "headline": headline,
        "total_projected_churn": round(total_projected),
        "generated_at": now.isoformat(),
    }


def _empty_forecast(
    shop: str, metric: str, horizon: int, window: int, reason: str = "insufficient_data",
    currency: str = "USD",
) -> dict:
    return {
        "shop_domain": shop,
        "method": "holt_double_exp",
        "metric": metric,
        "horizon_days": horizon,
        "window_days": window,
        "status": "insufficient_data",
        "reason": reason,
        "headline": "Not enough history to forecast — check back in a few days.",
        "dates": [],
        "observed_values": [],
        "fitted_values": [],
        "forecast_values": [],
        "forecast_point": 0.0,
        "forecast_lower_80": 0.0,
        "forecast_upper_80": 0.0,
        "forecast_lower_95": 0.0,
        "forecast_upper_95": 0.0,
        "residual_std": 0.0,
        "r_squared": 0.0,
        "direction": "stable",
        "confidence": "insufficient",
        # Keep the response_shape stable across happy + empty paths so the
        # dashboard can always read `currency` without optional-chaining.
        "currency": currency,
    }


# ============================================================================
# Per-SKU forecast — Gap #6 close (brutal $0-70 audit + parity doctrine)
# ============================================================================
#
# Lebesgue $59 + Forthcast $19.99 ship per-product demand forecasts at entry
# tier. Per founder parity doctrine 2026-04-27: every $0-60 competitor
# feature → we build, with clarity + accuracy + unique-feature on top.
#
# Architecture: REUSES holt_forecast + _residual_std + _r_squared + _prediction
# _interval helpers (all pure functions). Adds a top-N-by-revenue selector,
# runs the forecast pipeline per product, returns ranked list.
#
# Differentiator on top (parity doctrine §3 — unique-feature):
#   - biggest_riser / biggest_faller plain-language insight panel
#   - per-product confidence label (high/medium/low/insufficient) so
#     merchants don't trust a forecast on 3 days of data
#   - backtest accuracy_pct = 100 - mean(|residual| / observed) — single
#     scalar so the merchant can read "this forecast nails ~83% of days"
#     no $0-60 competitor surfaces this honestly

from sqlalchemy import text as _sql_text_pbsku


# ---------------------------------------------------------------------------
# forecast_by_sku — stage helpers
# Refactor 2026-05-13 (A3 close): 286-LOC god function → composer + 8 pure
# stage helpers. SQL byte-identical (CTE+LATERAL pre-filter pattern
# preserved, ANY(:pkeys) batched query preserved). Per-product math is
# byte-identical (Holt+sigma+r2+CI+accuracy).
# ---------------------------------------------------------------------------


def _clamp_sku_forecast_params(
    horizon_days: int, window_days: int, top_n: int,
) -> tuple[int, int, int]:
    """Param-clamping: forecast bounds are conservative on purpose so a
    bad request can't poison the planner with absurd ranges."""
    return (
        max(1, min(horizon_days, 60)),
        max(7, min(window_days, 365)),
        max(1, min(top_n, 25)),
    )


_TOP_PRODUCTS_SQL = _sql_text_pbsku("""
    WITH valid_orders AS (
        SELECT id, line_items
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :since
          AND (:currency IS NULL OR currency = :currency)
          AND line_items IS NOT NULL
          AND CASE WHEN jsonb_typeof(line_items) = 'array'
                   THEN jsonb_array_length(line_items) > 0
                   ELSE FALSE
              END
    )
    SELECT
        COALESCE(item->>'product_id', item->>'product_url') AS product_key,
        COALESCE(NULLIF(item->>'title', ''), '(untitled)') AS title,
        SUM((item->>'price')::numeric * (item->>'quantity')::int) AS revenue
    FROM valid_orders vo,
         jsonb_array_elements(vo.line_items) AS item
    WHERE item->>'price' IS NOT NULL
      AND item->>'quantity' IS NOT NULL
      AND COALESCE(item->>'product_id', item->>'product_url') IS NOT NULL
    GROUP BY 1, 2
    ORDER BY revenue DESC
    LIMIT :top_n
""")


_DAILY_SERIES_SQL = _sql_text_pbsku("""
    WITH valid_orders AS (
        SELECT created_at, line_items
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :since
          AND (:currency IS NULL OR currency = :currency)
          AND line_items IS NOT NULL
          AND CASE WHEN jsonb_typeof(line_items) = 'array'
                   THEN jsonb_array_length(line_items) > 0
                   ELSE FALSE
              END
    )
    SELECT
        COALESCE(item->>'product_id', item->>'product_url') AS pkey,
        date_trunc('day', vo.created_at AT TIME ZONE :tz)::date AS d,
        COALESCE(SUM(
            (item->>'price')::numeric * (item->>'quantity')::int
        ), 0) AS rev
    FROM valid_orders vo,
         jsonb_array_elements(vo.line_items) AS item
    WHERE COALESCE(item->>'product_id', item->>'product_url') = ANY(:pkeys)
      AND item->>'price' IS NOT NULL
      AND item->>'quantity' IS NOT NULL
    GROUP BY pkey, date_trunc('day', vo.created_at AT TIME ZONE :tz)::date
    ORDER BY pkey, d ASC
""")


def _fetch_top_products(
    db: Session, shop_domain: str, since: datetime,
    currency: str, top_n: int,
) -> list[tuple]:
    """Top-N products by window revenue. Returns [] on query failure
    (logged as warning, NOT swallowed silently); the composer turns
    [] into the empty-state response.

    CRITICAL: the WITH valid_orders CTE pre-filters by
    jsonb_typeof(line_items)='array' BEFORE the LATERAL
    jsonb_array_elements join. PostgreSQL's planner can evaluate the
    LATERAL on rows the WHERE clause would otherwise reject, which
    panics with "cannot extract elements from a scalar". The CTE+JOIN
    form forces strict ordering: only array-typed rows reach LATERAL.
    Regression-pinned by test_handles_json_null_line_items.
    """
    try:
        return db.execute(_TOP_PRODUCTS_SQL, {
            "shop": shop_domain, "since": since,
            "currency": currency, "top_n": top_n,
        }).fetchall()
    except Exception as exc:
        log.warning("forecast_by_sku: top-products query failed: %s", exc)
        return []


def _fetch_daily_series(
    db: Session, shop_domain: str, since: datetime,
    currency: str, tz: str, pkeys: list[str],
) -> dict[str, list[tuple]]:
    """Daily revenue series for the top-N products in ONE batched query.

    Was N+1: 1 outer top-products + N per-product daily queries (each
    with its own jsonb_array_elements LATERAL pre-filter). Now: 1 outer
    + 1 batched = 2 round-trips constant regardless of N. Uses
    ANY(:pkeys) filter + same CTE pre-filter as the per-product form.

    Returns {pkey: []} for every requested pkey when the query fails —
    that surfaces as the per-product "insufficient" branch downstream
    (no false zeroes injected into the forecast pipeline).
    """
    out: dict[str, list[tuple]] = {pkey: [] for pkey in pkeys}
    if not pkeys:
        return out
    try:
        rows = db.execute(_DAILY_SERIES_SQL, {
            "shop": shop_domain, "since": since, "currency": currency,
            "tz": tz, "pkeys": pkeys,
        }).fetchall()
        for r in rows:
            out.setdefault(r[0], []).append((r[1], r[2]))
    except Exception as exc:
        log.warning("forecast_by_sku: batched daily query failed: %s", exc)
    return out


def _insufficient_product_record(
    product_key: str, title: str, observed_revenue: float, n_days: int,
) -> dict:
    """The honest-cold-start record. Returns forecast=0 + 'insufficient'
    confidence — no fabricated point or interval."""
    return {
        "product_key": str(product_key)[:128],
        "title": title[:128],
        "observed_revenue": observed_revenue,
        "forecast_point": 0.0,
        "forecast_lower_80": 0.0,
        "forecast_upper_80": 0.0,
        "forecast_lower_95": 0.0,
        "forecast_upper_95": 0.0,
        "delta_pct": 0.0,
        "direction": "stable",
        "confidence": "insufficient",
        "accuracy_pct": None,
        "n_days": n_days,
        "r2": 0.0,
    }


def _classify_direction(delta_pct: float) -> str:
    """±5% deadband → 'stable'; outside → 'rising' / 'falling'."""
    if delta_pct > 5:
        return "rising"
    if delta_pct < -5:
        return "falling"
    return "stable"


def _compute_accuracy_pct(
    values: list[float], fitted: list[float],
) -> float | None:
    """Backtest accuracy = 100 - mean(|residual| / observed) %.
    Returns None when there's no positive observation to anchor on
    (mean-absolute-percentage-error needs o>0 to avoid div/0)."""
    if len(values) < 2 or not any(v > 0 for v in values):
        return None
    ape_values = [
        abs(o - f) / o * 100
        for o, f in zip(values, fitted) if o > 0
    ]
    if not ape_values:
        return None
    return max(0.0, min(100.0, round(100.0 - (sum(ape_values) / len(ape_values)), 1)))


def _compute_product_forecast(
    *, product_key: str, title: str, observed_revenue: float,
    daily_rows: list[tuple], horizon_days: int,
) -> dict:
    """Per-product forecast: Holt fit → CI → delta vs last 7d → direction
    → accuracy. Falls through to _insufficient_product_record when the
    training window is below threshold."""
    values = [float(r[1] or 0) for r in daily_rows]
    n_days = len(values)
    if n_days < _MIN_POINTS_FOR_FORECAST:
        return _insufficient_product_record(
            product_key, title, observed_revenue, n_days,
        )

    fitted, forecast_vals = holt_forecast(values, horizon=horizon_days)
    sigma = _residual_std(values, fitted)
    r2 = _r_squared(values, fitted)
    point = sum(forecast_vals) / len(forecast_vals)
    l80, u80, l95, u95 = _prediction_interval(point, sigma, horizon_days)

    last_week_mean = sum(values[-7:]) / min(7, n_days)
    delta_pct = (
        ((point - last_week_mean) / last_week_mean * 100)
        if last_week_mean > 0 else 0.0
    )

    return {
        "product_key": str(product_key)[:128],
        "title": title[:128],
        "observed_revenue": observed_revenue,
        "forecast_point": round(point, 2),
        "forecast_lower_80": round(l80, 2),
        "forecast_upper_80": round(u80, 2),
        "forecast_lower_95": round(l95, 2),
        "forecast_upper_95": round(u95, 2),
        "delta_pct": round(delta_pct, 1),
        "direction": _classify_direction(delta_pct),
        "confidence": _confidence_label(n_days, r2),
        "accuracy_pct": _compute_accuracy_pct(values, fitted),
        "n_days": n_days,
        "r2": round(r2, 3),
    }


def _pick_riser_faller(
    forecastable: list[dict],
) -> tuple[dict | None, dict | None, dict | None, dict | None]:
    """From the forecastable products, identify the biggest_riser
    (delta >= +5%) and biggest_faller (delta <= -5%) by delta_pct.
    Returns (riser, faller, best_obj, worst_obj) — best/worst always
    returned for downstream insight wording even if they don't pass
    the ±5% threshold."""
    if not forecastable:
        return None, None, None, None
    sorted_by_delta = sorted(forecastable, key=lambda p: p["delta_pct"])
    worst = sorted_by_delta[0]
    best = sorted_by_delta[-1]
    riser = None
    faller = None
    if best["delta_pct"] >= 5:
        riser = {
            "product_key": best["product_key"],
            "title": best["title"],
            "delta_pct": best["delta_pct"],
        }
    if worst["delta_pct"] <= -5:
        faller = {
            "product_key": worst["product_key"],
            "title": worst["title"],
            "delta_pct": worst["delta_pct"],
        }
    return riser, faller, best, worst


def _build_forecast_insight(
    *, riser: dict | None, faller: dict | None,
    best: dict | None, worst: dict | None,
    horizon_days: int, top_n: int, forecastable_count: int,
) -> str:
    """Plain-language insight narrative. 4 branches: both rise+fall,
    rise-only, fall-only, all-stable. Falls back to a cold-start
    message when there's no forecastable product."""
    if not forecastable_count or best is None or worst is None:
        return (
            "Need at least one product with 7+ days of revenue history "
            "for forecast direction to surface."
        )
    if riser and faller and best["product_key"] != worst["product_key"]:
        return (
            f"{best['title']} forecast is rising "
            f"{best['delta_pct']:.0f}% next {horizon_days} days vs "
            f"last week. {worst['title']} is falling "
            f"{abs(worst['delta_pct']):.0f}%. Re-stock the riser, "
            f"investigate the faller before inventory builds up."
        )
    if riser:
        return (
            f"{best['title']} forecast is rising {best['delta_pct']:.0f}% "
            f"next {horizon_days} days vs last week — the strongest "
            f"momentum in your top-{top_n}."
        )
    if faller:
        return (
            f"{worst['title']} forecast is falling "
            f"{abs(worst['delta_pct']):.0f}% next {horizon_days} days "
            f"vs last week — investigate before inventory builds up."
        )
    return (
        f"All top-{forecastable_count} products have stable forecasts "
        f"(within ±5% of last week's pace). No urgent re-stock or "
        f"discount action surfaced."
    )


def forecast_by_sku(
    db: Session,
    shop_domain: str,
    *,
    horizon_days: int = 14,
    window_days: int = 60,
    top_n: int = 10,
) -> dict:
    """Per-SKU revenue forecast for top-N products by window revenue.

    Args:
        horizon_days: forecast horizon (1-60)
        window_days: training window (7-365)
        top_n: max products forecasted (1-25)

    Returns dict with shape:
        {
            shop_domain, horizon_days, window_days, currency,
            generated_at,
            products: [
                {
                    product_key, title,
                    observed_revenue: float,    # window total
                    forecast_point: float,      # next horizon avg/day
                    forecast_lower_80, forecast_upper_80,
                    forecast_lower_95, forecast_upper_95,
                    delta_pct: float,           # vs last 7d avg/day
                    direction: "rising"|"falling"|"stable",
                    confidence: "high"|"medium"|"low"|"insufficient",
                    accuracy_pct: float | None, # backtest pct
                    n_days: int, r2: float,
                },
                ...
            ],
            biggest_riser: {product_key, title, delta_pct} | None,
            biggest_faller: {product_key, title, delta_pct} | None,
            insight: str,
        }

    Cold-start: products with < _MIN_POINTS_FOR_FORECAST days of revenue
    in window get confidence="insufficient" and forecast_point=0 (honest,
    not fabricated).

    Refactored 2026-05-13 (A3 close): 286-LOC god function → 35-LOC
    composer + 8 pure helpers. Contract preserved byte-identical.
    """
    horizon_days, window_days, top_n = _clamp_sku_forecast_params(
        horizon_days, window_days, top_n,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=window_days)
    currency = get_shop_currency(db, shop_domain) or "USD"
    tz = get_shop_timezone(db, shop_domain) or "UTC"

    top_products = _fetch_top_products(db, shop_domain, since, currency, top_n)
    if not top_products:
        return _empty_sku_forecast(shop_domain, horizon_days, window_days, currency, now)

    pkeys = [str(row[0]) for row in top_products if row[0] is not None]
    daily_by_pkey = _fetch_daily_series(db, shop_domain, since, currency, tz, pkeys)

    products_out = [
        _compute_product_forecast(
            product_key=row[0],
            title=str(row[1]),
            observed_revenue=round(float(row[2] or 0), 2),
            daily_rows=daily_by_pkey.get(str(row[0]), []),
            horizon_days=horizon_days,
        )
        for row in top_products
    ]

    forecastable = [p for p in products_out if p["confidence"] != "insufficient"]
    riser, faller, best, worst = _pick_riser_faller(forecastable)
    insight = _build_forecast_insight(
        riser=riser, faller=faller, best=best, worst=worst,
        horizon_days=horizon_days, top_n=top_n,
        forecastable_count=len(forecastable),
    )

    return {
        "shop_domain": shop_domain,
        "horizon_days": horizon_days,
        "window_days": window_days,
        "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "products": products_out,
        "biggest_riser": riser,
        "biggest_faller": faller,
        "insight": insight,
    }


def _empty_sku_forecast(shop_domain, horizon_days, window_days, currency, now):
    return {
        "shop_domain": shop_domain,
        "horizon_days": horizon_days,
        "window_days": window_days,
        "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "products": [],
        "biggest_riser": None,
        "biggest_faller": None,
        "insight": "No product revenue in the training window yet. "
                   "Forecasts surface once line-item data flows.",
    }
