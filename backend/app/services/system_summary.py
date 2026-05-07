# test-coverage: TBD — verify usage post Brain Vero pivot
"""
system_summary.py — Lightweight infra + cost awareness.

Aggregates system health and cost data for operator visibility.
NOT a real-time watchdog — produces point-in-time snapshots.

Designed to accept pluggable metrics later. Currently uses:
  - /proc/meminfo for RAM (Linux)
  - os.getloadavg() for CPU load
  - worker_log table for error rates
  - llm_budget counters for API usage
  - config-driven fixed cost estimates

Public interface:
    build_system_summary(db) -> dict
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("system_summary")

# ---------------------------------------------------------------------------
# Cost configuration (EUR/month estimates — update as needed)
# ---------------------------------------------------------------------------

# Founder-facing infrastructure costs (NOT merchant currency).
# The "_EUR" suffix is part of the env-var name, not a user-facing
# currency tag — these values display in /ops dashboards only, never
# leak to a merchant response. Exempt from currency-drift audit.
FIXED_COSTS = {
    "server_vps": float(os.getenv("COST_SERVER_EUR", "25.0")),    # audit:eur-default-ok (founder-cost)
    "domain_ssl": float(os.getenv("COST_DOMAIN_EUR", "2.0")),     # audit:eur-default-ok (founder-cost)
    "redis": float(os.getenv("COST_REDIS_EUR", "0.0")),           # audit:eur-default-ok (founder-cost, included in VPS)
    "resend_email": float(os.getenv("COST_RESEND_EUR", "0.0")),   # audit:eur-default-ok (founder-cost)
}

# LLM cost per 1K tokens (approximate, input+output blended).
# 2026-04-23: keys refreshed to match the canonical model strings in
# `llm_router` (Sonnet 4 → 4.6, Opus 4 → 4.7). Old keys retained as
# aliases so historical rows in `llm_daily_usage` don't lose cost
# attribution when the summary walks Redis counters.
LLM_COST_PER_1K = {
    "anthropic:claude-sonnet-4-6": 0.006,
    "anthropic:claude-opus-4-7": 0.030,
    "anthropic:claude-haiku-4-5-20251001": 0.0015,
    # Legacy aliases (pre-2026-04-23 upgrade)
    "anthropic:claude-sonnet-4-20250514": 0.006,
    "anthropic:claude-opus-4-20250514": 0.030,
    "openai:gpt-4o-mini": 0.0003,
    "openai:gpt-4o": 0.005,
    "default": 0.006,
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Infrastructure metrics
# ---------------------------------------------------------------------------

def _get_ram_usage() -> dict:
    """Read RAM from /proc/meminfo (Linux). Returns best-effort."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split()
            if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                mem[parts[0].rstrip(":")] = int(parts[1])  # kB

        total_mb = mem.get("MemTotal", 0) / 1024
        available_mb = mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024
        used_mb = total_mb - available_mb
        pct = (used_mb / total_mb * 100) if total_mb > 0 else 0

        return {
            "total_mb": round(total_mb),
            "used_mb": round(used_mb),
            "available_mb": round(available_mb),
            "usage_pct": round(pct, 1),
        }
    except Exception:
        return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "usage_pct": 0, "note": "unavailable"}


def _get_cpu_load() -> dict:
    """Get CPU load average (Linux)."""
    try:
        load1, load5, load15 = os.getloadavg()
        # Count CPUs for context
        cpu_count = os.cpu_count() or 1
        return {
            "load_1m": round(load1, 2),
            "load_5m": round(load5, 2),
            "load_15m": round(load15, 2),
            "cpu_count": cpu_count,
            "normalized_pct": round(load5 / cpu_count * 100, 1),
        }
    except Exception:
        return {"load_1m": 0, "load_5m": 0, "load_15m": 0, "cpu_count": 0, "note": "unavailable"}


# ---------------------------------------------------------------------------
# Worker health (from worker_log table)
# ---------------------------------------------------------------------------

def _get_worker_health(db: Session) -> dict:
    """Summarize worker errors from last 24 hours using SQL aggregation."""
    try:
        from sqlalchemy import text
        cutoff = _now() - timedelta(hours=24)

        # Guard: 3s statement timeout so a slow query degrades gracefully
        db.execute(text("SET LOCAL statement_timeout = '3000'"))

        # Single aggregate query — no ORM materialization
        row = db.execute(text(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COUNT(*) FILTER (WHERE errors > 0) AS errored, "
            "  COALESCE(SUM(errors), 0) AS total_errors, "
            "  COALESCE(AVG(duration_ms), 0) AS avg_duration "
            "FROM worker_log "
            "WHERE started_at >= :cutoff"
        ), {"cutoff": cutoff}).mappings().first()

        total = row["total"]
        errored = row["errored"]
        total_errors = int(row["total_errors"])
        avg_duration = round(float(row["avg_duration"]))

        # Per-worker breakdown via SQL group-by
        per_worker_rows = db.execute(text(
            "SELECT worker_name, COUNT(*) AS cycles, COALESCE(SUM(errors), 0) AS errors "
            "FROM worker_log "
            "WHERE started_at >= :cutoff "
            "GROUP BY worker_name"
        ), {"cutoff": cutoff}).mappings().all()

        workers = {
            r["worker_name"]: {"cycles": r["cycles"], "errors": int(r["errors"])}
            for r in per_worker_rows
        }

        return {
            "cycles_24h": total,
            "errored_cycles": errored,
            "total_errors": total_errors,
            "error_rate_pct": round(errored / max(total, 1) * 100, 1),
            "avg_cycle_duration_ms": avg_duration,
            "per_worker": workers,
        }
    except Exception as exc:
        log.warning("system_summary: worker health unavailable: %s", exc)
        return {"cycles_24h": 0, "errored_cycles": 0, "total_errors": 0, "error_rate_pct": 0, "note": "unavailable"}


# ---------------------------------------------------------------------------
# LLM usage + cost estimation
# ---------------------------------------------------------------------------

def _get_llm_usage() -> dict:
    """Get LLM usage from budget counters."""
    try:
        from app.core.llm_budget import get_usage_summary
        return get_usage_summary()
    except Exception:
        return {"date": "", "global_calls_today": 0, "modules": {}, "note": "unavailable"}


def _estimate_llm_cost(usage: dict) -> dict:
    """Estimate LLM cost from usage counters (daily → monthly projection)."""
    daily_tokens = 0
    modules = usage.get("modules", {})
    for mod_data in modules.values():
        daily_tokens += mod_data.get("tokens_today", 0)

    # Use blended rate (most calls are Sonnet)
    rate = LLM_COST_PER_1K.get("default", 0.006)
    daily_cost = (daily_tokens / 1000) * rate
    monthly_projection = daily_cost * 30

    return {
        "daily_tokens_estimate": daily_tokens,
        "daily_cost_eur": round(daily_cost, 4),
        "monthly_projection_eur": round(monthly_projection, 2),
        "rate_per_1k": rate,
        "note": "blended estimate — actual varies by model mix",
    }


def _get_cost_estimate(llm_usage: dict) -> dict:
    """Full monthly cost breakdown."""
    llm_cost = _estimate_llm_cost(llm_usage)
    fixed_total = sum(FIXED_COSTS.values())

    return {
        "fixed_monthly_eur": FIXED_COSTS,
        "fixed_total_eur": round(fixed_total, 2),
        "llm_monthly_eur": llm_cost["monthly_projection_eur"],
        "total_monthly_eur": round(fixed_total + llm_cost["monthly_projection_eur"], 2),
        "llm_detail": llm_cost,
    }


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def _generate_warnings(ram: dict, cpu: dict, workers: dict, llm: dict) -> list[str]:
    """Generate actionable warnings from metrics."""
    warnings = []

    if ram.get("usage_pct", 0) > 85:
        warnings.append(f"RAM usage at {ram['usage_pct']}% — consider upgrading server tier")
    elif ram.get("usage_pct", 0) > 70:
        warnings.append(f"RAM usage at {ram['usage_pct']}% — monitor trend")

    if cpu.get("normalized_pct", 0) > 80:
        warnings.append(f"CPU load high ({cpu['normalized_pct']}% normalized) — investigate bottleneck")

    if workers.get("error_rate_pct", 0) > 20:
        warnings.append(f"Worker error rate {workers['error_rate_pct']}% — review worker logs")
    elif workers.get("error_rate_pct", 0) > 10:
        warnings.append(f"Worker error rate {workers['error_rate_pct']}% — elevated but not critical")

    global_calls = llm.get("global_calls_today", 0)
    global_max = llm.get("global_max_per_day", 150)
    if global_max > 0 and global_calls / global_max > 0.8:
        warnings.append(f"LLM calls near daily cap ({global_calls}/{global_max})")

    blocked = llm.get("blocked_today", 0)
    if blocked > 5:
        warnings.append(f"{blocked} LLM calls blocked today — budget limits hitting")

    return warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _safe_call(fn, *args, fallback, label="component"):
    """Call fn with args; return fallback on exception and log warning."""
    try:
        return fn(*args)
    except Exception as exc:
        log.warning("system_summary: %s failed: %s", label, exc)
        return fallback


def build_system_summary(db: Session) -> dict:
    """
    Build a complete system summary snapshot.
    Returns structured dict with infra, llm_usage, cost_estimate, warnings.
    Each sub-component is individually guarded — a slow or broken component
    returns its fallback without blocking the rest.
    """
    ram = _safe_call(_get_ram_usage, fallback={"total_mb": 0, "used_mb": 0, "available_mb": 0, "usage_pct": 0, "note": "unavailable"}, label="ram")
    cpu = _safe_call(_get_cpu_load, fallback={"load_1m": 0, "load_5m": 0, "load_15m": 0, "cpu_count": 0, "note": "unavailable"}, label="cpu")
    workers = _safe_call(_get_worker_health, db, fallback={"cycles_24h": 0, "errored_cycles": 0, "total_errors": 0, "error_rate_pct": 0, "note": "unavailable"}, label="workers")
    llm = _safe_call(_get_llm_usage, fallback={"date": "", "global_calls_today": 0, "global_max_per_day": 150, "modules": {}, "note": "unavailable"}, label="llm")
    cost = _safe_call(_get_cost_estimate, llm, fallback={"fixed_monthly_eur": FIXED_COSTS, "fixed_total_eur": sum(FIXED_COSTS.values()), "llm_monthly_eur": 0, "total_monthly_eur": sum(FIXED_COSTS.values()), "note": "llm cost unavailable"}, label="cost")
    warnings = _generate_warnings(ram, cpu, workers, llm)

    return {
        "timestamp": _now().isoformat() + "Z",
        "infra": {
            "ram": ram,
            "cpu": cpu,
            "workers": workers,
        },
        "llm_usage": llm,
        "cost_estimate": cost,
        "warnings": warnings,
    }
