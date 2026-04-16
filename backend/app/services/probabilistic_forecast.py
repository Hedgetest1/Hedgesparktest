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
                SELECT (created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date AS d,
                       COALESCE(SUM(total_price), 0) AS rev
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :since
                  AND (:currency IS NULL OR currency = :currency)
                GROUP BY (created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date
                ORDER BY d ASC
                """
            ),
            {"shop": shop_domain, "since": since, "currency": currency, "tz": tz},
        ).fetchall()
    except Exception as exc:
        log.warning("forecast: revenue query failed: %s", exc)
        return _empty_forecast(shop_domain, "revenue", horizon_days, window_days)

    if len(rows) < _MIN_POINTS_FOR_FORECAST:
        return _empty_forecast(
            shop_domain, "revenue", horizon_days, window_days,
            reason=f"only {len(rows)} days of data",
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

    if delta_pct > 5:
        direction = "rising"
        headline = (
            f"Revenue trending up: next {horizon_days} days projected at "
            f"€{point:,.0f}/day (+{delta_pct:.0f}% vs last week's average)."
        )
    elif delta_pct < -5:
        direction = "falling"
        headline = (
            f"Revenue cooling: next {horizon_days} days projected at "
            f"€{point:,.0f}/day ({delta_pct:.0f}% vs last week's average)."
        )
    else:
        direction = "stable"
        headline = f"Revenue stable around €{point:,.0f}/day."

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
    shop: str, metric: str, horizon: int, window: int, reason: str = "insufficient_data"
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
    }
