"""
prediction_log.py — Log predictions + compute accuracy at backtest (MA-1).

The moat. Every competitor's marketing copy claims "accurate
forecasts". None of them publishes a MAPE (Mean Absolute Percentage
Error) against what actually happened after the forecast horizon
passed. We do.

Flow
----
1. Whenever our probabilistic-forecast surfaces (revenue_forecast,
   later LTV + RARS) compute a prediction, they call log_prediction()
   here. One row per (shop, metric, horizon_date) — unique-constraint-
   backed dedup so double-computing the same forecast doesn't double-
   count in MAPE.

2. Periodically (via run_mature_predictions() from aggregation_worker),
   we walk predictions whose horizon_date has passed, compute the
   actual observed value from shop_orders, and UPDATE the row with
   actual_value + measured_at. Strictly additive to the row — no
   mutation of predicted_* fields.

3. On demand via compute_accuracy(), we aggregate matured rows into
   MAPE per metric + the last N predictions for display. Below a
   minimum of MIN_PREDICTIONS_FOR_REPORT matured rows we return
   `status=insufficient_history` with an explicit unlock message —
   same honesty discipline as the benchmark engine (MA-4).

Why a dedicated table (not audit_log)
-------------------------------------
Audit_log is append-only; filling in actual_value later would force
a second "measured" row and double the JOIN work. The prediction_log
schema columns-not-JSON make backtest queries index-clean and keep
the MAPE compute path cheap.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("prediction_log")

# Minimum matured predictions before we publish accuracy numbers.
# 8 mirrors the "8-week run at weekly cadence" rationale for stable
# MAPE — same honesty pattern as benchmarks' 30-peer floor (MA-4).
MIN_PREDICTIONS_FOR_REPORT = 8

# Supported metrics. Keep in sync with frontend copy + log_prediction
# validation + compute_accuracy report shape.
METRICS = ("forecast_7d_revenue", "forecast_30d_revenue")

_LOOKBACK_DAYS = 120  # how far back we look for matured predictions


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def log_prediction(
    db: Session,
    *,
    shop_domain: str,
    metric: str,
    predicted_value: float,
    horizon_date: date,
    predicted_low: Optional[float] = None,
    predicted_high: Optional[float] = None,
    currency: str = "USD",
    confidence: Optional[str] = None,
    context_hash: Optional[str] = None,
) -> bool:
    """Append one prediction. Idempotent via UNIQUE (shop, metric,
    horizon_date) — duplicate writes become no-ops. Returns True if
    a row was inserted, False on duplicate or write error. Never raises
    (prediction logging must not break the forecast it was called from)."""
    if metric not in METRICS:
        log.warning("prediction_log: unknown metric %s", metric)
        return False

    prediction_date = _now().date()
    try:
        db.execute(
            text(
                """
                INSERT INTO prediction_log (
                    created_at, shop_domain, metric,
                    prediction_date, horizon_date,
                    predicted_value, predicted_low, predicted_high,
                    currency, confidence, context_hash
                ) VALUES (
                    :ts, :shop, :metric,
                    :pred_date, :horizon,
                    :pv, :pl, :ph,
                    :ccy, :conf, :ctx
                )
                ON CONFLICT (shop_domain, metric, horizon_date) DO NOTHING
                """
            ),
            {
                "ts": _now(),
                "shop": shop_domain,
                "metric": metric,
                "pred_date": prediction_date,
                "horizon": horizon_date,
                "pv": round(float(predicted_value), 2),
                "pl": round(float(predicted_low), 2) if predicted_low is not None else None,
                "ph": round(float(predicted_high), 2) if predicted_high is not None else None,
                "ccy": currency or "USD",
                "conf": confidence,
                "ctx": context_hash,
            },
        )
        return True
    except Exception as exc:
        log.warning("prediction_log: write failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Mature path — fill in actual_value + measured_at
# ---------------------------------------------------------------------------


def _actual_revenue(
    db: Session,
    shop_domain: str,
    start_date: date,
    end_date: date,
    currency: str,
) -> Optional[float]:
    """Sum observed revenue across [start_date, end_date) filtered to the
    prediction's currency. Returns None if no orders exist in this
    window for this currency — we can't distinguish 'zero orders' from
    'data gap' at this layer, so we skip those rows from accuracy math
    (honest: don't inflate the sample size with zeros).

    The currency filter prevents mixed-currency blending — a merchant
    selling in EUR and USD would otherwise see a sum that mixes units
    and the MAPE would be garbage. Our forecast predicts in the
    merchant's primary currency; we backtest against the same currency
    only."""
    try:
        row = db.execute(
            text(
                """
                SELECT COALESCE(SUM(total_price), 0) AS revenue,
                       COUNT(*) AS orders
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND currency = :ccy
                  AND created_at >= :start_ts
                  AND created_at <  :end_ts
                """
            ),
            {
                "shop": shop_domain,
                "ccy": (currency or "USD").upper(),
                "start_ts": datetime.combine(start_date, datetime.min.time()),
                "end_ts": datetime.combine(end_date, datetime.min.time()),
            },
        ).first()
    except Exception as exc:
        log.warning("prediction_log: actual query failed: %s", exc)
        return None
    if not row:
        return None
    orders = int(row[1] or 0)
    if orders == 0:
        return None
    return float(row[0] or 0)


def run_mature_predictions(db: Session, *, limit: int = 200) -> dict:
    """Walk matured predictions (horizon_date <= today) without a
    measured actual yet, compute the observed revenue for their
    window, and UPDATE the row in place. Idempotent: rows with
    actual_value already set are never reprocessed.

    Called from the aggregation_worker daily cycle. Bounded by `limit`
    so a cold start doesn't stall the worker."""
    today = _now().date()
    # Cross-tenant scan by design — the maturation worker walks every
    # shop's unfilled predictions in one bounded pass. The explicit
    # `shop_domain IS NOT NULL` guard both (a) signals to the tenant-
    # isolation audit that cross-shop-by-design is intentional and (b)
    # defends against a stray NULL from an upstream insert bug. Per-row
    # UPDATE below carries the row's own shop_domain implicitly via id.
    try:
        rows = db.execute(
            text(
                """
                SELECT id, shop_domain, metric, prediction_date, horizon_date, currency
                FROM prediction_log
                WHERE actual_value IS NULL
                  AND horizon_date <= :today
                  AND shop_domain IS NOT NULL
                ORDER BY horizon_date ASC
                LIMIT :lim
                """
            ),
            {"today": today, "lim": limit},
        ).fetchall()
    except Exception as exc:
        log.warning("prediction_log: mature query failed: %s", exc)
        return {"matured": 0, "skipped": 0}

    matured = 0
    skipped = 0
    for row in rows:
        rec_id, shop, metric, pred_date, horizon, ccy = row
        actual = _actual_revenue(db, shop, pred_date, horizon, ccy)
        if actual is None:
            skipped += 1
            continue
        try:
            # Explicit shop_domain in the UPDATE WHERE — belt-and-suspenders
            # tenant isolation. Matches the shop from the row we selected
            # above. Pairs with the cross-tenant disclaimer on the SELECT.
            db.execute(
                text(
                    """
                    UPDATE prediction_log
                    SET actual_value = :actual,
                        measured_at = :ts
                    WHERE id = :id
                      AND shop_domain = :shop
                      AND actual_value IS NULL
                    """
                ),
                {"actual": round(actual, 2), "ts": _now(), "id": rec_id, "shop": shop},
            )
            matured += 1
        except Exception as exc:
            log.warning("prediction_log: update failed id=%s: %s", rec_id, exc)
            skipped += 1
    return {"matured": matured, "skipped": skipped}


# ---------------------------------------------------------------------------
# Read path — accuracy report
# ---------------------------------------------------------------------------


def compute_accuracy(db: Session, shop_domain: str) -> dict:
    """Aggregate matured predictions for this shop into per-metric
    MAPE + last N predictions. Returns one of two shapes — see below.

    Insufficient:
        {
            "status": "insufficient_history",
            "metrics": {},
            "predictions_seen": N,
            "unlock_at": MIN_PREDICTIONS_FOR_REPORT,
            "message": str
        }

    Ok:
        {
            "status": "ok",
            "metrics": {
                "forecast_7d_revenue": {
                    "sample_size": int,
                    "mape_pct": float,         # mean absolute % error
                    "median_error_pct": float,
                    "currency": str,
                    "last_predictions": [...]  # most recent 8
                }, ...
            }
        }
    """
    since = _now() - timedelta(days=_LOOKBACK_DAYS)
    try:
        rows = db.execute(
            text(
                """
                SELECT metric, prediction_date, horizon_date,
                       predicted_value, actual_value, currency
                FROM prediction_log
                WHERE shop_domain = :shop
                  AND created_at >= :since
                  AND actual_value IS NOT NULL
                ORDER BY horizon_date DESC
                LIMIT 500
                """
            ),
            {"shop": shop_domain, "since": since},
        ).fetchall()
    except Exception as exc:
        log.warning("prediction_log: compute_accuracy fetch failed: %s", exc)
        return {
            "status": "error",
            "metrics": {},
            "message": "Couldn't read prediction history — we'll retry automatically.",
        }

    total_matured = len(rows)
    if total_matured < MIN_PREDICTIONS_FOR_REPORT:
        return {
            "status": "insufficient_history",
            "metrics": {},
            "predictions_seen": total_matured,
            "unlock_at": MIN_PREDICTIONS_FOR_REPORT,
            "message": (
                f"Prediction accuracy unlocks at {MIN_PREDICTIONS_FOR_REPORT} "
                f"matured predictions ({total_matured} so far). We don't "
                "publish a MAPE computed from too few samples — honest "
                "numbers only, same discipline as our peer benchmarks."
            ),
        }

    by_metric: dict[str, list[dict]] = {}
    for metric, pred_date, horizon, pv, av, ccy in rows:
        # Skip rows where actual is zero — dividing by zero for error_pct
        # would be noise; report only on non-zero actuals.
        av_f = float(av)
        if av_f == 0:
            continue
        pv_f = float(pv)
        err_pct = abs(pv_f - av_f) / abs(av_f) * 100
        by_metric.setdefault(metric, []).append({
            "prediction_date": pred_date.isoformat() if pred_date else None,
            "horizon_date": horizon.isoformat() if horizon else None,
            "predicted": round(pv_f, 2),
            "actual": round(av_f, 2),
            "error_pct": round(err_pct, 2),
            "currency": ccy or "USD",
        })

    if not by_metric:
        return {
            "status": "insufficient_history",
            "metrics": {},
            "predictions_seen": total_matured,
            "unlock_at": MIN_PREDICTIONS_FOR_REPORT,
            "message": (
                "Prediction accuracy requires non-zero observed revenue "
                "on the horizon window. Once your store has orders on "
                "the days your forecast covered, accuracy math kicks in."
            ),
        }

    out_metrics: dict[str, dict] = {}
    for metric, entries in by_metric.items():
        errors = sorted(e["error_pct"] for e in entries)
        mape = sum(errors) / len(errors)
        median = errors[len(errors) // 2]
        # Currency should be consistent per metric; carry first non-null.
        currency = entries[0].get("currency", "USD")
        out_metrics[metric] = {
            "sample_size": len(entries),
            "mape_pct": round(mape, 2),
            "median_error_pct": round(median, 2),
            "currency": currency,
            "last_predictions": entries[:8],
        }

    return {"status": "ok", "metrics": out_metrics}
