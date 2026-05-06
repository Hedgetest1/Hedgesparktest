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
_TELEGRAM_COOLDOWN_TRANSITION_SECONDS = 1800   # 30 min — debounce flapping (was 5min: too noisy under flap)
_TELEGRAM_COOLDOWN_REPEAT_CRITICAL_SECONDS = 43200  # 12 hours for repeat CRITICAL (was 4h: 672 spam observed in 7d)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _should_send_telegram(current_status: str, prev_status: str | None) -> tuple[bool, str]:
    """
    Decide whether to send Telegram and which cooldown to use.
    Returns (should_send, cooldown_type).
    - healthy → healthy: silence
    - healthy → degraded: send (transition)
    - degraded → degraded: silence
    - degraded → critical: send (transition, urgent)
    - critical → critical: send with 4h cooldown (repeat_critical)
    - any → healthy: send (recovery)
    """
    if prev_status is None:
        if current_status != "healthy":
            return True, "transition"
        return False, ""
    if current_status != prev_status:
        return True, "transition"  # state transition always sends
    if current_status == "critical":
        return True, "repeat_critical"  # repeat uses long cooldown
    return False, ""


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
    except Exception as exc:
        log.warning("system_health_synthesizer: synthesize_health failed: %s", exc)

    state = SystemHealthState(
        overall_status="healthy",
        confidence=0.0,
        assessed_at=now.isoformat() + "Z",
        previous_status=prev_status,
    )

    dimensions = []
    issues = []
    evidence = 0

    # --- 10 dimensions, each isolated, each non-fatal ---
    # Operational dims (workers..alerts) drive overall_status.
    # Strategic dims (memory, llm_usage, cost) gate Telegram pings to founder
    # via _STRATEGIC_DIMENSIONS — names must match those declared below.
    assessors = [
        _assess_worker_health,
        _assess_pipeline_health,
        _assess_pipeline_liveness,
        _assess_merchant_health,
        _assess_data_freshness,
        _assess_fix_effectiveness,
        _assess_alert_pressure,
        _assess_memory,
        _assess_llm_usage,
        _assess_cost,
    ]

    for fn in assessors:
        try:
            dim = fn(db, now)
            # Mark if status changed from previous cycle
            dim.changed = (prev_dims.get(dim.name) is not None
                           and prev_dims[dim.name] != dim.status)
            dimensions.append(dim)
            evidence += 1
        except Exception as exc:
            log.debug("cto_signal: %s failed: %s", fn.__name__, exc)

    # --- Synthesize overall (strict logic, no creative interpretation) ---
    # Separate founder-actionable dimensions from ops-only metrics.
    # Alert accumulation and fix success rate are operational signals that
    # should not wake the founder — they are visible in /status and the
    # daily digest instead.
    _OPS_ONLY_DIMENSIONS = {"alerts", "fix_rate"}
    actionable_dims = [d for d in dimensions if d.name not in _OPS_ONLY_DIMENSIONS]
    ops_dims = [d for d in dimensions if d.name in _OPS_ONLY_DIMENSIONS]

    # 2026-04-18: surface every founder-actionable non-healthy dim + any
    # critical ops-only dim, SORTED by severity so the Telegram 3-line cap
    # always shows the worst first. Priority order:
    #   (0) critical actionable  (1) degraded+worsening actionable
    #   (2) degraded+stable actionable  (3) critical ops-only
    def _severity_key(d):
        if d.name in _OPS_ONLY_DIMENSIONS:
            return (3, d.name)  # ops critical always last
        if d.status == "critical":
            return (0, d.name)
        if d.status == "degraded" and d.trend == "worsening":
            return (1, d.name)
        return (2, d.name)  # degraded+stable

    _surfaceable = [
        d for d in dimensions
        if (d.name not in _OPS_ONLY_DIMENSIONS and d.status in ("critical", "degraded"))
        or (d.name in _OPS_ONLY_DIMENSIONS and d.status == "critical")
    ]
    for _dim in sorted(_surfaceable, key=_severity_key):
        if _dim.name in _OPS_ONLY_DIMENSIONS:
            issues.append(f"{_dim.name} (ops): {_dim.detail}")
        elif _dim.status == "critical":
            issues.append(f"{_dim.name}: {_dim.detail}")
        else:
            _suffix = " (worsening)" if _dim.trend == "worsening" else ""
            issues.append(f"{_dim.name}: {_dim.detail}{_suffix}")

    actionable_critical = sum(1 for d in actionable_dims if d.status == "critical")
    actionable_degraded = sum(1 for d in actionable_dims if d.status == "degraded")
    actionable_worsening = sum(1 for d in actionable_dims if d.trend == "worsening")
    ops_critical = sum(1 for d in ops_dims if d.status == "critical")

    if actionable_critical >= 2:
        state.overall_status = "critical"
    elif actionable_critical == 1 and (actionable_degraded >= 1 or actionable_worsening >= 2):
        state.overall_status = "critical"
    elif actionable_critical >= 1 or actionable_degraded >= 2:
        state.overall_status = "degraded"
    elif actionable_degraded == 1 and actionable_worsening >= 1:
        state.overall_status = "degraded"
    elif ops_critical > 0:
        # Ops-only issues don't escalate to CRITICAL, but mark as degraded
        # so the founder sees a yellow flag in /status, not red Telegram spam.
        state.overall_status = "degraded"
    else:
        state.overall_status = "healthy"

    state.dimensions = dimensions
    state.top_issues = issues[:3]  # strict cap at 3
    state.confidence = min(1.0, evidence / 10.0)

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


_STRATEGIC_DIMENSIONS = frozenset({"memory", "llm_usage", "cost"})


def _is_strategic_critical(state: SystemHealthState) -> bool:
    """Founder-doctrine strategic-only Telegram gate (2026-05-05).

    A critical CTO signal reaches Telegram ONLY when at least one
    *strategic* dimension (memory/llm_usage/cost — i.e. capacity or
    spend) is critical. Operational dimensions (liveness, pipeline,
    alerts) drive the overall_status indicator on /ops/system-health
    but are handled autonomously by the brain — they never page the
    founder.

    Names in `_STRATEGIC_DIMENSIONS` MUST match the `name=` declared
    by `_assess_memory`, `_assess_llm_usage`, `_assess_cost` (asserted
    by `audit_strategic_dimension_names_match_emitters.py`) — otherwise
    the gate suppresses every signal silently, which is the exact
    silent-failure mode this audit prevents.

    `audit_telegram_strategic_only.py` blocks regressions of this gate.
    """
    for d in (state.dimensions or []):
        if d.status == "critical" and d.name in _STRATEGIC_DIMENSIONS:
            return True
    return False


def send_telegram_signal(state: SystemHealthState) -> bool:
    """
    Send CTO signal to Telegram with cooldown dedup.
    Returns True if sent, False if suppressed.
    """
    if not _is_strategic_critical(state):
        log.info(
            "cto_signal: strategic-only gate suppressed Telegram — "
            "no strategic dimension (memory/llm_usage/cost) is critical"
        )
        return False
    should_send, cooldown_type = _should_send_telegram(state.overall_status, state.previous_status)
    if not should_send:
        return False

    # Pick cooldown duration based on signal type
    cooldown_seconds = (
        _TELEGRAM_COOLDOWN_REPEAT_CRITICAL_SECONDS
        if cooldown_type == "repeat_critical"
        else _TELEGRAM_COOLDOWN_TRANSITION_SECONDS
    )
    cooldown_key = f"hs:cto_signal_cooldown:{cooldown_type}"

    # Cooldown check via Redis (with file-based fallback)
    try:
        from app.core.redis_client import cache_get, cache_set
        if cache_get(cooldown_key) is not None:
            return False  # within cooldown window
        cache_set(cooldown_key, True, cooldown_seconds)
    except Exception as exc:
        log.warning("system_health_synthesizer: send_telegram_signal failed: %s", exc)
        # Redis down — use file-based cooldown to prevent spam
        import tempfile, os, time as _time
        cooldown_file = os.path.join(tempfile.gettempdir(), f"hs_cto_{cooldown_type}")
        try:
            if os.path.exists(cooldown_file):
                mtime = os.path.getmtime(cooldown_file)
                if _time.time() - mtime < cooldown_seconds:
                    return False  # within file-based cooldown
            with open(cooldown_file, "w") as f:
                f.write(str(_time.time()))
        except Exception as exc:
            log.warning("system_health_synthesizer: send_telegram_signal failed: %s", exc)
            pass  # last resort: allow send

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

    # Status: queue depth thresholds — but downgrade to `degraded` if the
    # depth is due to an external blocker (LLM not trying), same logic as
    # _assess_pipeline_liveness. Critical is reserved for system fault.
    if total_q > 50:
        status = "critical"
        try:
            from app.core.llm_budget import get_usage_summary
            usage = get_usage_summary()
            if int(usage.get("global_calls_today") or 0) == 0:
                # No LLM activity → external blocker, not system fault
                status = "degraded"
        except Exception:
            pass  # SILENT-EXCEPT-OK: get_usage_summary best-effort enrichment on the degraded-status path; if the LLM-budget probe itself errors, the surrounding status synthesis must still complete and return a coherent payload — raising would 500 /ops/system-health.
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

    External-blocker awareness (added 2026-05-05): if `global_calls_today == 0`
    AND no LLM activity in 7d, the pipeline is not failing — it's not
    *trying*. That's an external dependency block (Anthropic credit
    exhausted, OAuth revoked, etc.) — `degraded`, not `critical`. Critical
    is reserved for "system actively broken"; this is "system parked,
    awaiting external resource".
    """
    candidates = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status IN ('open', 'analyzed')
    """)).fetchone()
    pending = int(candidates[0] or 0)

    proposals = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE proposal_attempted_at >= :c AND patch_diff IS NOT NULL
          AND status NOT IN ('discarded', 'rejected')
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
        # Distinguish "broken" (calls happen but fail) from "parked"
        # (no calls happen at all = external dep blocker).
        try:
            from app.core.llm_budget import get_usage_summary
            usage = get_usage_summary()
            calls_today = int(usage.get("global_calls_today") or 0)
            monthly_cost = float(usage.get("monthly_cost_eur") or 0.0)
        except Exception:
            calls_today, monthly_cost = -1, -1.0

        if calls_today == 0 and monthly_cost == 0.0:
            # Pipeline isn't trying → external dep block, not system fault
            return HealthDimension(
                "liveness", "degraded", float(pending), "stable",
                f"Pipeline parked: {pending} candidates, 0 LLM calls today — "
                f"awaiting external (likely Anthropic credit topup)",
            )
        # Calls happening but no proposals → genuinely broken
        return HealthDimension(
            "liveness", "critical", float(pending), "stable",
            f"Pipeline DEAD: {pending} candidates, 0 proposals/applied (7d), "
            f"{calls_today} LLM calls today — system fault",
        )

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
    """Unresolved alert volume with 24h trend.

    Uses DISTINCT (source, alert_type) pairs in the last 24h to assess
    pressure — recurring dedup'd rows for the same issue should not
    inflate the count and keep the system perpetually critical.
    """
    # Distinct alert TYPES in last 24h — group by alert_type only,
    # not source (sources include unique IDs like breach:42523 or
    # signal_webhooks:UUID that inflate the count for the same issue class).
    distinct = db.execute(text("""
        SELECT COUNT(DISTINCT alert_type) AS issue_count,
               COUNT(DISTINCT CASE WHEN severity = 'critical'
                     THEN alert_type END) AS crit_issues
        FROM ops_alerts
        WHERE resolved = false AND created_at >= :cutoff
    """), {"cutoff": now - timedelta(hours=24)}).fetchone()

    # Total unresolved (for informational detail only)
    total_row = db.execute(text("""
        SELECT COUNT(*) FROM ops_alerts WHERE resolved = false
    """)).fetchone()

    # Trend: new distinct types in last 24h vs 24-48h
    recent_created = db.execute(text("""
        SELECT COUNT(DISTINCT alert_type)
        FROM ops_alerts WHERE created_at >= :c
    """), {"c": now - timedelta(hours=24)}).fetchone()

    prev_created = db.execute(text("""
        SELECT COUNT(DISTINCT alert_type)
        FROM ops_alerts
        WHERE created_at >= :p AND created_at < :c
    """), {"p": now - timedelta(hours=48), "c": now - timedelta(hours=24)}).fetchone()

    issues = int(distinct.issue_count or 0)
    crit_issues = int(distinct.crit_issues or 0)
    total_all = int(total_row[0] or 0)
    new_r = int(recent_created[0] or 0)
    new_p = int(prev_created[0] or 0)

    # Status based on DISTINCT issue types, not raw row count
    if crit_issues > 5 or issues > 20:
        status = "critical"
    elif crit_issues > 2 or issues > 10:
        status = "degraded"
    else:
        status = "healthy"

    trend = _compute_trend(new_r, new_p, 1, invert=True)  # fewer new = improving

    return HealthDimension(
        name="alerts",
        status=status,
        value=float(issues),
        trend=trend,
        detail=f"{issues} active issues ({crit_issues} critical), {new_r} new types (24h), {total_all} total rows",
    )


# ---------------------------------------------------------------------------
# Strategic dimension assessors — gate Telegram pings to the founder.
# Names MUST match _STRATEGIC_DIMENSIONS exactly. audit_strategic_dimension_
# names_match_emitters.py blocks regressions of this contract.
# ---------------------------------------------------------------------------

def _read_proc_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of {key: kB} ints. Linux-only.
    Empty dict on non-Linux or read failure (caller decides degrade)."""
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                parts = line.split(":", 1)
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val_part = parts[1].strip().split()
                if not val_part:
                    continue
                try:
                    out[key] = int(val_part[0])
                except ValueError:
                    continue
    except Exception as exc:  # pragma: no cover — non-Linux / sandboxed
        log.debug("system_health_synthesizer: meminfo read failed: %s", exc)
    return out


def _assess_memory(db: Session, now: datetime) -> HealthDimension:
    """Linux RAM pressure via /proc/meminfo. Strategic dim — capacity
    decision the founder must hear about (more RAM, scale up, defer
    work). Operational pressure (heavy worker burst) does NOT count;
    we read MemAvailable which excludes reclaimable buffers/caches.

    Thresholds: critical >90% used, degraded >80% used.
    Probe unavailable → name="memory" status="healthy" value=0 with
    a clear detail string (degrade-open, never spam-page on missing data).
    """
    mi = _read_proc_meminfo()
    total_kb = mi.get("MemTotal", 0)
    avail_kb = mi.get("MemAvailable", 0)
    if total_kb <= 0 or avail_kb <= 0:
        return HealthDimension(
            name="memory",
            status="healthy",
            value=0.0,
            trend="stable",
            detail="memory probe unavailable (non-Linux or permission)",
        )
    used_pct = max(0.0, 1.0 - (avail_kb / total_kb))
    if used_pct >= 0.90:
        status = "critical"
    elif used_pct >= 0.80:
        status = "degraded"
    else:
        status = "healthy"

    return HealthDimension(
        name="memory",
        status=status,
        value=round(used_pct, 3),
        trend="stable",  # no historical baseline yet — keep stable
        detail=f"RAM {used_pct:.0%} used ({(total_kb - avail_kb) // 1024} MB / {total_kb // 1024} MB)",
    )


def _assess_llm_usage(db: Session, now: datetime) -> HealthDimension:
    """LLM monthly spend % of effective cap. Strategic dim —
    capacity/cost decision the founder owns (top up, switch model,
    cut traffic). Reads through llm_budget.get_usage_summary() so the
    same source-of-truth feeds /ops/llm-budget.

    Thresholds: critical >=90% of cap, degraded >=70% of cap.
    """
    try:
        from app.core.llm_budget import get_usage_summary
        summary = get_usage_summary()
    except Exception as exc:
        log.debug("system_health_synthesizer: llm summary failed: %s", exc)
        return HealthDimension(
            name="llm_usage",
            status="healthy",
            value=0.0,
            trend="stable",
            detail="llm budget probe unavailable",
        )
    cost = float(summary.get("monthly_cost_eur") or 0.0)
    cap = float(summary.get("monthly_cap_eur") or 0.0)
    pct = (cost / cap) if cap > 0 else 0.0
    if pct >= 0.90:
        status = "critical"
    elif pct >= 0.70:
        status = "degraded"
    else:
        status = "healthy"

    return HealthDimension(
        name="llm_usage",
        status=status,
        value=round(pct, 3),
        trend="stable",
        detail=f"LLM €{cost:.2f}/€{cap:.2f} ({pct:.0%} of monthly cap)",
    )


def _assess_cost(db: Session, now: datetime) -> HealthDimension:
    """Projected total monthly outflow (LLM + infra baseline) vs
    a configurable founder ceiling. Strategic dim — the signal that
    fires when the org is approaching the total monthly budget the
    founder set, regardless of which line item is responsible.

    Sources:
        LLM spend: get_usage_summary().monthly_cost_eur
        Infra baseline: INFRA_MONTHLY_BASELINE_EUR (env, default €30)
            covers VPS + Resend + Sentry + Anthropic creditless
            infrastructure that runs whether merchants exist or not.

    Thresholds: critical >=90% of TOTAL_COST_CAP_EUR (env, default
    €80 → €30 infra + €50 LLM ceiling per founder direttiva 2026-05-05).
    Degraded >=70% of cap.
    """
    try:
        from app.core.llm_budget import get_usage_summary
        summary = get_usage_summary()
        llm_cost = float(summary.get("monthly_cost_eur") or 0.0)
    except Exception as exc:
        log.debug("system_health_synthesizer: llm summary failed: %s", exc)
        llm_cost = 0.0

    import os as _os
    infra_baseline = float(_os.getenv("INFRA_MONTHLY_BASELINE_EUR", "30.0"))
    total_cap = float(_os.getenv("TOTAL_COST_CAP_EUR", "80.0"))
    total_cost = llm_cost + infra_baseline

    pct = (total_cost / total_cap) if total_cap > 0 else 0.0
    if pct >= 0.90:
        status = "critical"
    elif pct >= 0.70:
        status = "degraded"
    else:
        status = "healthy"

    return HealthDimension(
        name="cost",
        status=status,
        value=round(pct, 3),
        trend="stable",
        detail=f"total €{total_cost:.2f}/€{total_cap:.2f} ({pct:.0%}; LLM €{llm_cost:.2f} + infra €{infra_baseline:.2f})",
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
