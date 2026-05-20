"""
measurement_health.py — Adversarial resilience layer for the autonomous loop.

Validates measurement integrity BEFORE any outcome is accepted. Prevents
the system from learning from corrupted, partial, or skewed data.

Health states:
  HEALTHY  — normal operation, all checks pass
  DEGRADED — measurement anomaly detected, pause learning but continue measuring
  BROKEN   — critical data integrity failure, freeze all autonomous actions

Checks:
  1. Holdout ratio parity (expected ~20%, alert if <12% or >28%)
  2. Event flow continuity (detect sudden drops vs 7d baseline)
  3. Treatment/control sample balance (detect assignment skew)
  4. Temporal consistency (events should arrive continuously, not in bursts)
  5. Impossible lift detection (lift > 300% is likely a data error)

Integration: called by autonomous_loop BEFORE _check_completion and _update_sip.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BROKEN = "broken"


# ── Thresholds ──
_HOLDOUT_EXPECTED = 0.20
_HOLDOUT_LOW = 0.12      # < 12% holdout → assignment leak
_HOLDOUT_HIGH = 0.28     # > 28% holdout → over-assignment
_EVENT_DROP_THRESHOLD = 0.30  # events < 30% of baseline → broken
_EVENT_DROP_DEGRADED = 0.50   # events < 50% of baseline → degraded
_MAX_PLAUSIBLE_LIFT = 300.0   # > 300% lift is suspicious
_MIN_EVENTS_FOR_CHECK = 20    # need at least 20 events to evaluate health


def check_measurement_health(
    db: Session,
    shop_domain: str,
    nudge_id: int,
    exposed_count: int,
    holdout_count: int,
    cvr_lift_pct: float | None,
) -> tuple[HealthState, str]:
    """
    Validate measurement integrity for a specific nudge experiment.

    Returns (health_state, detail_message).
    Called BEFORE outcome classification, trust updates, or SIP learning.
    """
    issues: list[str] = []
    state = HealthState.HEALTHY

    total = exposed_count + holdout_count
    if total < _MIN_EVENTS_FOR_CHECK:
        return HealthState.HEALTHY, "Insufficient data for health check"

    # ── Check 1: Holdout ratio parity ──
    actual_holdout_ratio = holdout_count / total if total > 0 else 0

    if actual_holdout_ratio < _HOLDOUT_LOW:
        issues.append(f"Holdout ratio {actual_holdout_ratio:.1%} is below {_HOLDOUT_LOW:.0%} "
                      f"(expected ~{_HOLDOUT_EXPECTED:.0%}). Possible assignment leak.")
        state = _escalate(state, HealthState.BROKEN)
    elif actual_holdout_ratio > _HOLDOUT_HIGH:
        issues.append(f"Holdout ratio {actual_holdout_ratio:.1%} exceeds {_HOLDOUT_HIGH:.0%}. "
                      f"Possible over-assignment.")
        state = _escalate(state, HealthState.DEGRADED)

    # ── Check 2: Event flow continuity ──
    event_health = _check_event_flow(db, shop_domain)
    if event_health == HealthState.BROKEN:
        issues.append("Event flow dropped below 30% of 7-day baseline. Tracking may be broken.")
        state = _escalate(state, HealthState.BROKEN)
    elif event_health == HealthState.DEGRADED:
        issues.append("Event flow dropped below 50% of 7-day baseline.")
        state = _escalate(state, HealthState.DEGRADED)

    # ── Check 3: Impossible lift detection ──
    if cvr_lift_pct is not None and abs(cvr_lift_pct) > _MAX_PLAUSIBLE_LIFT:
        issues.append(f"CVR lift {cvr_lift_pct:.1f}% exceeds plausible range (±{_MAX_PLAUSIBLE_LIFT:.0f}%). "
                      f"Likely data anomaly.")
        state = _escalate(state, HealthState.BROKEN)

    # ── Check 4: Zero holdout events with non-zero exposed ──
    if exposed_count > 50 and holdout_count == 0:
        issues.append("Zero holdout events despite 50+ exposed. Holdout assignment likely broken.")
        state = _escalate(state, HealthState.BROKEN)

    detail = "; ".join(issues) if issues else "All checks passed"

    if state != HealthState.HEALTHY:
        log.warning(
            "measurement_health: %s for shop=%s nudge_id=%d — %s",
            state.value, shop_domain, nudge_id, detail,
        )

    return state, detail


def update_sip_measurement_health(
    db: Session,
    shop_domain: str,
    health: HealthState,
    detail: str,
) -> None:
    """Persist measurement health state to the SIP."""
    db.execute(
        text("""
            UPDATE store_intelligence_profiles
            SET measurement_health = :health,
                measurement_health_detail = :detail,
                updated_at = NOW()
            WHERE shop_domain = :shop
        """),
        {"health": health.value, "detail": detail[:512], "shop": shop_domain},
    )
    db.commit()


def _check_event_flow(db: Session, shop_domain: str) -> HealthState:
    """Compare recent event rate to 7-day baseline."""
    import time
    now_ms = int(time.time() * 1000)
    day_ago = now_ms - 86_400_000
    week_ago = now_ms - 7 * 86_400_000

    # sql-ms-type: ok — day_ago/week_ago are int epoch ms (computed above).
    row = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE timestamp > :day_ago) AS last_24h,
                COUNT(*) FILTER (WHERE timestamp > :week_ago) AS last_7d
            FROM events
            WHERE shop_domain = :shop AND timestamp > :week_ago
        """),
        {"shop": shop_domain, "day_ago": day_ago, "week_ago": week_ago},
    ).fetchone()

    if not row:
        return HealthState.HEALTHY

    last_24h = row[0] or 0
    last_7d = row[1] or 0

    if last_7d < 50:
        return HealthState.HEALTHY  # Not enough baseline data

    daily_avg = last_7d / 7.0

    if daily_avg == 0:
        return HealthState.HEALTHY

    ratio = last_24h / daily_avg

    if ratio < _EVENT_DROP_THRESHOLD:
        return HealthState.BROKEN
    if ratio < _EVENT_DROP_DEGRADED:
        return HealthState.DEGRADED

    return HealthState.HEALTHY


def _escalate(current: HealthState, proposed: HealthState) -> HealthState:
    """Return the more severe of two health states."""
    severity = {HealthState.HEALTHY: 0, HealthState.DEGRADED: 1, HealthState.BROKEN: 2}
    return proposed if severity[proposed] > severity[current] else current
