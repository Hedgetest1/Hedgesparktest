"""
scaling_intelligence.py — Observe → Forecast → Recommend → Notify.

Collects daily system snapshots, projects growth trends, and generates
scaling recommendations for human review. No auto-scaling.

Public interface:
    capture_daily_snapshot(db) -> SystemSnapshot | None
    build_forecast(db, horizon_days=30) -> dict
    generate_recommendations(db) -> list[dict]
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.system_snapshot import SystemSnapshot
from app.models.scaling_recommendation import ScalingRecommendation

log = logging.getLogger("scaling_intelligence")

# Cooldowns (in-process, monotonic time)
_SNAPSHOT_COOLDOWN_S = 20 * 3600  # 20 hours — ensures at most 1/day
_RECOMMEND_COOLDOWN_S = 24 * 3600
_last_snapshot: float | None = None
_last_recommend: float | None = None

MIN_FORECAST_DAYS = 5  # need at least 5 days of snapshots for a forecast


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today():
    return date.today()


# ---------------------------------------------------------------------------
# Cooldown checks
# ---------------------------------------------------------------------------

def should_capture_snapshot() -> bool:
    if _last_snapshot is None:
        return True
    return (time.monotonic() - _last_snapshot) >= _SNAPSHOT_COOLDOWN_S


def mark_snapshot_captured():
    global _last_snapshot
    _last_snapshot = time.monotonic()


def should_generate_recommendations() -> bool:
    if _last_recommend is None:
        return True
    return (time.monotonic() - _last_recommend) >= _RECOMMEND_COOLDOWN_S


def mark_recommendations_generated():
    global _last_recommend
    _last_recommend = time.monotonic()


# ---------------------------------------------------------------------------
# PHASE 2 — Snapshot builder
# ---------------------------------------------------------------------------

def capture_daily_snapshot(db: Session) -> SystemSnapshot | None:
    """
    Capture a daily system snapshot. Idempotent per date_bucket.
    Returns the snapshot or None if already captured today.
    """
    today = _today()

    # Dedup: one per day
    existing = db.query(SystemSnapshot).filter(SystemSnapshot.date_bucket == today).first()
    if existing:
        return existing

    snapshot = SystemSnapshot(date_bucket=today)

    # Merchant counts
    try:
        from app.models.merchant import Merchant
        snapshot.active_merchants = (
            db.query(func.count(Merchant.id))
            .filter(Merchant.install_status == "active")
            .scalar() or 0
        )
        snapshot.billing_active_merchants = (
            db.query(func.count(Merchant.id))
            .filter(Merchant.install_status == "active", Merchant.billing_active == True)
            .scalar() or 0
        )
    except Exception:
        pass

    # Event volume (24h)
    try:
        from app.models.event import Event
        cutoff_ms = int((_now() - timedelta(hours=24)).timestamp() * 1000)
        snapshot.total_events_24h = (
            db.query(func.count(Event.id))
            .filter(Event.timestamp >= cutoff_ms)
            .scalar() or 0
        )
    except Exception:
        pass

    # LLM usage
    try:
        from app.core.llm_budget import get_usage_summary
        usage = get_usage_summary()
        snapshot.llm_calls_24h = usage.get("global_calls_today", 0)
        daily_tokens = sum(
            m.get("tokens_today", 0) for m in usage.get("modules", {}).values()
        )
        snapshot.llm_estimated_cost_eur = round(daily_tokens / 1000 * 0.006, 4)
    except Exception:
        pass

    # Worker health
    try:
        from app.services.system_summary import _get_worker_health
        wh = _get_worker_health(db)
        snapshot.worker_error_rate = wh.get("error_rate_pct", 0)
    except Exception:
        pass

    # Infrastructure
    try:
        from app.services.system_summary import _get_ram_usage, _get_cpu_load
        ram = _get_ram_usage()
        cpu = _get_cpu_load()
        snapshot.ram_used_mb = ram.get("used_mb")
        snapshot.ram_total_mb = ram.get("total_mb")
        snapshot.cpu_pct = cpu.get("normalized_pct")
    except Exception:
        pass

    # Disk usage
    try:
        stat = os.statvfs("/")
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bfree * stat.f_frsize
        snapshot.disk_used_pct = round((1 - free / total) * 100, 1) if total > 0 else None
    except Exception:
        pass

    # Operational counts (24h)
    try:
        cutoff = _now() - timedelta(hours=24)
        from app.models.support_incident import SupportIncident
        snapshot.support_incident_count = (
            db.query(func.count(SupportIncident.id))
            .filter(SupportIncident.created_at >= cutoff)
            .scalar() or 0
        )
    except Exception:
        pass

    try:
        cutoff = _now() - timedelta(hours=24)
        from app.models.ops_alert import OpsAlert
        snapshot.ops_alert_count = (
            db.query(func.count(OpsAlert.id))
            .filter(OpsAlert.created_at >= cutoff)
            .scalar() or 0
        )
    except Exception:
        pass

    # Warnings count from system_summary
    try:
        from app.services.system_summary import build_system_summary
        summary = build_system_summary(db)
        snapshot.api_warning_count = len(summary.get("warnings", []))
    except Exception:
        pass

    db.add(snapshot)
    db.flush()
    log.info("scaling: daily snapshot captured for %s", today)
    return snapshot


# ---------------------------------------------------------------------------
# PHASE 3 — Forecast / Projection Engine
# ---------------------------------------------------------------------------

def _get_recent_snapshots(db: Session, days: int = 30) -> list[SystemSnapshot]:
    """Fetch most recent N snapshots ordered by date."""
    return (
        db.query(SystemSnapshot)
        .order_by(desc(SystemSnapshot.date_bucket))
        .limit(days)
        .all()
    )[::-1]  # oldest first


def _linear_trend(values: list[float]) -> tuple[float, float]:
    """
    Simple linear regression: y = slope * x + intercept.
    x = 0..N-1 (day index).
    Returns (slope_per_day, last_value).
    """
    n = len(values)
    if n < 2:
        return 0.0, values[-1] if values else 0.0

    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))

    slope = num / den if den != 0 else 0.0
    return slope, values[-1]


def _project(slope: float, current: float, days: int) -> float:
    """Project value N days into the future."""
    return current + slope * days


def build_forecast(db: Session, horizon_days: int = 30) -> dict:
    """
    Build a forecast from recent snapshots.
    Returns structured projections or not_enough_data.
    """
    snapshots = _get_recent_snapshots(db, days=30)

    if len(snapshots) < MIN_FORECAST_DAYS:
        return {
            "status": "not_enough_data",
            "snapshots_available": len(snapshots),
            "minimum_required": MIN_FORECAST_DAYS,
        }

    # Extract time series. Coerce monetary Decimal columns to float
    # at the boundary: the forecast math is a plain linear regression
    # that mixes float day-indices with the stored values, and Python
    # refuses `float * Decimal`. Forecast precision is limited by
    # noise anyway; we don't need cent-precise projections.
    merchants = [s.active_merchants or 0 for s in snapshots]
    events = [s.total_events_24h or 0 for s in snapshots]
    llm_cost = [float(s.llm_estimated_cost_eur or 0) for s in snapshots]
    error_rate = [s.worker_error_rate or 0 for s in snapshots]
    ram_pct = [
        round((s.ram_used_mb or 0) / max(s.ram_total_mb or 1, 1) * 100, 1)
        for s in snapshots
    ]

    # Compute trends
    m_slope, m_current = _linear_trend(merchants)
    e_slope, e_current = _linear_trend(events)
    l_slope, l_current = _linear_trend(llm_cost)
    er_slope, er_current = _linear_trend(error_rate)
    r_slope, r_current = _linear_trend(ram_pct)

    confidence = "high" if len(snapshots) >= 14 else ("medium" if len(snapshots) >= 7 else "low")

    return {
        "status": "ok",
        "confidence": confidence,
        "horizon_days": horizon_days,
        "snapshots_used": len(snapshots),
        "merchants": {
            "current": round(m_current),
            "projected": max(0, round(_project(m_slope, m_current, horizon_days))),
            "daily_growth": round(m_slope, 2),
        },
        "events_24h": {
            "current": round(e_current),
            "projected": max(0, round(_project(e_slope, e_current, horizon_days))),
            "daily_growth": round(e_slope, 1),
        },
        "llm_daily_cost_eur": {
            "current": round(l_current, 4),
            "projected": max(0, round(_project(l_slope, l_current, horizon_days), 4)),
            "monthly_projected": max(0, round(_project(l_slope, l_current, horizon_days) * 30, 2)),
        },
        "worker_error_rate": {
            "current": round(er_current, 1),
            "projected": max(0, round(_project(er_slope, er_current, horizon_days), 1)),
        },
        "ram_pct": {
            "current": round(r_current, 1),
            "projected": min(100, max(0, round(_project(r_slope, r_current, horizon_days), 1))),
        },
    }


# ---------------------------------------------------------------------------
# PHASE 4 — Scaling Recommendation Engine
# ---------------------------------------------------------------------------

# Thresholds for generating recommendations
_THRESHOLDS = {
    "ram_pct_warn": 80,
    "ram_pct_critical": 90,
    "llm_monthly_warn_eur": 20,
    "error_rate_warn": 15,
    "merchant_growth_high": 2.0,  # >2 merchants/day growth
}

# Approximate cost increases for recommendations
_COST_ESTIMATES = {
    "vps_upgrade": 12.0,
    "redis_tier": 5.0,
    "llm_budget": 10.0,
}


def generate_recommendations(db: Session) -> list[dict]:
    """
    Generate scaling recommendations based on forecast trends.
    Returns list of recommendation dicts. Stores in DB with dedup.
    """
    forecast = build_forecast(db)

    if forecast.get("status") != "ok":
        return []

    recs = []
    horizon = forecast["horizon_days"]
    confidence = forecast["confidence"]

    # 1. RAM saturation forecast
    ram = forecast["ram_pct"]
    if ram["projected"] >= _THRESHOLDS["ram_pct_critical"]:
        recs.append({
            "resource_type": "vps",
            "title": "Upgrade VPS — RAM saturation projected",
            "reason": f"RAM at {ram['current']}% now, projected {ram['projected']}% in {horizon}d. Risk of OOM and worker instability.",
            "current_value": f"{ram['current']}%",
            "projected_value": f"{ram['projected']}%",
            "severity": "critical" if ram["projected"] >= 95 else "warning",
            "confidence": confidence,
            "estimated_cost_increase_eur": _COST_ESTIMATES["vps_upgrade"],
        })
    elif ram["projected"] >= _THRESHOLDS["ram_pct_warn"]:
        recs.append({
            "resource_type": "vps",
            "title": "Monitor VPS RAM — approaching threshold",
            "reason": f"RAM at {ram['current']}% now, projected {ram['projected']}% in {horizon}d.",
            "current_value": f"{ram['current']}%",
            "projected_value": f"{ram['projected']}%",
            "severity": "info",
            "confidence": confidence,
            "estimated_cost_increase_eur": _COST_ESTIMATES["vps_upgrade"],
        })

    # 2. LLM cost growth
    llm = forecast["llm_daily_cost_eur"]
    if llm["monthly_projected"] >= _THRESHOLDS["llm_monthly_warn_eur"]:
        recs.append({
            "resource_type": "llm_budget",
            "title": "Review LLM budget — cost growth detected",
            "reason": f"Daily cost €{llm['current']:.4f} now, projected monthly €{llm['monthly_projected']:.2f}.",
            "current_value": f"€{llm['current']:.4f}/day",
            "projected_value": f"€{llm['monthly_projected']:.2f}/month",
            "severity": "warning",
            "confidence": confidence,
            "estimated_cost_increase_eur": _COST_ESTIMATES["llm_budget"],
        })

    # 3. Worker error rate trending up
    err = forecast["worker_error_rate"]
    if err["projected"] >= _THRESHOLDS["error_rate_warn"] and err["projected"] > err["current"] * 1.5:
        recs.append({
            "resource_type": "vps",
            "title": "Worker error rate trending up",
            "reason": f"Error rate {err['current']}% now, projected {err['projected']}% in {horizon}d. Investigate worker capacity or code issues.",
            "current_value": f"{err['current']}%",
            "projected_value": f"{err['projected']}%",
            "severity": "warning",
            "confidence": confidence,
            "estimated_cost_increase_eur": None,
        })

    # 4. Merchant growth rate (capacity planning signal)
    merch = forecast["merchants"]
    if merch["daily_growth"] >= _THRESHOLDS["merchant_growth_high"]:
        recs.append({
            "resource_type": "vps",
            "title": "High merchant growth — plan capacity ahead",
            "reason": f"{merch['current']} merchants now, projected {merch['projected']} in {horizon}d ({merch['daily_growth']}/day). Review server, DB, and worker capacity.",
            "current_value": str(merch["current"]),
            "projected_value": str(merch["projected"]),
            "severity": "info",
            "confidence": confidence,
            "estimated_cost_increase_eur": None,
        })

    # Store with dedup
    stored = _store_recommendations(db, recs, horizon)
    return stored


def _store_recommendations(db: Session, recs: list[dict], horizon: int) -> list[dict]:
    """Store recommendations in DB with dedup. Returns stored list."""
    stored = []
    for r in recs:
        dedup_key = f"scaling:{r['resource_type']}:{r['title'][:50]}"

        existing = (
            db.query(ScalingRecommendation)
            .filter(
                ScalingRecommendation.dedup_key == dedup_key,
                ScalingRecommendation.status == "active",
            )
            .first()
        )
        if existing:
            continue

        row = ScalingRecommendation(
            resource_type=r["resource_type"],
            title=r["title"],
            reason=r["reason"],
            current_value=r.get("current_value"),
            projected_value=r.get("projected_value"),
            projected_horizon_days=horizon,
            severity=r.get("severity", "info"),
            confidence=r.get("confidence", "low"),
            estimated_cost_increase_eur=r.get("estimated_cost_increase_eur"),
            status="active",
            dedup_key=dedup_key,
        )
        db.add(row)
        stored.append(r)

    if stored:
        db.flush()
        log.info("scaling: %d new recommendations stored", len(stored))

    return stored


# ---------------------------------------------------------------------------
# Query helpers (for API + Telegram)
# ---------------------------------------------------------------------------

def get_active_recommendations(db: Session) -> list[dict]:
    """Return all active recommendations."""
    rows = (
        db.query(ScalingRecommendation)
        .filter(ScalingRecommendation.status == "active")
        .order_by(desc(ScalingRecommendation.created_at))
        .all()
    )
    return [
        {
            "id": r.id,
            "resource_type": r.resource_type,
            "title": r.title,
            "reason": r.reason,
            "current_value": r.current_value,
            "projected_value": r.projected_value,
            "horizon_days": r.projected_horizon_days,
            "severity": r.severity,
            "confidence": r.confidence,
            "cost_increase_eur": r.estimated_cost_increase_eur,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]


def get_recent_snapshots(db: Session, limit: int = 14) -> list[dict]:
    """Return recent snapshots for API."""
    rows = (
        db.query(SystemSnapshot)
        .order_by(desc(SystemSnapshot.date_bucket))
        .limit(limit)
        .all()
    )
    return [
        {
            "date": str(r.date_bucket),
            "active_merchants": r.active_merchants,
            "billing_active": r.billing_active_merchants,
            "events_24h": r.total_events_24h,
            "llm_calls": r.llm_calls_24h,
            "llm_cost_eur": r.llm_estimated_cost_eur,
            "worker_error_rate": r.worker_error_rate,
            "ram_used_mb": r.ram_used_mb,
            "ram_total_mb": r.ram_total_mb,
            "cpu_pct": r.cpu_pct,
            "disk_pct": r.disk_used_pct,
            "incidents": r.support_incident_count,
            "alerts": r.ops_alert_count,
        }
        for r in rows
    ]
