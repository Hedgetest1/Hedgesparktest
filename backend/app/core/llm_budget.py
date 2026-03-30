"""
llm_budget.py — Central LLM budget guard for all AI provider calls.

Every LLM call must go through check_budget() before making a request
and record_usage() after a successful response.

Budget enforcement:
    1. Monthly EUR hard cap (MONTHLY_EUR_CAP = 5.0)
    2. Per-module daily call limits
    3. Global daily call limit
    4. Per-module cooldown between calls

When any limit is hit, the call is blocked and logged — the caller
degrades to deterministic fallback. Never crashes the worker.

Cost estimation:
    Uses conservative cost-per-1k-token estimates. Actual cost may be
    lower (prompt caching, etc.) but we never underestimate.

429 handling:
    Provides a per-provider cooldown mechanism. When a 429 is received,
    the caller reports it via record_429(). All subsequent calls to that
    provider are blocked for a backoff period (exponential, capped at 5 min).

Storage: Redis counters with daily/monthly TTL (auto-expire).
Fallback: in-process counters if Redis is unavailable (reset on restart).

Public interface:
    check_budget(module) -> (allowed: bool, reason: str)
    record_usage(module, tokens_used, provider, model)
    record_blocked(module, reason)
    record_429(provider)       — report a 429, triggers backoff
    is_provider_backed_off(provider) -> bool
    get_usage_summary() -> dict
    get_max_tokens(module) -> int
    reset_cycle_counts()
    reset_daily_counters()     — for testing
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("llm_budget")

# ---------------------------------------------------------------------------
# Monthly EUR hard cap
# ---------------------------------------------------------------------------
MONTHLY_EUR_CAP = 5.0

# Conservative cost-per-1k-token estimates (output tokens, which dominate cost)
# These are UPPER BOUNDS — we'd rather block slightly early than overspend.
_COST_PER_1K_TOKENS: dict[str, float] = {
    "gpt-4o-mini":                 0.0006,   # $0.60/M output
    "gpt-4o":                      0.010,    # $10/M output
    "claude-sonnet-4-20250514":    0.015,    # $15/M output
    "claude-opus-4-20250514":      0.075,    # $75/M output
    "default":                     0.010,    # conservative fallback
}

# ---------------------------------------------------------------------------
# Per-module limits
# ---------------------------------------------------------------------------

BUDGET_LIMITS: dict[str, dict] = {
    "orchestrator": {
        "max_calls_per_day": 96,
        "max_calls_per_cycle": 1,
        "max_tokens_per_request": 512,
        "cooldown_seconds": 300,
    },
    "bugfix_proposal": {
        "max_calls_per_day": 10,
        "max_calls_per_cycle": 2,
        "max_tokens_per_request": 2048,
        "cooldown_seconds": 600,
    },
    "evolution_audit": {
        "max_calls_per_day": 2,
        "max_calls_per_cycle": 1,
        "max_tokens_per_request": 2048,
        "cooldown_seconds": 3600,
    },
    "monthly_opus_audit": {
        "max_calls_per_day": 1,
        "max_calls_per_cycle": 1,
        "max_tokens_per_request": 4096,
        "cooldown_seconds": 86400,
    },
    "nudge_composer": {
        "max_calls_per_day": 30,
        "max_calls_per_cycle": 5,
        "max_tokens_per_request": 1024,
        "cooldown_seconds": 60,
    },
    "default": {
        "max_calls_per_day": 20,
        "max_calls_per_cycle": 2,
        "max_tokens_per_request": 1024,
        "cooldown_seconds": 300,
    },
}

# Global daily cap across all modules
GLOBAL_MAX_CALLS_PER_DAY = 150

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_daily_counts: dict[str, int] = {}
_cycle_counts: dict[str, int] = {}
_last_call: dict[str, float] = {}
_total_tokens: dict[str, int] = {}
_blocked_count: int = 0
_day_key: str = ""

# Monthly cost tracking (in-process, reset on month change)
_monthly_cost_eur: float = 0.0
_month_key: str = ""

# 429 backoff tracking per provider
_provider_429: dict[str, dict] = {}   # provider → {last_429: float, backoff_secs: int, count: int}
_MAX_BACKOFF = 300   # 5 minutes max
_INITIAL_BACKOFF = 5  # 5 seconds initial


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _ensure_day():
    """Reset daily counters if day changed."""
    global _daily_counts, _cycle_counts, _total_tokens, _blocked_count, _day_key
    today = _today()
    if _day_key != today:
        _daily_counts = {}
        _cycle_counts = {}
        _total_tokens = {}
        _blocked_count = 0
        _day_key = today


def _ensure_month():
    """Reset monthly cost if month changed."""
    global _monthly_cost_eur, _month_key
    month = _this_month()
    if _month_key != month:
        _monthly_cost_eur = 0.0
        _month_key = month


def _get_limits(module: str) -> dict:
    return BUDGET_LIMITS.get(module, BUDGET_LIMITS["default"])


def _estimate_cost(tokens: int, model: str) -> float:
    """Estimate cost in EUR for a given token count and model."""
    rate = _COST_PER_1K_TOKENS.get(model, _COST_PER_1K_TOKENS["default"])
    return (tokens / 1000.0) * rate


# ---------------------------------------------------------------------------
# Redis helpers (optional persistence)
# ---------------------------------------------------------------------------

def _redis_incr(key: str, ttl: int = 86400) -> int | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return None
        val = rc.incr(key)
        if val == 1:
            rc.expire(key, ttl)
        return val
    except Exception:
        return None


def _redis_get(key: str) -> int:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return 0
        val = rc.get(key)
        return int(val) if val else 0
    except Exception:
        return 0


def _redis_incrbyfloat(key: str, amount: float, ttl: int = 2678400) -> float | None:
    """Increment a Redis float counter. TTL defaults to 31 days."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return None
        val = rc.incrbyfloat(key, amount)
        rc.expire(key, ttl)
        return float(val)
    except Exception:
        return None


def _redis_get_float(key: str) -> float:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return 0.0
        val = rc.get(key)
        return float(val) if val else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 429 backoff
# ---------------------------------------------------------------------------

def record_429(provider: str):
    """Record a 429 response from a provider. Triggers exponential backoff."""
    now = time.monotonic()
    state = _provider_429.get(provider, {"last_429": 0, "backoff_secs": 0, "count": 0})

    state["count"] += 1
    state["last_429"] = now

    # Exponential backoff: 5s, 10s, 20s, 40s, 80s, 160s, 300s (capped)
    if state["backoff_secs"] == 0:
        state["backoff_secs"] = _INITIAL_BACKOFF
    else:
        state["backoff_secs"] = min(state["backoff_secs"] * 2, _MAX_BACKOFF)

    _provider_429[provider] = state
    log.warning(
        "llm_budget: 429 from %s — backoff %ds (total 429s today: %d)",
        provider, state["backoff_secs"], state["count"],
    )


def is_provider_backed_off(provider: str) -> bool:
    """Check if a provider is in 429 backoff. Returns True if still cooling down."""
    state = _provider_429.get(provider)
    if not state or state["backoff_secs"] == 0:
        return False
    elapsed = time.monotonic() - state["last_429"]
    if elapsed >= state["backoff_secs"]:
        # Backoff expired — reset
        state["backoff_secs"] = 0
        state["count"] = 0
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_budget(module: str) -> tuple[bool, str]:
    """
    Check if an LLM call is allowed for this module.
    Returns (allowed, reason).

    Checks in order:
        1. Monthly EUR cap
        2. Per-module cooldown
        3. Per-module daily limit
        4. Global daily limit
    """
    _ensure_day()
    _ensure_month()
    limits = _get_limits(module)
    today = _today()

    # Check monthly EUR cap
    month = _this_month()
    redis_cost = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_cost, _monthly_cost_eur)
    if monthly_cost >= MONTHLY_EUR_CAP:
        return False, f"monthly_eur_cap_reached: €{monthly_cost:.3f}/€{MONTHLY_EUR_CAP:.2f}"

    # Check cooldown
    last = _last_call.get(module, 0)
    cooldown = limits.get("cooldown_seconds", 0)
    if cooldown and (time.monotonic() - last) < cooldown:
        remaining = int(cooldown - (time.monotonic() - last))
        return False, f"cooldown_active: {remaining}s remaining"

    # Check per-module daily limit
    redis_key = f"llm:daily:{module}:{today}"
    redis_count = _redis_get(redis_key)
    local_count = _daily_counts.get(module, 0)
    count = max(redis_count, local_count)

    max_daily = limits.get("max_calls_per_day", 20)
    if count >= max_daily:
        return False, f"daily_limit_reached: {count}/{max_daily}"

    # Check global daily limit
    global_key = f"llm:daily:_global:{today}"
    global_count = _redis_get(global_key) or sum(_daily_counts.values())
    if global_count >= GLOBAL_MAX_CALLS_PER_DAY:
        return False, f"global_daily_limit_reached: {global_count}/{GLOBAL_MAX_CALLS_PER_DAY}"

    return True, "allowed"


def record_usage(module: str, tokens_used: int = 0, provider: str = "", model: str = ""):
    """Record a successful LLM call with cost tracking."""
    global _monthly_cost_eur
    _ensure_day()
    _ensure_month()
    today = _today()
    month = _this_month()

    _daily_counts[module] = _daily_counts.get(module, 0) + 1
    _cycle_counts[module] = _cycle_counts.get(module, 0) + 1
    _total_tokens[module] = _total_tokens.get(module, 0) + tokens_used
    _last_call[module] = time.monotonic()

    # Cost tracking
    cost = _estimate_cost(tokens_used, model)
    _monthly_cost_eur += cost
    _redis_incrbyfloat(f"llm:monthly_cost:{month}", cost)

    # Redis persistence
    _redis_incr(f"llm:daily:{module}:{today}")
    _redis_incr(f"llm:daily:_global:{today}")

    log.info(
        "llm_budget: call module=%s provider=%s model=%s tokens=%d cost=€%.4f daily=%d monthly=€%.3f",
        module, provider, model, tokens_used, cost, _daily_counts.get(module, 0), _monthly_cost_eur,
    )


def record_blocked(module: str, reason: str):
    """Record a blocked LLM call."""
    global _blocked_count
    _blocked_count += 1
    log.warning("llm_budget: BLOCKED module=%s reason=%s", module, reason)


def get_max_tokens(module: str) -> int:
    """Get the max_tokens_per_request for a module."""
    return _get_limits(module).get("max_tokens_per_request", 1024)


def get_usage_summary() -> dict:
    """Return current usage stats for operator visibility."""
    _ensure_day()
    _ensure_month()
    month = _this_month()

    redis_monthly = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_monthly, _monthly_cost_eur)

    modules = {}
    for mod, limits in BUDGET_LIMITS.items():
        if mod == "default":
            continue
        modules[mod] = {
            "calls_today": _daily_counts.get(mod, 0),
            "max_per_day": limits["max_calls_per_day"],
            "tokens_today": _total_tokens.get(mod, 0),
            "max_tokens_per_request": limits["max_tokens_per_request"],
            "cooldown_seconds": limits["cooldown_seconds"],
        }

    # 429 backoff state
    backoff_state = {}
    for provider, state in _provider_429.items():
        if state.get("count", 0) > 0:
            backed_off = is_provider_backed_off(provider)
            backoff_state[provider] = {
                "backed_off": backed_off,
                "backoff_secs": state["backoff_secs"] if backed_off else 0,
                "total_429s": state["count"],
            }

    return {
        "date": _today(),
        "month": month,
        "global_calls_today": sum(_daily_counts.values()),
        "global_max_per_day": GLOBAL_MAX_CALLS_PER_DAY,
        "blocked_today": _blocked_count,
        "monthly_cost_eur": round(monthly_cost, 4),
        "monthly_cap_eur": MONTHLY_EUR_CAP,
        "monthly_remaining_eur": round(max(0, MONTHLY_EUR_CAP - monthly_cost), 4),
        "monthly_cap_reached": monthly_cost >= MONTHLY_EUR_CAP,
        "provider_429_state": backoff_state,
        "modules": modules,
    }


def reset_cycle_counts():
    """Reset per-cycle counters (called at start of each worker cycle)."""
    _cycle_counts.clear()


def reset_daily_counters():
    """For testing only — reset all counters including Redis."""
    global _daily_counts, _cycle_counts, _total_tokens, _blocked_count, _day_key
    global _monthly_cost_eur, _month_key
    _daily_counts = {}
    _cycle_counts = {}
    _total_tokens = {}
    _last_call.clear()
    _blocked_count = 0
    _day_key = ""
    _monthly_cost_eur = 0.0
    _month_key = ""
    _provider_429.clear()
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            for key in rc.scan_iter(match="llm:*", count=100):
                rc.delete(key)
    except Exception:
        pass
