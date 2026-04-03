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

import os as _os

log = logging.getLogger("llm_budget")

# ---------------------------------------------------------------------------
# Monthly EUR hard caps — env-configurable for operator control
# ---------------------------------------------------------------------------
MONTHLY_EUR_CAP = float(_os.getenv("LLM_MONTHLY_BUDGET_EUR", "10.0"))

# Per-provider caps (independent of global cap)
ANTHROPIC_MONTHLY_CAP = float(_os.getenv("ANTHROPIC_MONTHLY_BUDGET_EUR", "10.0"))
OPENAI_MONTHLY_CAP = float(_os.getenv("OPENAI_MONTHLY_BUDGET_EUR", "10.0"))

# Budget alert threshold (fraction 0-1)
_BUDGET_ALERT_THRESHOLD = 0.9  # alert at 90% usage

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
_provider_cost_eur: dict[str, float] = {}  # per-provider cost tracking
_month_key: str = ""

# Budget alert dedup (one alert per provider per month at 90%)
_budget_alert_sent: dict[str, bool] = {}  # "anthropic:2026-04" → True

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
    global _monthly_cost_eur, _provider_cost_eur, _month_key, _budget_alert_sent
    month = _this_month()
    if _month_key != month:
        _monthly_cost_eur = 0.0
        _provider_cost_eur = {}
        _budget_alert_sent = {}
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

    # Check monthly EUR cap (global)
    month = _this_month()
    redis_cost = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_cost, _monthly_cost_eur)
    if monthly_cost >= MONTHLY_EUR_CAP:
        return False, f"monthly_eur_cap_reached: €{monthly_cost:.3f}/€{MONTHLY_EUR_CAP:.2f}"

    # Check per-provider caps
    for provider, cap in [("anthropic", ANTHROPIC_MONTHLY_CAP), ("openai", OPENAI_MONTHLY_CAP)]:
        prov_redis = _redis_get_float(f"llm:monthly_cost:{provider}:{month}")
        prov_local = _provider_cost_eur.get(provider, 0.0)
        prov_cost = max(prov_redis, prov_local)
        if prov_cost >= cap:
            return False, f"provider_cap_reached: {provider} €{prov_cost:.3f}/€{cap:.2f}"

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
    """Record a successful LLM call with cost tracking + budget threshold alerts."""
    global _monthly_cost_eur
    _ensure_day()
    _ensure_month()
    today = _today()
    month = _this_month()

    _daily_counts[module] = _daily_counts.get(module, 0) + 1
    _cycle_counts[module] = _cycle_counts.get(module, 0) + 1
    _total_tokens[module] = _total_tokens.get(module, 0) + tokens_used
    _last_call[module] = time.monotonic()

    # Cost tracking (global)
    cost = _estimate_cost(tokens_used, model)
    _monthly_cost_eur += cost
    _redis_incrbyfloat(f"llm:monthly_cost:{month}", cost)

    # Cost tracking (per-provider)
    if provider:
        _provider_cost_eur[provider] = _provider_cost_eur.get(provider, 0.0) + cost
        _redis_incrbyfloat(f"llm:monthly_cost:{provider}:{month}", cost)

    # Redis persistence
    _redis_incr(f"llm:daily:{module}:{today}")
    _redis_incr(f"llm:daily:_global:{today}")

    log.info(
        "llm_budget: call module=%s provider=%s model=%s tokens=%d cost=€%.4f daily=%d monthly=€%.3f",
        module, provider, model, tokens_used, cost, _daily_counts.get(module, 0), _monthly_cost_eur,
    )

    # Budget threshold alert (90%) — deduped, one per provider per month
    if provider:
        _check_budget_threshold_alert(provider, month)


def _check_budget_threshold_alert(provider: str, month: str):
    """
    Send Telegram alert when a provider reaches 90% of its monthly budget.
    Deduped: fires once per provider per month.
    """
    dedup_key = f"{provider}:{month}"
    if _budget_alert_sent.get(dedup_key):
        return  # already sent this month

    cap = ANTHROPIC_MONTHLY_CAP if provider == "anthropic" else OPENAI_MONTHLY_CAP
    prov_redis = _redis_get_float(f"llm:monthly_cost:{provider}:{month}")
    prov_local = _provider_cost_eur.get(provider, 0.0)
    prov_cost = max(prov_redis, prov_local)

    pct = prov_cost / cap if cap > 0 else 0
    if pct < _BUDGET_ALERT_THRESHOLD:
        return  # below threshold

    # Mark as sent BEFORE sending (prevent race)
    _budget_alert_sent[dedup_key] = True

    # Calculate remaining calls estimate
    avg_cost_per_call = prov_cost / max(sum(_daily_counts.values()), 1)
    remaining_eur = cap - prov_cost
    remaining_calls = int(remaining_eur / avg_cost_per_call) if avg_cost_per_call > 0 else 0

    log.warning(
        "llm_budget: THRESHOLD ALERT %s at %.0f%% (€%.3f/€%.2f) — ~%d calls remaining",
        provider, pct * 100, prov_cost, cap, remaining_calls,
    )

    # Send Telegram alert (non-blocking, non-fatal)
    try:
        from app.services.telegram_agent import send_message, is_configured
        if is_configured():
            send_message(
                f"🔔 *BUDGET ALERT* — {provider.upper()} usage at {pct:.0%} "
                f"of monthly €{cap:.0f} cap\n\n"
                f"Spent: €{prov_cost:.3f} / €{cap:.2f}\n"
                f"Remaining: €{remaining_eur:.3f} (~{remaining_calls} calls)\n"
                f"Global: €{_monthly_cost_eur:.3f} / €{MONTHLY_EUR_CAP:.2f}"
            )
    except Exception as exc:
        log.debug("llm_budget: telegram alert failed (non-fatal): %s", exc)


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

    # Per-provider costs
    provider_costs = {}
    for prov, cap in [("anthropic", ANTHROPIC_MONTHLY_CAP), ("openai", OPENAI_MONTHLY_CAP)]:
        prov_redis = _redis_get_float(f"llm:monthly_cost:{prov}:{month}")
        prov_local = _provider_cost_eur.get(prov, 0.0)
        prov_cost = max(prov_redis, prov_local)
        provider_costs[prov] = {
            "cost_eur": round(prov_cost, 4),
            "cap_eur": cap,
            "remaining_eur": round(max(0, cap - prov_cost), 4),
            "usage_pct": round(prov_cost / cap * 100, 1) if cap > 0 else 0,
            "cap_reached": prov_cost >= cap,
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
        "provider_costs": provider_costs,
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
