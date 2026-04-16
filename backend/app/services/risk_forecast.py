"""
risk_forecast.py — Future-facing RARS (H5).

RARS tells merchants how much revenue is at risk TODAY. risk_forecast
projects it 7 days out using a simple deterministic linear regression
over the rolling history we accumulate each time RARS is computed.

Why this matters
----------------
Competitors show current-state dashboards. HedgeSpark is the only one
that says *"at this trajectory, you'll be down another €420 next week"*
— a forward-looking loss-prevention nudge that can't be replicated
without our accumulated per-shop RARS history.

Storage
-------
Redis list `hs:rars_history:v1:{shop}` — JSON array of
    {"ts": iso, "total_at_risk_eur": float}
capped at 60 entries (~2 months at daily cadence, resilient to
5-minute cache refreshes because we dedupe by YYYY-MM-DD).

Method
------
Least-squares linear regression on (day_index, rars_total). Returns
slope, intercept, 7-day projection, confidence level (low/medium/high
based on sample size + residual variance). 100% deterministic, no LLM.

Self-healing integration
------------------------
* project_brain domain: rars (inherits from RARS)
* ops_alert when the forecast is computable but projects a >30% week-
  over-week jump (the pipeline wants to know about volatility)
* Exposed via /pro/risk-forecast
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("risk_forecast")

_REDIS_KEY = "hs:rars_history:v1"
_TTL_SECONDS = 120 * 24 * 3600
_MAX_HISTORY = 60
_MIN_POINTS_FOR_FORECAST = 4


def _key(shop: str) -> str:
    return f"{_REDIS_KEY}:{shop}"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("risk_forecast: _redis failed: %s", exc)
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_rars_snapshot(shop_domain: str, total_at_risk_eur: float) -> None:
    """Append today's RARS total to the rolling history.

    Dedupes by YYYY-MM-DD so the 5-min RARS cache refreshes don't
    pollute the series with near-identical points.
    """
    rc = _redis()
    if rc is None:
        record_silent_return("risk_forecast.record")
        return
    try:
        raw = rc.get(_key(shop_domain))
        history: list[dict[str, Any]] = json.loads(raw) if raw else []
        if not isinstance(history, list):
            history = []

        today_key = _now().strftime("%Y-%m-%d")
        for existing in history:
            if str(existing.get("ts", ""))[:10] == today_key:
                existing["total_at_risk_eur"] = round(float(total_at_risk_eur), 2)
                break
        else:
            history.append({
                "ts": _now().isoformat(),
                "total_at_risk_eur": round(float(total_at_risk_eur), 2),
            })

        if len(history) > _MAX_HISTORY:
            history = history[-_MAX_HISTORY:]

        rc.setex(_key(shop_domain), _TTL_SECONDS, json.dumps(history, default=str))
    except Exception as exc:
        log.debug("risk_forecast: record failed: %s", exc)


def _load_history(shop_domain: str) -> list[dict[str, Any]]:
    rc = _redis()
    if rc is None:
        record_silent_return("risk_forecast.load")
        return []
    try:
        raw = rc.get(_key(shop_domain))
        if not raw:
            return []
        history = json.loads(raw)
        if not isinstance(history, list):
            return []
        return [h for h in history if isinstance(h, dict)]
    except Exception as exc:
        log.warning("risk_forecast: _load_history failed: %s", exc)
        return []


def _linear_regression(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Least-squares fit. Returns (slope, intercept, r_squared, residual_std_error).

    residual_std_error is the standard error of the residuals, used to
    build prediction intervals (α6 — probabilistic forecast).
    """
    n = len(points)
    if n < 2:
        return 0.0, (points[0][1] if points else 0.0), 0.0, 0.0
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    num = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    den = sum((p[0] - mean_x) ** 2 for p in points)
    if den == 0:
        return 0.0, mean_y, 0.0, 0.0
    slope = num / den
    intercept = mean_y - slope * mean_x
    ss_tot = sum((p[1] - mean_y) ** 2 for p in points)
    ss_res = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in points)
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    # Residual std error: sqrt(ss_res / (n-2)) — unbiased estimator
    rse = (ss_res / max(1, n - 2)) ** 0.5
    return slope, intercept, max(0.0, min(1.0, r2)), rse


def _prediction_interval(
    forecast_value: float,
    residual_std: float,
    horizon_days: int = 7,
) -> tuple[float, float, float, float]:
    """Compute 80% and 95% prediction intervals around a point forecast.

    Uses normal approximation (t-tabulated z-scores). Horizon inflation
    is sqrt(horizon_days) — a simple random-walk-ish widening that keeps
    us conservative without needing a full ARIMA implementation.

    Returns (lower_80, upper_80, lower_95, upper_95), all non-negative.
    """
    # z80 = 1.28, z95 = 1.96
    horizon_factor = max(1.0, horizon_days**0.5)
    std_h = residual_std * horizon_factor
    lower_80 = max(0.0, forecast_value - 1.28 * std_h)
    upper_80 = forecast_value + 1.28 * std_h
    lower_95 = max(0.0, forecast_value - 1.96 * std_h)
    upper_95 = forecast_value + 1.96 * std_h
    return lower_80, upper_80, lower_95, upper_95


def _confidence_label(n_points: int, r_squared: float) -> str:
    if n_points < _MIN_POINTS_FOR_FORECAST:
        return "insufficient_history"
    if n_points >= 14 and r_squared >= 0.6:
        return "high"
    if n_points >= 7 and r_squared >= 0.3:
        return "medium"
    return "low"


def get_risk_forecast(shop_domain: str) -> dict[str, Any]:
    """Compute a 7-day RARS forecast from the rolling history.

    Returns a dict suitable for the API response.
    """
    history = _load_history(shop_domain)

    if len(history) < _MIN_POINTS_FOR_FORECAST:
        return {
            "shop_domain": shop_domain,
            "status": "insufficient_history",
            "points_available": len(history),
            "points_required": _MIN_POINTS_FOR_FORECAST,
            "headline": (
                "Forecast needs a few more daily snapshots before it's "
                "reliable. Check back in a few days."
            ),
            "history": history,
        }

    # Build (day_index, rars) points — day 0 = oldest
    try:
        timestamps = [datetime.fromisoformat(str(h["ts"]).replace("Z", "")) for h in history]
    except Exception as exc:
        log.warning("risk_forecast: get_risk_forecast failed: %s", exc)
        return {
            "shop_domain": shop_domain,
            "status": "history_corrupt",
            "headline": "Forecast unavailable — history could not be parsed.",
            "history": history,
        }

    t0 = min(timestamps)
    points: list[tuple[float, float]] = []
    for h, ts in zip(history, timestamps):
        day_idx = (ts - t0).total_seconds() / 86400.0
        points.append((day_idx, float(h.get("total_at_risk_eur") or 0)))

    slope, intercept, r2, rse = _linear_regression(points)

    last_day = max(p[0] for p in points)
    today_value = slope * last_day + intercept
    forecast_day = last_day + 7
    forecast_value = max(0.0, slope * forecast_day + intercept)

    # α6 — probabilistic prediction intervals
    p_lower_80, p_upper_80, p_lower_95, p_upper_95 = _prediction_interval(
        forecast_value, rse, horizon_days=7,
    )

    week_delta = forecast_value - today_value
    week_delta_pct = (week_delta / today_value * 100) if today_value > 0 else 0.0

    confidence = _confidence_label(len(points), r2)

    if week_delta > 0 and week_delta_pct > 5:
        direction = "rising"
        headline = (
            f"Risk is rising — next week projected at €{forecast_value:.0f} "
            f"(+€{week_delta:.0f}, +{week_delta_pct:.0f}%)."
        )
    elif week_delta < 0 and abs(week_delta_pct) > 5:
        direction = "falling"
        headline = (
            f"Risk is falling — next week projected at €{forecast_value:.0f} "
            f"(−€{abs(week_delta):.0f}, −{abs(week_delta_pct):.0f}%)."
        )
    else:
        direction = "stable"
        headline = f"Risk is stable around €{today_value:.0f}/month."

    if confidence in ("high", "medium") and week_delta_pct > 30:
        try:
            from app.core.database import SessionLocal
            from app.services.alerting import write_alert
            db = SessionLocal()
            try:
                write_alert(
                    db,
                    severity="info",  # projection, not an incident
                    source="risk_forecast",
                    alert_type="rars_volatility_projected",
                    summary=(
                        f"Projected RARS jump >30% for {shop_domain}: "
                        f"€{today_value:.0f} → €{forecast_value:.0f}"
                    ),
                    shop_domain=shop_domain,
                    detail={
                        "today_value": round(today_value, 2),
                        "forecast_value": round(forecast_value, 2),
                        "delta_pct": round(week_delta_pct, 2),
                        "confidence": confidence,
                        "points": len(points),
                    },
                )
                # Phase Ω''' — outbound webhook fan-out for rars.spike.
                # Distinct event type so subscribers can target it specifically
                # without listening to every anomaly.
                try:
                    from app.services.event_emitter import emit
                    emit(db, shop_domain, "rars.spike", {
                        "shop_domain": shop_domain,
                        "today_eur": round(today_value, 2),
                        "forecast_eur": round(forecast_value, 2),
                        "delta_pct": round(week_delta_pct, 2),
                        "confidence": confidence,
                    })
                except Exception as exc:
                    log.warning("risk_forecast: get_risk_forecast failed: %s", exc)
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            log.warning("risk_forecast: get_risk_forecast failed: %s", exc)

    return {
        "shop_domain": shop_domain,
        "status": "ok",
        "today_value_eur": round(today_value, 2),
        "forecast_7d_eur": round(forecast_value, 2),
        # α6: probabilistic prediction intervals
        "forecast_7d_lower_80_eur": round(p_lower_80, 2),
        "forecast_7d_upper_80_eur": round(p_upper_80, 2),
        "forecast_7d_lower_95_eur": round(p_lower_95, 2),
        "forecast_7d_upper_95_eur": round(p_upper_95, 2),
        "residual_std_error": round(rse, 2),
        "week_delta_eur": round(week_delta, 2),
        "week_delta_pct": round(week_delta_pct, 2),
        "direction": direction,
        "confidence": confidence,
        "r_squared": round(r2, 3),
        "points_used": len(points),
        "slope_per_day": round(slope, 4),
        "headline": headline,
        "history": history[-30:],  # last 30 for dashboard mini-chart
    }
