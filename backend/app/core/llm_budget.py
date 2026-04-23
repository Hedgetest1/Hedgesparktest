"""
llm_budget.py — Central LLM budget guard for all AI provider calls.

Every LLM call must go through check_budget() before making a request
and record_usage() after a successful response.

Budget enforcement:
    1. Monthly EUR scaled cap: floor MONTHLY_EUR_CAP (dev €10, env-configurable
       via LLM_MONTHLY_BUDGET_EUR), per-merchant scaling (_LLM_EUR_PER_MERCHANT),
       ceiling _LLM_MAX_MONTHLY_EUR (€500). Source of truth for CLAUDE.md §8.1.
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
# Monthly EUR hard caps — env-configurable for operator control.
#
# SOURCE OF TRUTH for CLAUDE.md §8.1 and principle #9. If you change any
# of these constants, update CLAUDE.md + EXECUTION_POLICY.md in the SAME
# commit. The doctrine points here; the number lives here only.
# ---------------------------------------------------------------------------
MONTHLY_EUR_CAP = float(_os.getenv("LLM_MONTHLY_BUDGET_EUR", "10.0"))

# Per-provider caps (independent of global cap)
ANTHROPIC_MONTHLY_CAP = float(_os.getenv("ANTHROPIC_MONTHLY_BUDGET_EUR", "10.0"))
OPENAI_MONTHLY_CAP = float(_os.getenv("OPENAI_MONTHLY_BUDGET_EUR", "10.0"))

# ---------------------------------------------------------------------------
# Per-plan tiered budgets (α4 — elite roadmap)
# Dominating competitors requires better-than-5€-total intelligence. Each
# plan gets its own per-merchant monthly ceiling so Pro customers can
# afford richer LLM usage without inflating the aggregate cap.
# Values in EUR / merchant / month.
# ---------------------------------------------------------------------------
PLAN_MONTHLY_BUDGETS_EUR: dict[str, float] = {
    "free":  0.00,   # no LLM — deterministic only
    "trial": 0.10,   # discovery tier, tight cap
    "core":  0.30,   # entry (€49) — occasional LLM chatbot fallback
    "plus":  1.00,   # Pro (€99) — richer chatbot, more aggressive fallbacks
    "pro":   1.00,   # alias for plus (legacy plan label)
    "agency": 5.00,  # Agency (€999) — full autonomy, LLM-rich workflows
}


def get_plan_budget_eur(plan: str | None) -> float:
    """Return per-merchant per-month LLM budget for the given plan.
    Unknown plans fall back to 'core'."""
    if plan is None:
        return PLAN_MONTHLY_BUDGETS_EUR["free"]
    return PLAN_MONTHLY_BUDGETS_EUR.get(plan.lower(), PLAN_MONTHLY_BUDGETS_EUR["core"])


def can_charge_merchant(db, shop_domain: str, estimated_cost_eur: float) -> tuple[bool, str]:
    """Check if a merchant has headroom in their plan budget for an
    additional LLM charge. Returns (allowed, reason).

    The per-merchant counter is tracked separately from the global cap
    so expensive merchants don't starve cheap ones.
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            # Fail-closed: no Redis → deny LLM spend for merchant accounting
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.check_merchant")
            return False, "redis_unavailable"

        # Lookup plan
        from app.models.merchant import Merchant
        m = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if m is None:
            return False, "merchant_not_found"
        plan = (m.plan or "free").lower()
        cap = get_plan_budget_eur(plan)
        if cap <= 0:
            return False, f"plan_no_llm:{plan}"

        # Redis key: hs:llm:merchant:{shop}:{YYYY-MM}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        key = f"hs:llm:merchant:{shop_domain}:{month}"
        raw = rc.get(key)
        spent = float(raw) if raw else 0.0
        if spent + estimated_cost_eur > cap:
            return False, f"plan_budget_exhausted:{spent:.3f}/{cap:.2f}"
        return True, f"ok:{spent:.3f}/{cap:.2f}"
    except Exception as exc:
        log.warning("llm_budget: per-merchant check failed: %s", exc)
        return False, f"check_error:{type(exc).__name__}"


def record_merchant_charge(shop_domain: str, cost_eur: float) -> None:
    """Increment a merchant's monthly LLM spend counter. Call AFTER a
    successful LLM call."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.record_merchant_charge")
            return
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        key = f"hs:llm:merchant:{shop_domain}:{month}"
        rc.incrbyfloat(key, cost_eur)
        rc.expire(key, 40 * 86400)  # 40d TTL so month boundary is covered
    except Exception as exc:
        log.warning("llm_budget: per-merchant record failed: %s", exc)

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
        "max_calls_per_day": 48,
        "max_calls_per_cycle": 1,
        "max_tokens_per_request": 512,
        "cooldown_seconds": 900,  # 15 min — matches agent_worker cycle
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
    # B1 (2026-04-19) — on-alert responder LLM triage. Critical class
    # because a real production incident firing at 03:00 must be
    # triaged before the 08:00 Rome brief. Low cooldown because that's
    # the whole point of the module; cycle cap of 5 mirrors the
    # `_find_untrimmed_criticals` limit so a burst of 10 alerts in one
    # cycle stays within budget. Daily cap generous for dev phase; at
    # 30 calls × ~3k input + ~500 output tokens (~€0.016 each on
    # Claude Sonnet 4) that's a €14/mo worst case — below the €10/mo
    # floor in practice because real alerts are sparse (5-10/day).
    "on_alert_responder": {
        "max_calls_per_day": 30,
        "max_calls_per_cycle": 5,
        "max_tokens_per_request": 1024,
        "cooldown_seconds": 60,
    },
    # B2 (2026-04-19) — real-model LLM drift corpus. Runs once per
    # week on a small fixed prompt corpus to detect behavioral drift
    # in Claude/OpenAI output shape (refusal rate, JSON validity,
    # deterministic-command format). Single execution burst: ~20
    # calls in a few minutes, bounded daily to keep the monthly cost
    # under €1.
    "llm_realmodel_drift": {
        "max_calls_per_day": 25,
        "max_calls_per_cycle": 25,
        "max_tokens_per_request": 512,
        "cooldown_seconds": 0,
    },
    # Strada 4 (2026-04-20) — merchant-facing AI analytics assistant.
    # Lite/Pro accessible; merchant-triggered (user clicks "Ask").
    # Cap: 60 calls/day global (soft ceiling across all merchants),
    # 30s cooldown per merchant enforced upstream by React state.
    # max_tokens matches the 600-token answer cap in the service.
    # At ~€0.004 per call (Sonnet 4, ~1.5k input + 500 output), the
    # daily ceiling caps monthly cost at ~€7 — below the €10 dev-phase
    # floor.
    "analytics_assistant": {
        "max_calls_per_day": 60,
        "max_calls_per_cycle": 60,
        "max_tokens_per_request": 600,
        "cooldown_seconds": 30,
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

# multi-worker: redis-mirrored — counters dual-written via _redis_incr /
# _redis_incrbyfloat helpers. check_budget() reconciles by taking
# max(redis_value, local_value) before decision. 4× per-worker counting
# is tolerated because the Redis mirror is the authoritative total.
_daily_counts: dict[str, int] = {}  # multi-worker: redis-mirrored
_cycle_counts: dict[str, int] = {}  # multi-worker: redis-mirrored
_last_call: dict[str, float] = {}  # multi-worker: redis-mirrored
_total_tokens: dict[str, int] = {}
_blocked_count: int = 0
_day_key: str = ""

# multi-worker: redis-mirrored — monthly spend uses max(redis, local) reconciliation
_monthly_cost_eur: float = 0.0
_provider_cost_eur: dict[str, float] = {}  # per-provider cost tracking
_month_key: str = ""

# multi-worker: accept-degrade — alert dedup per-worker = 4× alerts max on
# outage, acceptable (operator prefers 4 dupes to a silent miss)
_budget_alert_sent: dict[str, bool] = {}  # "anthropic:2026-04" → True

# multi-worker: redis-backed — fleet-coordinated via hs:llm:429:{provider}
# SETEX with TTL=backoff_secs (see record_429). In-process dict retained
# as telemetry (count) and Redis-outage gate only.
_provider_429: dict[str, dict] = {}   # provider → {last_429: float, backoff_secs: int, count: int}
_MAX_BACKOFF = 300   # 5 minutes max
_INITIAL_BACKOFF = 5  # 5 seconds initial

# multi-worker: accept-degrade — alert dedup per-worker (same rationale as _budget_alert_sent)
_both_failed_alert_sent: dict[str, bool] = {}

# multi-worker: accept-degrade — alert dedup per-worker
_exhaustion_alert_sent: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# Priority tiers — budget pressure gating
# ---------------------------------------------------------------------------

_MODULE_TIER: dict[str, str] = {
    "orchestrator": "critical",
    "bugfix_proposal": "important",
    "evolution_audit": "important",
    "monthly_opus_audit": "important",
    "nudge_composer": "optional",
    # On-alert responder is critical: a paged-at-night incident
    # should not be suppressed because budget pressure downgraded the
    # module tier. Orchestrator tier is reserved for it.
    "on_alert_responder": "critical",
    # Drift corpus is optional: it's meta-quality tracking, not a
    # runtime guardrail. Budget pressure rightly skips it.
    "llm_realmodel_drift": "optional",
    # Analytics assistant is "important" but not critical: under
    # severe budget pressure we downgrade to the deterministic
    # fallback rather than blocking the call entirely. The service
    # handles the downgrade gracefully via the `degraded=true` flag
    # in the response.
    "analytics_assistant": "important",
    "default": "optional",
}

# ---------------------------------------------------------------------------
# Scaled budget — dynamic cap by merchant count
# ---------------------------------------------------------------------------

_LLM_EUR_PER_MERCHANT = float(_os.getenv("LLM_EUR_PER_MERCHANT", "0.10"))
_LLM_MAX_MONTHLY_EUR = float(_os.getenv("LLM_MAX_MONTHLY_EUR", "500.0"))
_effective_cap_cache: dict[str, object] = {"value": None, "computed_at": 0.0, "merchants": 0}


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


def _get_module_tier(module: str) -> str:
    """Return priority tier for a module: critical, important, or optional."""
    return _MODULE_TIER.get(module, _MODULE_TIER["default"])


def _get_mode_override() -> str:
    """Read operator mode override from Redis. Returns 'full', 'limited', or 'off'."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.mode_override_read")
            return "full"
        val = rc.get("llm:mode_override")
        if val and val.decode() if isinstance(val, bytes) else val:
            mode = (val.decode() if isinstance(val, bytes) else val).strip().lower()
            if mode in ("off", "limited", "full"):
                return mode
    except Exception as exc:
        log.warning("llm_budget: mode_override read failed: %s", exc)
    return "full"


def set_mode_override(mode: str) -> bool:
    """Set operator mode override. Returns True on success, False if invalid."""
    if mode not in ("off", "limited", "full"):
        return False
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.mode_override_write")
            return False
        rc.set("llm:mode_override", mode, ex=86400 * 30)
        return True
    except Exception as exc:
        log.warning("llm_budget: mode_override write failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Redis helpers (optional persistence)
# ---------------------------------------------------------------------------

def _redis_incr(key: str, ttl: int = 86400) -> int | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.counter_incr")
            return None
        val = rc.incr(key)
        if val == 1:
            rc.expire(key, ttl)
        return val
    except Exception as exc:
        log.warning("llm_budget: redis counter incr failed: %s", exc)
        return None


def _redis_get(key: str) -> int:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.counter_read")
            return 0
        val = rc.get(key)
        return int(val) if val else 0
    except Exception as exc:
        log.warning("llm_budget: redis counter read failed: %s", exc)
        return 0


def _redis_incrbyfloat(key: str, amount: float, ttl: int = 2678400) -> float | None:
    """Increment a Redis float counter. TTL defaults to 31 days."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.cost_incr")
            return None
        val = rc.incrbyfloat(key, amount)
        rc.expire(key, ttl)
        return float(val)
    except Exception as exc:
        log.warning("llm_budget: redis cost incr failed: %s", exc)
        return None


def _redis_get_float(key: str) -> float:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_budget.cost_read")
            return 0.0
        val = rc.get(key)
        return float(val) if val else 0.0
    except Exception as exc:
        log.warning("llm_budget: redis cost read failed: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 429 backoff
# ---------------------------------------------------------------------------

_BACKOFF_REDIS_PREFIX = "hs:llm:429"


def record_429(provider: str):
    """Record a 429 response from a provider. Triggers exponential backoff
    across the entire uvicorn worker fleet via Redis SETEX.

    Local dict `_provider_429` retained only as (a) count telemetry and
    (b) Redis-outage fallback gate.
    """
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

    # Redis: broadcast the backoff to every worker. Key auto-expires when
    # the backoff period ends — no explicit "clear" needed.
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(
                f"{_BACKOFF_REDIS_PREFIX}:{provider}",
                int(state["backoff_secs"]),
                "1",
            )
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("llm_budget.record_429.redis_error")

    log.warning(
        "llm_budget: 429 from %s — backoff %ds (total 429s today: %d)",
        provider, state["backoff_secs"], state["count"],
    )


def is_provider_backed_off(provider: str) -> bool:
    """Check if a provider is in 429 backoff across the fleet.

    Redis-primary (cross-worker coherent) with in-process fallback.
    Under multi-worker, a 429 on one worker backs off ALL workers
    until the Redis TTL expires.
    """
    # Redis primary — TTL means "still backed off"; absence means "available"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            if rc.exists(f"{_BACKOFF_REDIS_PREFIX}:{provider}") > 0:
                return True
            # Redis says available — sync local state so telemetry doesn't lie
            state = _provider_429.get(provider)
            if state and state.get("backoff_secs", 0) > 0:
                state["backoff_secs"] = 0
                state["count"] = 0
            return False
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("llm_budget.is_backed_off.redis_error")

    # Fallback: in-process gate (Redis outage, single-worker, or cold-start)
    state = _provider_429.get(provider)
    if not state or state["backoff_secs"] == 0:
        return False
    elapsed = time.monotonic() - state["last_429"]
    if elapsed >= state["backoff_secs"]:
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
    month = _this_month()
    tier = _get_module_tier(module)

    # Check operator mode override
    mode = _get_mode_override()
    if mode == "off" and tier != "critical":
        return False, f"mode_off: operator disabled LLM calls"
    if mode == "limited" and tier == "optional":
        return False, f"mode_limited: optional modules blocked by operator"

    # Check monthly EUR cap (global)
    redis_cost = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_cost, _monthly_cost_eur)
    if monthly_cost >= MONTHLY_EUR_CAP:
        log.warning(
            "llm_budget: BLOCKED — global budget exceeded: €%.3f/€%.2f",
            monthly_cost, MONTHLY_EUR_CAP,
        )
        _send_exhaustion_alert("global", month, monthly_cost, MONTHLY_EUR_CAP)
        return False, f"monthly_eur_cap_reached: €{monthly_cost:.3f}/€{MONTHLY_EUR_CAP:.2f}"

    # Check per-provider caps
    for provider, cap in [("anthropic", ANTHROPIC_MONTHLY_CAP), ("openai", OPENAI_MONTHLY_CAP)]:
        prov_redis = _redis_get_float(f"llm:monthly_cost:{provider}:{month}")
        prov_local = _provider_cost_eur.get(provider, 0.0)
        prov_cost = max(prov_redis, prov_local)
        if prov_cost >= cap:
            log.warning(
                "llm_budget: BLOCKED — %s budget exceeded: €%.3f/€%.2f",
                provider, prov_cost, cap,
            )
            _send_exhaustion_alert(provider, month, prov_cost, cap)
            return False, f"provider_cap_reached: {provider} €{prov_cost:.3f}/€{cap:.2f}"

    # Priority tier gating under budget pressure
    remaining_pct = 1.0 - (monthly_cost / MONTHLY_EUR_CAP) if MONTHLY_EUR_CAP > 0 else 1.0
    if tier == "optional" and remaining_pct < 0.20:
        return False, f"tier_blocked: optional modules blocked at <20% remaining ({remaining_pct:.0%})"
    if tier == "important" and remaining_pct < 0.10:
        return False, f"tier_blocked: important modules blocked at <10% remaining ({remaining_pct:.0%})"

    # Check cooldown
    # Default to float('-inf') — not 0 — so a module that has never been called
    # is treated as "called infinitely long ago" (cooldown NOT active).
    # Using 0 was a latent bug: time.monotonic() grows from 0 at process start,
    # so after ~107s uptime, 900-107=793s cooldown appeared active for every fresh module.
    last = _last_call.get(module, float("-inf"))
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

    # Budget exhaustion alert (100%) — deduped per scope per month
    if _monthly_cost_eur >= MONTHLY_EUR_CAP:
        _send_exhaustion_alert("global", month, _monthly_cost_eur, MONTHLY_EUR_CAP)
    if provider:
        prov_cost = _provider_cost_eur.get(provider, 0.0)
        prov_cap = ANTHROPIC_MONTHLY_CAP if provider == "anthropic" else OPENAI_MONTHLY_CAP
        if prov_cost >= prov_cap:
            _send_exhaustion_alert(provider, month, prov_cost, prov_cap)


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

    effective_cap = get_effective_monthly_cap()

    return {
        "date": _today(),
        "month": month,
        "global_calls_today": sum(_daily_counts.values()),
        "global_max_per_day": GLOBAL_MAX_CALLS_PER_DAY,
        "blocked_today": _blocked_count,
        "monthly_cost_eur": round(monthly_cost, 4),
        "monthly_cap_eur": effective_cap,
        "monthly_cap_static_floor": MONTHLY_EUR_CAP,
        "monthly_cap_scaled_by_merchants": _effective_cap_cache.get("merchants", 0),
        "monthly_remaining_eur": round(max(0, effective_cap - monthly_cost), 4),
        "monthly_cap_reached": monthly_cost >= effective_cap,
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
    _provider_cost_eur.clear()
    _budget_alert_sent.clear()
    _both_failed_alert_sent.clear()
    _exhaustion_alert_sent.clear()
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            for key in rc.scan_iter(match="llm:*", count=100):
                rc.delete(key)
    except Exception as exc:
        log.warning("llm_budget: redis counter reset failed: %s", exc)


# ---------------------------------------------------------------------------
# Exhaustion alerts (100% cap reached)
# ---------------------------------------------------------------------------

def _send_exhaustion_alert(scope: str, month: str, cost: float, cap: float):
    """Send Telegram alert when budget is fully exhausted. Deduped per scope per month."""
    dedup_key = f"{scope}:{month}"
    if _exhaustion_alert_sent.get(dedup_key):
        return
    _exhaustion_alert_sent[dedup_key] = True

    if scope == "global":
        msg = (
            f"🚨 *LLM BUDGET EXHAUSTED — SYSTEM DEGRADED*\n\n"
            f"GLOBAL cap reached: €{cost:.3f} / €{cap:.2f}\n"
            f"LLM CALLS ARE NOW BLOCKED\n"
            f"System is in DEGRADED MODE"
        )
    else:
        provider_label = scope.upper()
        msg = (
            f"🚨 *LLM BUDGET EXHAUSTED — {provider_label} cap reached*\n\n"
            f"{provider_label} cap reached: €{cost:.3f} / €{cap:.2f}\n"
            f"Anthropic: €{_provider_cost_eur.get('anthropic', 0):.3f}\n"
            f"Openai: €{_provider_cost_eur.get('openai', 0):.3f}\n"
            f"Global: €{_monthly_cost_eur:.3f} / €{MONTHLY_EUR_CAP:.2f}"
        )

    try:
        from app.services.telegram_agent import send_message, is_configured
        if is_configured():
            send_message(msg)
    except Exception as exc:
        log.debug("llm_budget: exhaustion alert failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Both-providers-failed alert
# ---------------------------------------------------------------------------

def alert_both_providers_failed(
    module: str,
    anthropic_error: str = "",
    openai_error: str = "",
):
    """Alert when both LLM providers failed. Deduped: once per module per hour."""
    hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    dedup_key = f"{module}:{hour_key}"
    if _both_failed_alert_sent.get(dedup_key):
        return
    _both_failed_alert_sent[dedup_key] = True

    msg = (
        f"🚨 *BOTH LLM PROVIDERS FAILED* — module={module}\n\n"
        f"Anthropic: {anthropic_error}\n"
        f"OpenAI: {openai_error}\n"
        f"Module is running in deterministic fallback mode."
    )

    try:
        from app.services.telegram_agent import send_message, is_configured
        if is_configured():
            send_message(msg)
    except Exception as exc:
        log.debug("llm_budget: both-failed alert failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def is_llm_disabled() -> bool:
    """Return True if LLM calls are effectively disabled (cap reached or mode=off)."""
    _ensure_month()
    if _get_mode_override() == "off":
        return True
    month = _this_month()
    redis_cost = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_cost, _monthly_cost_eur)
    if monthly_cost >= MONTHLY_EUR_CAP:
        return True
    return False


def get_llm_status() -> tuple[str, str]:
    """Return (emoji, label) for operator dashboards.

    Returns:
        ("🟢", "ACTIVE") — healthy
        ("🟡", "LIMITED") — approaching cap or operator limited
        ("🔴", "DISABLED ...") — cap reached or operator off
    """
    _ensure_month()
    mode = _get_mode_override()

    if mode == "off":
        return "🔴", "DISABLED — operator override"
    if mode == "limited":
        return "🟡", "LIMITED — operator override"

    month = _this_month()
    redis_cost = _redis_get_float(f"llm:monthly_cost:{month}")
    monthly_cost = max(redis_cost, _monthly_cost_eur)

    if monthly_cost >= MONTHLY_EUR_CAP:
        return "🔴", "DISABLED — global budget exhausted"

    # Check per-provider caps
    for prov, cap in [("anthropic", ANTHROPIC_MONTHLY_CAP), ("openai", OPENAI_MONTHLY_CAP)]:
        prov_redis = _redis_get_float(f"llm:monthly_cost:{prov}:{month}")
        prov_local = _provider_cost_eur.get(prov, 0.0)
        prov_cost = max(prov_redis, prov_local)
        if prov_cost >= cap:
            return "🔴", f"DISABLED — {prov} budget exhausted"

    # Check if approaching cap (90%+)
    if MONTHLY_EUR_CAP > 0 and monthly_cost / MONTHLY_EUR_CAP >= _BUDGET_ALERT_THRESHOLD:
        return "🟡", "LIMITED"

    return "🟢", "ACTIVE"


# ---------------------------------------------------------------------------
# Scaled budget — dynamic cap by merchant count
# ---------------------------------------------------------------------------

def get_effective_monthly_cap() -> float:
    """Return effective monthly cap, scaled by active merchant count.

    Logic:
        scaled = merchants * _LLM_EUR_PER_MERCHANT
        effective = clamp(scaled, floor=MONTHLY_EUR_CAP, ceiling=_LLM_MAX_MONTHLY_EUR)

    Cached for 1 hour to avoid DB queries on every budget check.
    """
    now = time.monotonic()
    cached_at = _effective_cap_cache.get("computed_at", 0.0)
    cached_val = _effective_cap_cache.get("value")

    if cached_val is not None and (now - cached_at) < 3600:
        merchants = _effective_cap_cache.get("merchants", 0)
        scaled = merchants * _LLM_EUR_PER_MERCHANT
        effective = max(scaled, MONTHLY_EUR_CAP)
        return min(effective, _LLM_MAX_MONTHLY_EUR)

    # Query merchant count from DB
    merchants = 0
    try:
        from app.core.database import SessionLocal
        from app.models.merchant import Merchant
        db = SessionLocal()
        try:
            merchants = db.query(Merchant).filter(Merchant.install_status == "active").count()
        finally:
            db.close()
    except Exception as exc:
        log.warning("llm_budget: merchant count query failed: %s", exc)
        merchants = _effective_cap_cache.get("merchants", 0)

    _effective_cap_cache["merchants"] = merchants
    _effective_cap_cache["computed_at"] = now
    _effective_cap_cache["value"] = True

    scaled = merchants * _LLM_EUR_PER_MERCHANT
    effective = max(scaled, MONTHLY_EUR_CAP)
    return min(effective, _LLM_MAX_MONTHLY_EUR)
