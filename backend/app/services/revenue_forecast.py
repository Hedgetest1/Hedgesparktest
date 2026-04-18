"""
revenue_forecast.py — Deterministic revenue forecasting from real order data.

Uses daily revenue history from shop_orders to produce honest forward-looking
projections.  No ML, no external dependencies, no fake precision.

Approach: linear regression on recent daily revenue + volatility-based range.

Forecast method:
    1. Build daily revenue series from shop_orders (zero-filled)
    2. Compute linear trend (slope) on the series
    3. Project forward using trend line
    4. Compute forecast range from historical volatility (stddev)
    5. Classify trend direction from slope + significance

Confidence levels:
    "high"   — 28+ days of history, low volatility
    "medium" — 14-27 days of history, or moderate volatility
    "low"    — 7-13 days of history, or high volatility
    None     — < 7 days → forecast returned with honest "insufficient_history"

Public interface
----------------
    get_revenue_forecast(db, shop_domain, history_days=90) -> dict
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("revenue_forecast")

_MIN_HISTORY_DAYS = 7      # absolute minimum to produce any forecast
_GOOD_HISTORY_DAYS = 14    # enough for medium confidence
_HIGH_HISTORY_DAYS = 28    # enough for high confidence


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Linear regression — same approach as scaling_intelligence._linear_trend
# ---------------------------------------------------------------------------

def _linear_regression(values: list[float]) -> tuple[float, float]:
    """
    Ordinary least squares: y = slope * x + intercept.
    x = 0..N-1 (day index).
    Returns (slope_per_day, intercept).
    """
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0

    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))

    slope = num / den if den != 0 else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _stddev(values: list[float]) -> float:
    """Population standard deviation."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance)


def _coefficient_of_variation(values: list[float]) -> float:
    """CV = stddev / mean.  Measures relative volatility."""
    mean = sum(values) / len(values) if values else 0
    if mean <= 0:
        return 0.0
    return _stddev(values) / mean


# ---------------------------------------------------------------------------
# Day-of-week seasonality — optional, only if enough data
# ---------------------------------------------------------------------------

def _dow_factors(daily_series: list[tuple[date, float]]) -> dict[int, float] | None:
    """
    Compute day-of-week scaling factors from at least 3 full weeks of data.
    Returns {0: Mon_factor, 1: Tue_factor, ...} where factor = dow_avg / overall_avg.
    Returns None if insufficient data for reliable seasonality.
    """
    if len(daily_series) < 21:  # need 3+ full weeks
        return None

    by_dow: dict[int, list[float]] = {d: [] for d in range(7)}
    for dt, rev in daily_series:
        by_dow[dt.weekday()].append(rev)

    # Each DOW needs at least 3 samples
    if any(len(v) < 3 for v in by_dow.values()):
        return None

    overall_avg = sum(r for _, r in daily_series) / len(daily_series)
    if overall_avg <= 0:
        return None

    factors = {}
    for dow, revenues in by_dow.items():
        dow_avg = sum(revenues) / len(revenues)
        factors[dow] = dow_avg / overall_avg

    return factors


# ---------------------------------------------------------------------------
# Core forecast
# ---------------------------------------------------------------------------

def get_revenue_forecast(
    db: Session,
    shop_domain: str,
    history_days: int = 90,
) -> dict:
    """
    Compute deterministic revenue forecast from historical daily revenue.

    Returns:
        {
            "generated_at": str,
            "currency": str,
            "history": {
                "days_available": int,
                "days_with_revenue": int,
                "daily_series": [{"day": str, "revenue": float, "orders": int}, ...],
                "total_revenue": float,
                "avg_daily_revenue": float,
            },
            "forecast_7d": {
                "revenue": float,          # point estimate
                "revenue_low": float,      # lower bound (1 stddev below)
                "revenue_high": float,     # upper bound (1 stddev above)
                "avg_daily": float,
            },
            "forecast_30d": {
                "revenue": float,
                "revenue_low": float,
                "revenue_high": float,
                "avg_daily": float,
            },
            "trend": {
                "direction": "up" | "flat" | "down",
                "slope_per_day": float,    # revenue change per day
                "weekly_change_pct": float,# projected weekly % change
            },
            "confidence": "high" | "medium" | "low" | None,
            "confidence_reason": str,
            "seasonality_available": bool,
        }
    """
    history_days = max(7, min(history_days, 365))

    # Fetch daily revenue series
    currency, daily_series = _fetch_daily_series(db, shop_domain, history_days)

    if not daily_series:
        return _insufficient_response(currency, reason="no_order_history")

    days_available = len(daily_series)
    days_with_revenue = sum(1 for _, r in daily_series if r > 0)

    if days_with_revenue < 3:
        return _insufficient_response(
            currency,
            reason=f"only_{days_with_revenue}_days_with_revenue",
            daily_series=daily_series,
        )

    # Extract revenue values for regression
    revenues = [r for _, r in daily_series]
    total_revenue = sum(revenues)
    avg_daily = total_revenue / len(revenues) if revenues else 0.0

    # Linear regression on the full series
    slope, intercept = _linear_regression(revenues)

    # Volatility
    cv = _coefficient_of_variation(revenues)
    daily_stddev = _stddev(revenues)

    # Day-of-week seasonality (optional boost)
    dow_factors = _dow_factors(daily_series)
    seasonality_available = dow_factors is not None

    # Confidence level
    confidence, confidence_reason = _assess_confidence(
        days_available, days_with_revenue, cv,
    )

    # Build forecasts
    last_day = daily_series[-1][0]
    n = len(revenues)  # trend line current position

    forecast_1d = _project_window(
        slope, intercept, n, daily_stddev, last_day, 1, dow_factors,
    )
    forecast_7d = _project_window(
        slope, intercept, n, daily_stddev, last_day, 7, dow_factors,
    )
    forecast_30d = _project_window(
        slope, intercept, n, daily_stddev, last_day, 30, dow_factors,
    )

    # Trend direction
    trend_direction, weekly_change_pct = _classify_trend(slope, avg_daily)

    # Format history for response
    history_points = [
        {"day": dt.isoformat(), "revenue": round(rev, 2), "orders": 0}
        for dt, rev in daily_series
    ]
    # Backfill order counts (lightweight — we already have the revenue)
    order_counts = _fetch_daily_order_counts(db, shop_domain, history_days)
    for point in history_points:
        point["orders"] = order_counts.get(point["day"], 0)

    # Prediction accuracy foundation (MA-1). Write-and-forget: every
    # call to this function persists the 7d + 30d point-forecasts to
    # prediction_log with a deterministic dedup key (shop, metric,
    # horizon_date). A nightly mature-pass later fills in actual_value
    # and the /pro/prediction-accuracy endpoint aggregates MAPE. Never
    # raises — forecast accuracy logging must not break the forecast.
    try:
        from datetime import timedelta as _td
        from app.services.prediction_log import log_prediction as _log_prediction
        today = _now().date()
        if confidence is not None:  # only log forecasts we'd actually show
            # 1-day forecast: the fast-maturing channel. Matures tomorrow
            # so the MA-1 accuracy card shows real MAPE inside a merchant's
            # first 8 days instead of the ~56 days the 30d horizon takes.
            _log_prediction(
                db,
                shop_domain=shop_domain,
                metric="forecast_1d_revenue",
                predicted_value=forecast_1d.get("revenue", 0.0),
                predicted_low=forecast_1d.get("revenue_low"),
                predicted_high=forecast_1d.get("revenue_high"),
                horizon_date=today + _td(days=1),
                currency=currency,
                confidence=confidence,
            )
            _log_prediction(
                db,
                shop_domain=shop_domain,
                metric="forecast_7d_revenue",
                predicted_value=forecast_7d.get("revenue", 0.0),
                predicted_low=forecast_7d.get("revenue_low"),
                predicted_high=forecast_7d.get("revenue_high"),
                horizon_date=today + _td(days=7),
                currency=currency,
                confidence=confidence,
            )
            _log_prediction(
                db,
                shop_domain=shop_domain,
                metric="forecast_30d_revenue",
                predicted_value=forecast_30d.get("revenue", 0.0),
                predicted_low=forecast_30d.get("revenue_low"),
                predicted_high=forecast_30d.get("revenue_high"),
                horizon_date=today + _td(days=30),
                currency=currency,
                confidence=confidence,
            )
    except Exception as _exc:
        log.warning("revenue_forecast: prediction_log write failed: %s", _exc)

    return {
        "generated_at": _now().isoformat() + "Z",
        "currency": currency,
        "history": {
            "days_available": days_available,
            "days_with_revenue": days_with_revenue,
            "daily_series": history_points[-90:],  # cap response size
            "total_revenue": round(total_revenue, 2),
            "avg_daily_revenue": round(avg_daily, 2),
        },
        "forecast_1d": forecast_1d,
        "forecast_7d": forecast_7d,
        "forecast_30d": forecast_30d,
        "trend": {
            "direction": trend_direction,
            "slope_per_day": round(slope, 2),
            "weekly_change_pct": round(weekly_change_pct, 1),
        },
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "seasonality_available": seasonality_available,
    }


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

def _project_window(
    slope: float,
    intercept: float,
    n: int,
    daily_stddev: float,
    last_day: date,
    window_days: int,
    dow_factors: dict[int, float] | None,
) -> dict:
    """
    Project revenue for a future window.
    Returns {revenue, revenue_low, revenue_high, avg_daily}.
    """
    total = 0.0
    for d in range(1, window_days + 1):
        # Trend line value at day (n + d)
        base = intercept + slope * (n + d - 1)
        base = max(0.0, base)  # revenue can't be negative

        # Apply day-of-week seasonality if available
        if dow_factors:
            future_date = last_day + timedelta(days=d)
            dow = future_date.weekday()
            base *= dow_factors.get(dow, 1.0)

        total += base

    total = max(0.0, total)
    avg_daily = total / window_days if window_days > 0 else 0.0

    # Range: ±1 stddev * sqrt(window) — widening uncertainty over time
    uncertainty = daily_stddev * math.sqrt(window_days)
    low = max(0.0, total - uncertainty)
    high = total + uncertainty

    return {
        "revenue": round(total, 2),
        "revenue_low": round(low, 2),
        "revenue_high": round(high, 2),
        "avg_daily": round(avg_daily, 2),
    }


def _classify_trend(slope: float, avg_daily: float) -> tuple[str, float]:
    """
    Classify trend direction based on slope relative to avg daily revenue.
    Returns (direction, weekly_change_pct).
    """
    if avg_daily <= 0:
        return "flat", 0.0

    weekly_change = slope * 7
    weekly_change_pct = (weekly_change / avg_daily) * 100

    # Thresholds: >5% weekly change is meaningful
    if weekly_change_pct > 5:
        return "up", weekly_change_pct
    elif weekly_change_pct < -5:
        return "down", weekly_change_pct
    else:
        return "flat", weekly_change_pct


def _assess_confidence(
    days_available: int,
    days_with_revenue: int,
    cv: float,
) -> tuple[str | None, str]:
    """
    Assess forecast confidence.
    Returns (confidence_level, reason).
    """
    if days_available < _MIN_HISTORY_DAYS:
        return None, f"insufficient_history: {days_available} days (need {_MIN_HISTORY_DAYS}+)"

    # High volatility degrades confidence
    volatility = "low" if cv < 0.5 else ("moderate" if cv < 1.0 else "high")

    if days_available >= _HIGH_HISTORY_DAYS and volatility == "low":
        return "high", f"{days_available}d history, low volatility (CV={cv:.2f})"

    if days_available >= _GOOD_HISTORY_DAYS:
        if volatility == "high":
            return "low", f"{days_available}d history but high volatility (CV={cv:.2f})"
        return "medium", f"{days_available}d history, {volatility} volatility (CV={cv:.2f})"

    return "low", f"limited history: {days_available}d, {volatility} volatility (CV={cv:.2f})"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_daily_series(
    db: Session, shop_domain: str, days: int,
) -> tuple[str, list[tuple[date, float]]]:
    """
    Fetch daily revenue series from shop_orders.
    Returns (currency, [(date, revenue), ...]) zero-filled.
    """
    from app.services.revenue_metrics import get_shop_currency, get_shop_timezone
    currency = get_shop_currency(db, shop_domain) or "USD"
    tz = get_shop_timezone(db, shop_domain)

    try:
        rows = db.execute(
            text("""
                SELECT
                    d.day::date AS day,
                    COALESCE(SUM(so.total_price), 0) AS revenue
                FROM generate_series(
                    (CURRENT_DATE - make_interval(days => :days - 1)),
                    CURRENT_DATE,
                    '1 day'::interval
                ) AS d(day)
                LEFT JOIN shop_orders so
                    ON so.shop_domain = :shop
                   AND date_trunc('day', so.created_at AT TIME ZONE :tz)::date = d.day::date
                   AND (:currency IS NULL OR so.currency = :currency)
                GROUP BY d.day
                ORDER BY d.day ASC
            """),
            {"shop": shop_domain, "days": days, "currency": currency, "tz": tz},
        ).fetchall()

        series = [(r[0], float(r[1] or 0)) for r in rows]
        return currency, series

    except Exception as exc:
        log.error("revenue_forecast: daily series query failed shop=%s: %s", shop_domain, exc)
        return currency, []


def _fetch_daily_order_counts(
    db: Session, shop_domain: str, days: int,
) -> dict[str, int]:
    """Fetch daily order counts keyed by ISO date string."""
    from app.services.revenue_metrics import get_shop_timezone
    tz = get_shop_timezone(db, shop_domain)
    try:
        rows = db.execute(
            text("""
                SELECT date_trunc('day', created_at AT TIME ZONE :tz)::date AS day,
                       COUNT(*)::int AS cnt
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= (CURRENT_DATE - make_interval(days => :days - 1))
                GROUP BY date_trunc('day', created_at AT TIME ZONE :tz)::date
            """),
            {"shop": shop_domain, "days": days, "tz": tz},
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Insufficient data response
# ---------------------------------------------------------------------------

def _insufficient_response(
    currency: str,
    reason: str,
    daily_series: list[tuple[date, float]] | None = None,
) -> dict:
    """Return honest response when forecast cannot be produced."""
    history_points = []
    if daily_series:
        history_points = [
            {"day": dt.isoformat(), "revenue": round(rev, 2), "orders": 0}
            for dt, rev in daily_series
        ]

    return {
        "generated_at": _now().isoformat() + "Z",
        "currency": currency,
        "history": {
            "days_available": len(daily_series) if daily_series else 0,
            "days_with_revenue": sum(1 for _, r in daily_series if r > 0) if daily_series else 0,
            "daily_series": history_points,
            "total_revenue": round(sum(r for _, r in daily_series), 2) if daily_series else 0.0,
            "avg_daily_revenue": 0.0,
        },
        "forecast_7d": None,
        "forecast_30d": None,
        "trend": None,
        "confidence": None,
        "confidence_reason": reason,
        "seasonality_available": False,
    }
