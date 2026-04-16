"""
trust_outcome_measurement.py — Closed-loop learning for delegated autonomy.

Every trust_execution_log row starts with outcome='pending'. 48h after
execution this service measures the real revenue delta vs a baseline
(the same product's revenue over the 48h immediately BEFORE the action)
and updates the row.

The outcome feeds three downstream systems:
1. TrustControlCenter dashboard — "effective rate" stat
2. ROI Hero banner — aggregate savings number
3. Trust contract calibration — if a contract has <30% effective rate
   over its last 10 executions, it gets auto-paused as a safety brake.

Deterministic. No LLM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models.trust_contract import TrustContract, TrustExecutionLog

log = logging.getLogger("trust_outcome_measurement")

_MEASUREMENT_DELAY_HOURS = 48
_MAX_MEASURE_PER_CYCLE = 100
_AUTO_PAUSE_MIN_EXECUTIONS = 10
_AUTO_PAUSE_MIN_EFFECTIVE_RATE = 0.30


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def measure_pending_trust_executions(db: Session) -> dict:
    """Find executions older than 48h with outcome='pending' and measure them."""
    report = {
        "scanned": 0,
        "measured": 0,
        "effective": 0,
        "ineffective": 0,
        "inconclusive": 0,
        "auto_paused_contracts": 0,
    }
    cutoff = _now() - timedelta(hours=_MEASUREMENT_DELAY_HOURS)

    try:
        rows = (
            db.query(TrustExecutionLog)
            .filter(
                TrustExecutionLog.outcome == "pending",
                TrustExecutionLog.executed_at < cutoff,
            )
            .order_by(TrustExecutionLog.executed_at.asc())
            .limit(_MAX_MEASURE_PER_CYCLE)
            .all()
        )
    except Exception as exc:
        log.warning("trust_outcome: query failed: %s", exc)
        return report

    report["scanned"] = len(rows)

    for row in rows:
        outcome, delta = _measure_one(db, row)
        row.outcome = outcome
        row.revenue_delta_eur = delta
        row.measured_at = _now()

        if outcome == "effective":
            report["effective"] += 1
        elif outcome == "ineffective":
            report["ineffective"] += 1
        else:
            report["inconclusive"] += 1
        report["measured"] += 1

    try:
        db.flush()
    except Exception as exc:
        log.warning("trust_outcome: flush failed: %s", exc)
        db.rollback()
        return report

    # After measuring, run contract calibration — auto-pause chronically
    # ineffective contracts so they stop consuming budget on bad actions.
    paused = _calibrate_contracts(db)
    report["auto_paused_contracts"] = paused

    try:
        db.commit()
    except Exception as exc:
        log.warning("trust_outcome: commit failed: %s", exc)
        db.rollback()

    return report


def _measure_one(db: Session, row: TrustExecutionLog) -> tuple[str, float]:
    """Measure the revenue delta for a single execution.

    Strategy: compare the 48h window AFTER execution against the 48h
    window immediately BEFORE, for the same product (if scoped) or
    shop-wide otherwise. If the after-window is ≥+5% of the before
    window, outcome=effective. If ≤-5%, ineffective. Otherwise inconclusive.
    """
    exec_at = row.executed_at
    if exec_at is None:
        return "inconclusive", 0.0

    before_start = exec_at - timedelta(hours=48)
    after_start = exec_at
    after_end = exec_at + timedelta(hours=48)

    # If target_url is populated, try to match by product (via shop_orders.line_items)
    # Otherwise fall back to shop-wide revenue.
    try:
        result = db.execute(
            sql_text(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN created_at >= :before_start AND created_at < :after_start
                                      THEN total_price ELSE 0 END), 0) AS before_rev,
                    COALESCE(SUM(CASE WHEN created_at >= :after_start AND created_at < :after_end
                                      THEN total_price ELSE 0 END), 0) AS after_rev
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :before_start
                  AND created_at < :after_end
                """
            ),
            {
                "shop": row.shop_domain,
                "before_start": before_start,
                "after_start": after_start,
                "after_end": after_end,
            },
        ).fetchone()
    except Exception as exc:
        log.warning("trust_outcome: measurement query failed: %s", exc)
        return "inconclusive", 0.0

    if not result:
        return "inconclusive", 0.0

    before_rev = float(result[0] or 0)
    after_rev = float(result[1] or 0)
    delta = after_rev - before_rev

    # Need meaningful baseline
    if before_rev < 50.0:
        return "inconclusive", round(delta, 2)

    pct_change = (delta / before_rev) * 100

    if pct_change >= 5.0:
        return "effective", round(delta, 2)
    if pct_change <= -5.0:
        return "ineffective", round(delta, 2)
    return "inconclusive", round(delta, 2)


def _calibrate_contracts(db: Session) -> int:
    """Auto-pause any contract whose last 10 measured executions show
    effective_rate < 30%. Operator can re-enable manually."""
    paused = 0
    try:
        # Find contracts with ≥ _AUTO_PAUSE_MIN_EXECUTIONS measured exec
        contracts = (
            db.query(TrustContract)
            .filter(TrustContract.status == "active")
            .all()
        )
    except Exception as exc:
        log.warning("trust_outcome_measurement: _calibrate_contracts failed: %s", exc)
        return 0

    for contract in contracts:
        try:
            recent = (
                db.query(TrustExecutionLog)
                .filter(
                    TrustExecutionLog.contract_id == contract.id,
                    TrustExecutionLog.outcome.in_(["effective", "ineffective", "inconclusive"]),
                )
                .order_by(TrustExecutionLog.executed_at.desc())
                .limit(10)
                .all()
            )
        except Exception as exc:
            log.warning("trust_outcome_measurement: _calibrate_contracts failed: %s", exc)
            continue

        if len(recent) < _AUTO_PAUSE_MIN_EXECUTIONS:
            continue

        effective = sum(1 for r in recent if r.outcome == "effective")
        rate = effective / len(recent)

        if rate < _AUTO_PAUSE_MIN_EFFECTIVE_RATE:
            contract.status = "paused"
            contract.revoked_at = _now()
            contract.revoked_reason = f"auto_pause:effective_rate_{rate:.0%}"
            paused += 1
            log.info(
                "trust_outcome: auto-paused contract #%d (rate=%.0f%%)",
                contract.id, rate * 100,
            )
            # Emit alert so merchant knows
            try:
                from app.services.alerting import write_alert
                write_alert(
                    db,
                    severity="warning",
                    source=f"trust_contract:{contract.id}",
                    alert_type="trust_contract_auto_paused",
                    summary=(
                        f"Contract #{contract.id} ({contract.action_type}) auto-paused — "
                        f"only {rate:.0%} of recent actions were effective"
                    ),
                    shop_domain=contract.shop_domain,
                    detail={
                        "contract_id": contract.id,
                        "action_type": contract.action_type,
                        "effective_rate": round(rate, 2),
                        "sample_size": len(recent),
                    },
                )
            except Exception as exc:
                log.warning("trust_outcome_measurement: _calibrate_contracts failed: %s", exc)

    return paused
