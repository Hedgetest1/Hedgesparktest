"""
protection_state.py — unified system degradation signal.

Consolidates existing health signals into ONE read-only function that
any code path can check before doing non-critical work. The system
bends BEFORE it breaks.

Design principles
-----------------
- Pure read function (no side effects, no state mutation)
- Zero new background monitoring / no new workers
- Reuses existing signals (llm_budget, health endpoint, worker_state)
- Cheap: ~3 indexed queries + in-process budget read
- Cached 30s in-process to avoid bursts

Protection levels
-----------------
OK          everything green — non-critical work proceeds normally
DEGRADED    some subsystem under pressure — shed non-essential LLM /
            non-critical queries / larger batch sizes
CRITICAL    multiple subsystems failing — only CRITICAL-tier work
            proceeds; everything optional is blocked

Callers read protection_state() and adapt:
  if state["level"] == "CRITICAL":
      return early
  if "llm" in state["degraded_subsystems"]:
      skip_optional_llm()
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("protection_state")

_CACHE_TTL_SECONDS = 30
_cache: dict = {"value": None, "computed_at": 0.0}

# Thresholds
_LLM_DEGRADED_AT = 0.80   # >=80% spent → degraded
_LLM_CRITICAL_AT = 0.95   # >=95% spent → critical
_WORKER_STALE_MULTIPLIER = 3.0  # worker age > 3× its interval = degraded

# Each worker's expected interval (seconds) — matches app/api/health.py
_WORKER_INTERVALS = {
    "aggregation_worker":          300,
    "intelligence_worker":         600,
    "segment_monitor_worker":      300,
    "agent_worker":                900,
    "nudge_optimization_worker":   21600,
    "gdpr_worker":                 300,
}


def _llm_pressure() -> tuple[str, dict]:
    """Return (level, detail) from the LLM budget subsystem."""
    try:
        from app.core.llm_budget import get_usage_summary
        s = get_usage_summary()
        spent = float(s.get("monthly_cost_eur", 0.0))
        cap = float(s.get("monthly_cap_eur", 0.0) or 1.0)
        ratio = spent / cap if cap > 0 else 0.0
        if s.get("monthly_cap_reached") or ratio >= _LLM_CRITICAL_AT:
            return "critical", {"spent_eur": spent, "cap_eur": cap, "ratio": round(ratio, 3)}
        if ratio >= _LLM_DEGRADED_AT:
            return "degraded", {"spent_eur": spent, "cap_eur": cap, "ratio": round(ratio, 3)}
        return "ok", {"spent_eur": spent, "cap_eur": cap, "ratio": round(ratio, 3)}
    except Exception as exc:
        log.warning("protection_state: llm budget read failed (%s)", type(exc).__name__)
        return "ok", {"read_error": type(exc).__name__}


def _redis_pressure() -> tuple[str, dict]:
    """Return (level, detail) from the Redis subsystem."""
    try:
        from app.core.redis_client import _client
        client = _client()
        if client is None:
            return "degraded", {"reason": "no_redis_url"}
        # Cheap PING — no data transfer
        client.ping()
        return "ok", {}
    except Exception as exc:
        return "degraded", {"reason": "ping_failed", "err": type(exc).__name__}


def _db_pool_pressure() -> tuple[str, dict]:
    """Return (level, detail) from the SQLAlchemy pool."""
    try:
        from app.core.database import engine
        pool = engine.pool
        # checkedout() = in-use connections; size() = pool_size
        in_use = pool.checkedout()
        size = pool.size()
        overflow = pool.overflow()
        total_capacity = size + 30  # max_overflow default from database.py
        ratio = in_use / total_capacity if total_capacity > 0 else 0.0
        if ratio >= 0.90:
            return "critical", {"in_use": in_use, "capacity": total_capacity, "ratio": round(ratio, 3)}
        if ratio >= 0.70:
            return "degraded", {"in_use": in_use, "capacity": total_capacity, "ratio": round(ratio, 3)}
        return "ok", {"in_use": in_use, "capacity": total_capacity, "ratio": round(ratio, 3)}
    except Exception as exc:
        log.warning("protection_state: db pool read failed (%s)", type(exc).__name__)
        return "ok", {"read_error": type(exc).__name__}


def _worker_pressure() -> tuple[str, dict]:
    """Return (level, detail) from worker_state freshness."""
    try:
        from app.core.database import engine
        from sqlalchemy import text as _text
        with engine.connect() as conn:
            rows = conn.execute(_text(
                "SELECT worker_name, last_run_at FROM worker_state"
            )).fetchall()
        if not rows:
            return "ok", {"note": "no worker_state rows yet"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale: list[str] = []
        for wname, last_run in rows:
            interval = _WORKER_INTERVALS.get(wname)
            if interval is None or last_run is None:
                continue
            age = (now - last_run).total_seconds()
            if age > interval * _WORKER_STALE_MULTIPLIER:
                stale.append(f"{wname}({int(age)}s)")
        if len(stale) >= 2:
            return "critical", {"stale_workers": stale}
        if len(stale) == 1:
            return "degraded", {"stale_workers": stale}
        return "ok", {}
    except Exception as exc:
        log.warning("protection_state: worker check failed (%s)", type(exc).__name__)
        return "ok", {"read_error": type(exc).__name__}


def _merge_levels(*levels: str) -> str:
    if "critical" in levels:
        return "CRITICAL"
    if "degraded" in levels:
        return "DEGRADED"
    return "OK"


def protection_state(*, force_refresh: bool = False) -> dict:
    """
    Return the system's current protection posture.

    Shape:
      {
        "level": "OK" | "DEGRADED" | "CRITICAL",
        "degraded_subsystems": ["llm", "redis", ...],
        "subsystems": {
          "llm": {"level": "ok"|"degraded"|"critical", ...},
          "redis": {...},
          "db_pool": {...},
          "workers": {...},
        },
        "protective_actions": [...],
        "checked_at": "<iso timestamp>",
      }
    """
    now = time.time()
    if not force_refresh and _cache["value"] is not None and (now - _cache["computed_at"]) < _CACHE_TTL_SECONDS:
        return _cache["value"]

    llm_level, llm_detail = _llm_pressure()
    redis_level, redis_detail = _redis_pressure()
    db_level, db_detail = _db_pool_pressure()
    worker_level, worker_detail = _worker_pressure()

    subsystems = {
        "llm":     {"level": llm_level, **llm_detail},
        "redis":   {"level": redis_level, **redis_detail},
        "db_pool": {"level": db_level, **db_detail},
        "workers": {"level": worker_level, **worker_detail},
    }
    degraded = [name for name, s in subsystems.items() if s["level"] != "ok"]
    overall = _merge_levels(llm_level, redis_level, db_level, worker_level)

    # Derive explicit protective actions the caller should take.
    actions: list[str] = []
    if llm_level == "critical":
        actions.append("skip_all_optional_llm_calls")
    elif llm_level == "degraded":
        actions.append("skip_optional_llm_calls")
    if redis_level != "ok":
        actions.append("use_db_fallback_for_caches")
    if db_level == "critical":
        actions.append("skip_non_critical_db_queries")
    elif db_level == "degraded":
        actions.append("reduce_batch_sizes")
    if worker_level != "ok":
        actions.append("skip_non_critical_jobs")

    result = {
        "level": overall,
        "degraded_subsystems": degraded,
        "subsystems": subsystems,
        "protective_actions": actions,
        "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    _cache["value"] = result
    _cache["computed_at"] = now
    return result


def should_skip_optional_llm() -> bool:
    """Shortcut for callers: 'is it safe to burn optional LLM calls?'"""
    state = protection_state()
    return "skip_optional_llm_calls" in state["protective_actions"] or "skip_all_optional_llm_calls" in state["protective_actions"]


def should_reduce_batch() -> bool:
    """Shortcut: 'should I reduce my batch size this cycle?'"""
    state = protection_state()
    return state["level"] != "OK"


def invalidate_cache() -> None:
    """Force the next protection_state() call to refresh. Used in tests."""
    _cache["value"] = None
    _cache["computed_at"] = 0.0
