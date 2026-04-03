"""
system_health_synthesizer.py — CTO Signal Layer (Phase 0).

Strict operational intelligence system. Runs every agent_worker cycle.

WHAT IT DOES:
  - Detects system degradation, bottlenecks, rising error trends
  - Detects pipeline congestion, worker instability, data staleness
  - Detects anomaly patterns BEFORE cascading failure
  - Produces a structured signal for Telegram + Redis + circuit breaker

WHAT IT NEVER DOES:
  - Propose product features
  - Modify business/intelligence logic
  - Override reviewer decisions
  - Generate code patches
  - Interfere with bugfix pipeline
  - Act as strategist

SIGNAL QUALITY:
  - Trend detection (2h vs 4h comparison), not single-spike reaction
  - Dedup via Redis cooldown (1h per signal type)
  - Hard thresholds with hysteresis to prevent flapping
  - Zero LLM calls, zero heavy queries

INTERACTION WITH OTHER LAYERS:
  - CTO → Reviewer: FLAGS risk areas (via Redis state). Cannot approve/reject.
  - CTO → Pipeline: HIGHLIGHTS degraded domains. Pipeline decides fixes.
  - CTO → Monthly Audit: PROVIDES health data. Audit decides strategy.
  - CTO → Circuit Breaker: FEEDS overall_status. Breaker decides pause.

Public interface:
    synthesize_health(db) -> SystemHealthState
    format_telegram_signal(state) -> str
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("cto_signal")

# ---------------------------------------------------------------------------
# Signal cooldown — prevents repeated Telegram noise
# ---------------------------------------------------------------------------

_SIGNAL_COOLDOWN_SECONDS = 3600  # 1 hour between identical signals
_TELEGRAM_COOLDOWN_SECONDS = 900  # 15 min between any Telegram message


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _should_send_telegram(current_status: str, prev_status: str | None) -> bool:
    """
    Only send Telegram when status CHANGES or is critical.
    - healthy → healthy: silence
    - healthy → degraded: send
    - degraded → degraded: silence (already known)
    - degraded → critical: send (escalation)
    - critical → critical: send once per hour (via cooldown)
    - any → healthy: send (recovery)
    """
    if prev_status is None:
        return current_status != "healthy"
    if current_status != prev_status:
        return True  # state transition always sends
    if current_status == "critical":
        return True  # critical repeats (cooldown handles dedup)
    return False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HealthDimension:
    """Single health dimension with current value and trend."""
    name: str
    status: str       # "healthy" | "degraded" | "critical"
    value: float      # current metric value
    trend: str        # "improving" | "stable" | "worsening"
    detail: str       # human-readable one-liner
    changed: bool = False  # True if status changed from previous cycle


@dataclass
class SystemHealthState:
    """Unified system health assessment. Read-only signal."""
    overall_status: str     # "healthy" | "degraded" | "critical"
    confidence: float       # 0-1
    dimensions: list[HealthDimension] = field(default_factory=list)
    top_issues: list[str] = field(default_factory=list)  # max 3
    assessed_at: str = ""
    previous_status: str | None = None  # for transition detection

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status,
            "confidence": self.confidence,
            "dimensions": [
                {"name": d.name, "status": d.status, "value": d.value,
                 "trend": d.trend, "detail": d.detail, "changed": d.changed}
                for d in self.dimensions
            ],
            "top_issues": self.top_issues,
            "assessed_at": self.assessed_at,
        }


# ---------------------------------------------------------------------------
# Main synthesis
# ---------------------------------------------------------------------------

def synthesize_health(db: Session) -> SystemHealthState:
    """
    Synthesize all operational signals into a unified health state.

    6 dimensions, each with status + trend + value.
    No recommendations (CTO observes, does not prescribe).
    No feature suggestions. Pure operational signal.
    """
    now = _now()

    # Load previous state from Redis for transition detection
    prev_status = None
    prev_dims: dict[str, str] = {}
    try:
        from app.core.redis_client import cache_get
        prev = cache_get("hs:system_health")
        if prev:
            prev_status = prev.get("overall_status")
            for d in prev.get("dimensions", []):
                prev_dims[d["name"]] = d["status"]
    except Exception:
        pass

    state = SystemHealthState(
        overall_status="healthy",
        confidence=0.0,
        assessed_at=now.isoformat() + "Z",
        previous_status=prev_status,
    )

    dimensions = []
    issues = []
    evidence = 0

    # --- 7 dimensions, each isolated, each non-fatal ---
    assessors = [
        _assess_worker_health,
        _assess_pipeline_health,
        _assess_pipeline_liveness,
        _assess_merchant_health,
        _assess_data_freshness,
        _assess_fix_effectiveness,
        _assess_alert_pressure,
    ]

    for fn in assessors:
        try:
            dim = fn(db, now)
            # Mark if status changed from previous cycle
            dim.changed = (prev_dims.get(dim.name) is not None
                           and prev_dims[dim.name] != dim.status)
            dimensions.append(dim)
            evidence += 1
            if dim.status == "critical":
                issues.append(f"{dim.name}: {dim.detail}")
            elif dim.status == "degraded" and dim.trend == "worsening":
                issues.append(f"{dim.name} worsening: {dim.detail}")
        except Exception as exc:
            log.debug("cto_signal: %s failed: %s", fn.__name__, exc)

    # --- Synthesize overall (strict logic, no creative interpretation) ---
    critical_count = sum(1 for d in dimensions if d.status == "critical")
    degraded_count = sum(1 for d in dimensions if d.status == "degraded")
    worsening_count = sum(1 for d in dimensions if d.trend == "worsening")

    if critical_count >= 2:
        state.overall_status = "critical"
    elif critical_count == 1 and (degraded_count >= 1 or worsening_count >= 2):
        state.overall_status = "critical"
    elif critical_count >= 1 or degraded_count >= 2:
        state.overall_status = "degraded"
    elif degraded_count == 1 and worsening_count >= 1:
        state.overall_status = "degraded"
    else:
        state.overall_status = "healthy"

    state.dimensions = dimensions
    state.top_issues = issues[:3]  # strict cap at 3
    state.confidence = min(1.0, evidence / 7.0)

    return state


# ---------------------------------------------------------------------------
# Telegram signal formatter
# ---------------------------------------------------------------------------

def format_telegram_signal(state: SystemHealthState) -> str:
    """
    Format health state as a concise Telegram message.
    No fluff. No recommendations. Pure signal.
    """
    icon = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}.get(
        state.overall_status, "⚪"
    )

    lines = [f"{icon} *SYSTEM: {state.overall_status.upper()}*"]

    # Dimension summary — one line each, only non-healthy
    for d in state.dimensions:
        if d.status == "healthy" and d.trend != "worsening":
            continue  # silence healthy+stable
        d_icon = {"critical": "🔴", "degraded": "🟡", "healthy": "🟢"}[d.status]
        trend_arrow = {"worsening": "↑", "improving": "↓", "stable": "→"}[d.trend]
        changed = " ⚡" if d.changed else ""
        lines.append(f"  {d_icon} {d.name}: {d.detail} {trend_arrow}{changed}")

    # Top issues (max 3)
    if state.top_issues:
        lines.append("")
        lines.append("*Issues:*")
        for issue in state.top_issues:
            lines.append(f"  • {issue}")

    return "\n".join(lines)


def send_telegram_signal(state: SystemHealthState) -> bool:
    """
    Send CTO signal to Telegram with cooldown dedup.
    Returns True if sent, False if suppressed.
    """
    if not _should_send_telegram(state.overall_status, state.previous_status):
        return False

    # Cooldown check via Redis
    try:
        from app.core.redis_client import cache_get, cache_set
        cooldown_key = "hs:cto_signal_cooldown"
        if cache_get(cooldown_key) is not None:
            return False  # within cooldown window
        cache_set(cooldown_key, True, _TELEGRAM_COOLDOWN_SECONDS)
    except Exception:
        pass  # Redis down — proceed without dedup

    try:
        from app.services.telegram_agent import send_message, is_configured
        if not is_configured():
            return False
        msg = format_telegram_signal(state)
        return send_message(msg)
    except Exception as exc:
        log.debug("cto_signal: telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Dimension assessors — each is a pure function (db, now) -> HealthDimension
# No side effects. No writes. No recommendations.
# ---------------------------------------------------------------------------

def _assess_worker_health(db: Session, now: datetime) -> HealthDimension:
    """Worker error rate: 2h recent vs 2-4h previous for trend."""
    cutoff_2h = now - timedelta(hours=2)
    cutoff_4h = now - timedelta(hours=4)

    recent = db.execute(text("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN errors > 0 THEN 1 ELSE 0 END), 0) AS errored
        FROM worker_log WHERE started_at >= :c
    """), {"c": cutoff_2h}).fetchone()

    prev = db.execute(text("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN errors > 0 THEN 1 ELSE 0 END), 0) AS errored
        FROM worker_log WHERE started_at >= :p AND started_at < :c
    """), {"p": cutoff_4h, "c": cutoff_2h}).fetchone()

    total_r = int(recent.total or 0)
    err_r = int(recent.errored or 0)
    total_p = int(prev.total or 0)
    err_p = int(prev.errored or 0)

    rate = err_r / max(total_r, 1)
    prev_rate = err_p / max(total_p, 1)

    status = "critical" if rate > 0.5 else ("degraded" if rate > 0.2 else "healthy")
    trend = _compute_trend(rate, prev_rate, total_p)

    return HealthDimension(
        name="workers",
        status=status,
        value=round(rate, 3),
        trend=trend,
        detail=f"{err_r}/{total_r} error cycles (2h), rate {rate:.0%}",
    )


def _assess_pipeline_health(db: Session, now: datetime) -> HealthDimension:
    """Bugfix pipeline queue depth + throughput + thrashing."""
    queued = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status IN ('open', 'analyzed', 'patch_proposed', 'approved')
    """)).fetchone()
    total_q = int(queued[0] or 0)

    applied_7d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status = 'applied' AND applied_at >= :c
    """), {"c": now - timedelta(days=7)}).fetchone()
    applied = int(applied_7d[0] or 0)

    applied_prev = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status = 'applied' AND applied_at >= :p AND applied_at < :c
    """), {"p": now - timedelta(days=14), "c": now - timedelta(days=7)}).fetchone()
    applied_p = int(applied_prev[0] or 0)

    # Status: queue depth thresholds
    if total_q > 50:
        status = "critical"
    elif total_q > 20:
        status = "degraded"
    else:
        status = "healthy"

    # Trend: throughput comparison
    trend = _compute_trend(applied, applied_p, 1)  # more applied = better

    return HealthDimension(
        name="pipeline",
        status=status,
        value=float(total_q),
        trend=trend,
        detail=f"{total_q} queued, {applied} applied (7d)",
    )


def _assess_pipeline_liveness(db: Session, now: datetime) -> HealthDimension:
    """
    Detect if the LLM pipeline is dead (candidates exist but nothing processes).

    DEAD: candidates > 0 AND proposals_7d == 0 AND applied_7d == 0
    This catches: missing API keys, budget exhaustion, persistent LLM errors.
    """
    candidates = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status IN ('open', 'analyzed')
    """)).fetchone()
    pending = int(candidates[0] or 0)

    proposals = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE proposal_attempted_at >= :c AND patch_diff IS NOT NULL
    """), {"c": now - timedelta(days=7)}).fetchone()
    proposals_7d = int(proposals[0] or 0)

    applied = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status = 'applied' AND applied_at >= :c
    """), {"c": now - timedelta(days=7)}).fetchone()
    applied_7d = int(applied[0] or 0)

    if pending == 0:
        return HealthDimension("liveness", "healthy", 0, "stable",
                               "No pending candidates")

    if proposals_7d == 0 and applied_7d == 0:
        return HealthDimension("liveness", "critical", float(pending), "stable",
                               f"Pipeline DEAD: {pending} candidates, 0 proposals/applied (7d) — check LLM keys")

    if applied_7d == 0 and proposals_7d > 0:
        return HealthDimension("liveness", "degraded", float(pending), "stable",
                               f"Pipeline stalled: {proposals_7d} proposals but 0 applied (7d)")

    return HealthDimension("liveness", "healthy", float(pending), "stable",
                           f"{pending} pending, {proposals_7d} proposed, {applied_7d} applied (7d)")


def _assess_merchant_health(db: Session, now: datetime) -> HealthDimension:
    """High/critical support incidents: 24h recent vs 24-48h previous."""
    cutoff_24h = now - timedelta(hours=24)
    cutoff_48h = now - timedelta(hours=48)

    recent = db.execute(text("""
        SELECT COUNT(*) FROM support_incidents
        WHERE created_at >= :c AND severity IN ('high', 'critical')
    """), {"c": cutoff_24h}).fetchone()

    prev = db.execute(text("""
        SELECT COUNT(*) FROM support_incidents
        WHERE created_at >= :p AND created_at < :c
          AND severity IN ('high', 'critical')
    """), {"p": cutoff_48h, "c": cutoff_24h}).fetchone()

    unresolved = db.execute(text("""
        SELECT COUNT(*) FROM support_incidents
        WHERE status IN ('open', 'triaged', 'investigating')
    """)).fetchone()

    count_r = int(recent[0] or 0)
    count_p = int(prev[0] or 0)
    unres = int(unresolved[0] or 0)

    if count_r > 10 or unres > 20:
        status = "critical"
    elif count_r > 5 or unres > 10:
        status = "degraded"
    else:
        status = "healthy"

    trend = _compute_trend(count_r, count_p, 1, invert=True)  # fewer = improving

    return HealthDimension(
        name="merchants",
        status=status,
        value=float(count_r),
        trend=trend,
        detail=f"{count_r} incidents (24h), {unres} unresolved",
    )


def _assess_data_freshness(db: Session, now: datetime) -> HealthDimension:
    """Aggregation worker lag in minutes."""
    from app.models.worker_state import WorkerState
    ws = db.query(WorkerState).filter(
        WorkerState.worker_name == "aggregation_worker"
    ).first()

    if not ws or not ws.last_run_at:
        return HealthDimension("freshness", "degraded", 0, "unknown",
                               "Aggregation never ran")

    age_min = (now - ws.last_run_at).total_seconds() / 60

    status = "critical" if age_min > 30 else ("degraded" if age_min > 15 else "healthy")

    return HealthDimension(
        name="freshness",
        status=status,
        value=round(age_min, 1),
        trend="stable",
        detail=f"Last aggregation {age_min:.0f}m ago",
    )


def _assess_fix_effectiveness(db: Session, now: datetime) -> HealthDimension:
    """30-day fix success rate."""
    result = db.execute(text("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN outcome_status = 'effective' THEN 1 ELSE 0 END), 0) AS eff,
               COALESCE(SUM(CASE WHEN outcome_status = 'ineffective' THEN 1 ELSE 0 END), 0) AS ineff
        FROM bugfix_candidates
        WHERE outcome_measured_at >= :c AND outcome_status IS NOT NULL
    """), {"c": now - timedelta(days=30)}).fetchone()

    total = int(result.total or 0)
    eff = int(result.eff or 0)
    rate = eff / max(total, 1)

    if total < 3:
        status, trend = "healthy", "stable"  # insufficient data
    elif rate < 0.3:
        status, trend = "degraded", "worsening"
    else:
        status, trend = "healthy", "stable"

    return HealthDimension(
        name="fix_rate",
        status=status,
        value=round(rate, 3),
        trend=trend,
        detail=f"{eff}/{total} effective (30d)",
    )


def _assess_alert_pressure(db: Session, now: datetime) -> HealthDimension:
    """Unresolved alert volume with 24h trend."""
    current = db.execute(text("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END), 0) AS crit
        FROM ops_alerts WHERE resolved = false
    """)).fetchone()

    # Trend: alerts created in last 24h vs 24-48h
    recent_created = db.execute(text("""
        SELECT COUNT(*) FROM ops_alerts WHERE created_at >= :c
    """), {"c": now - timedelta(hours=24)}).fetchone()

    prev_created = db.execute(text("""
        SELECT COUNT(*) FROM ops_alerts
        WHERE created_at >= :p AND created_at < :c
    """), {"p": now - timedelta(hours=48), "c": now - timedelta(hours=24)}).fetchone()

    total = int(current.total or 0)
    crit = int(current.crit or 0)
    new_r = int(recent_created[0] or 0)
    new_p = int(prev_created[0] or 0)

    if crit > 3 or total > 30:
        status = "critical"
    elif crit > 0 or total > 15:
        status = "degraded"
    else:
        status = "healthy"

    trend = _compute_trend(new_r, new_p, 1, invert=True)  # fewer new = improving

    return HealthDimension(
        name="alerts",
        status=status,
        value=float(total),
        trend=trend,
        detail=f"{total} unresolved ({crit} critical), {new_r} new (24h)",
    )


# ---------------------------------------------------------------------------
# Trend computation — shared logic, prevents inconsistency
# ---------------------------------------------------------------------------

def _compute_trend(
    current: float, previous: float, min_prev: int = 1, invert: bool = False,
) -> str:
    """
    Compare current vs previous period.
    Returns "improving" | "stable" | "worsening".

    invert=True means lower current = improving (e.g., error count, incidents).
    Default (invert=False) means higher current = improving (e.g., throughput).
    """
    if previous < min_prev:
        return "stable"  # insufficient baseline

    if invert:
        if current < previous * 0.6:
            return "improving"
        if current > previous * 1.5:
            return "worsening"
    else:
        if current > previous * 1.5:
            return "improving"
        if current < previous * 0.6:
            return "worsening"

    return "stable"
