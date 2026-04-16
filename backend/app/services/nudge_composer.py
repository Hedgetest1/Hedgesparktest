"""
nudge_composer.py — AI-powered nudge copy generation service.

Public interface
----------------
    compose_nudge_variants(
        product_title:     str,
        product_url:       str,
        signals:           dict,
        data_window_hours: int = 72,
    ) -> tuple[list[dict], dict]

    Async.  Returns (variants_list, composer_meta).

      variants_list : copy_variants-compatible format:
                      [{"variant_name": str, "copy_config": dict}, ...]
      composer_meta : {"model", "signal_basis", "strategy_pair",
                       "fallback_used", "variant_count", "rejection_reason"}

Design principles
-----------------
Signal-grounded copy only
    The LLM receives only real behavioral metrics from the DB.
    The system prompt explicitly prohibits:
      - Invented visitor counts or social proof numbers
      - Inventory scarcity claims ("Only 3 left!")
      - Fake urgency ("Limited time!", "Today only!")
      - Review counts, ratings, price comparisons
    A field-level validation layer enforces these constraints on the
    model output.  Any violation triggers the rule-based fallback.

Variant strategy selection
    Variant 1: always "social_proof"  (control, quantity signal)
    Variant 2: dynamically selected from:
        "high_interest"   — traffic momentum (default, always available)
        "return_visitor"  — loyalty signal   (requires return_visitor_count_7d ≥ 5)
        "engagement_depth"— depth signal     (requires avg_dwell_24h ≥ 15)
    Selection ranks the available non-social-proof strategies by signal
    strength and picks the strongest.

Fallback chain
    1. OpenAI → JSON parse → validate → use
    2. Validation failure → rule-based fallback (nudge_engine._build_all_variants)
    3. OpenAI API error → rule-based fallback
    composer_meta.fallback_used records which path was taken.

Output copy_config schema (per variant)
    {
        "headline":          str   (≤ 8 words, ≤ 60 chars)
        "subtext":           str   (≤ 25 words, ≤ 160 chars, may be null)
        "badge":             str   (≤ 3 words, ≤ 20 chars, may be null)
        "visitor_count":     int | null  — must equal real count or null
        "data_window_hours": int         — must equal input value exactly
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

import httpx

from app.services.nudge_engine import _build_all_variants

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_OPENAI_API_KEY: str  = os.getenv("OPENAI_API_KEY", "")
_OPENAI_API_URL: str  = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL:   str  = "gpt-4o-mini"
_OPENAI_TIMEOUT: float = 20.0   # seconds
_OPENAI_MAX_TOKENS: int = 600

# Forbidden phrases — claims that can't be grounded in available signals.
# Case-insensitive full-word match.
_FORBIDDEN_PATTERNS: list[str] = [
    r"viewing\s+right\s+now",
    r"watching\s+right\s+now",
    r"left\s+in\s+stock",
    r"limited\s+stock",
    r"almost\s+gone",
    r"selling\s+fast",
    r"only\s+\d+\s+left",
    r"limited\s+time",
    r"today\s+only",
    r"ends\s+tonight",
    r"sale\s+ends",
    r"\d+\s*%\s*off",
    r"\d+\s+reviews?",
    r"\d+\s+ratings?",
    r"stars?\s+out\s+of",
    r"free\s+shipping",
    r"lowest\s+price",
    r"price\s+drop",
    r"back\s+in\s+stock",
]
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

# Transient HTTP status codes that warrant a retry.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Retry schedule: [delay_after_attempt_1, delay_after_attempt_2, ...]
# Total max time with 20s timeout: 20 + 2 + 20 + 4 + 20 = 66s worst-case.
# Keep attempts low — we'd rather fall back fast than block the request path.
_RETRY_DELAYS = (2.0, 4.0)   # 3 total attempts (initial + 2 retries)

# ---------------------------------------------------------------------------
# Per-shop daily OpenAI call budget
#
# Prevents a single shop or a looping caller from running unbounded OpenAI
# spend.  Uses Redis when available; degrades to an in-memory counter when
# Redis is absent (counter resets on process restart — acceptable degradation).
#
# Budget is per shop per UTC calendar day.  The counter key expires at
# midnight UTC so budget resets automatically without a cron job.
#
# DEFAULT_DAILY_BUDGET_CALLS can be overridden per-deploy via env var:
#   OPENAI_DAILY_CALLS_PER_SHOP=100
# ---------------------------------------------------------------------------

_DAILY_BUDGET: int = int(os.getenv("OPENAI_DAILY_CALLS_PER_SHOP", "50"))

# Fallback in-memory budget when Redis is unavailable.
# dict[str, (count: int, date: str)]  — key = shop_domain
_mem_budget: dict[str, tuple[int, str]] = {}
_mem_budget_lock = Lock()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget_key(shop_domain: str) -> str:
    return f"hs:ai_budget:{shop_domain}:{_today_utc()}"


def _check_and_increment_budget(shop_domain: str) -> bool:
    """
    Return True if the shop is within its daily OpenAI call budget.
    Atomically increments the counter — the call is counted before it is made.

    Returns False (budget exceeded) when the shop has already hit _DAILY_BUDGET
    calls today.  The caller should fall back to rule-based copy immediately.

    Redis path: INCR + EXPIRE (atomic enough for our purpose — small over-run
    on a race is acceptable vs the cost of a distributed lock).
    Memory fallback: simple dict with date check (resets on restart).
    """
    try:
        from app.core.redis_client import _client as _redis_client
        client = _redis_client()
        if client is not None:
            key = _budget_key(shop_domain)
            count = client.incr(key)
            if count == 1:
                # First call today — set TTL to expire at tomorrow midnight UTC
                now = datetime.now(timezone.utc)
                secs_until_midnight = int(
                    86400 - (now.hour * 3600 + now.minute * 60 + now.second)
                )
                client.expire(key, secs_until_midnight + 60)  # +60s safety margin
            if count > _DAILY_BUDGET:
                log.warning(
                    "nudge_composer: daily OpenAI budget exceeded for shop=%s "
                    "(count=%d limit=%d) — using rule-based fallback",
                    shop_domain, count, _DAILY_BUDGET,
                )
                return False
            return True
    except Exception as exc:
        log.warning("nudge_composer: budget check failed: %s", exc)

    # In-memory fallback
    today = _today_utc()
    with _mem_budget_lock:
        existing = _mem_budget.get(shop_domain)
        if existing is None or existing[1] != today:
            _mem_budget[shop_domain] = (1, today)
            return True
        count, _ = existing
        count += 1
        _mem_budget[shop_domain] = (count, today)
        if count > _DAILY_BUDGET:
            log.warning(
                "nudge_composer: daily OpenAI budget exceeded (in-memory) for shop=%s "
                "(count=%d limit=%d) — using rule-based fallback",
                shop_domain, count, _DAILY_BUDGET,
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Variant strategy registry
# ---------------------------------------------------------------------------

# Each strategy defines:
#   framing_instruction — what the LLM is told to emphasize for this variant
#   signal_keys         — behavioral metrics this strategy draws from
#   score_fn            — callable(signals) → float, used to rank strategies

_STRATEGIES: dict[str, dict] = {
    "social_proof": {
        "framing_instruction": (
            "Emphasize the number of real people who have recently shown interest "
            "in this product. Use the exact unique_visitors_24h count when it is ≥ 3. "
            "Frame as genuine social activity, never as a live counter."
        ),
        "signal_keys": ["unique_visitors_24h", "views_24h"],
        "score_fn":    lambda s: float((s.get("unique_visitors_24h") or 0) * 2
                                       + (s.get("views_24h") or 0) * 0.5),
    },
    "high_interest": {
        "framing_instruction": (
            "Emphasize that this product is attracting strong or growing interest. "
            "Use views_24h or views_7d to support the claim. "
            "Avoid implying live concurrent viewers — frame as recent activity."
        ),
        "signal_keys": ["views_24h", "views_7d", "views_1h"],
        "score_fn":    lambda s: float((s.get("views_24h") or 0)
                                       + (s.get("views_1h") or 0) * 5),
    },
    "return_visitor": {
        "framing_instruction": (
            "Emphasize that visitors keep coming back to this product on multiple days, "
            "suggesting genuine considered interest rather than impulse browsing. "
            "Use return_visitor_count_7d if ≥ 5."
        ),
        "signal_keys": ["return_visitor_count_7d"],
        "score_fn":    lambda s: float((s.get("return_visitor_count_7d") or 0) * 4),
        "min_signal":  lambda s: (s.get("return_visitor_count_7d") or 0) >= 5,
    },
    "engagement_depth": {
        "framing_instruction": (
            "Emphasize the depth of engagement — visitors are spending significant time "
            "reading about this product (use avg_dwell_24h in seconds if ≥ 15) and/or "
            "scrolling deeply through the page (use avg_scroll_24h if ≥ 50%). "
            "This signals high purchase intent."
        ),
        "signal_keys": ["avg_dwell_24h", "avg_scroll_24h"],
        "score_fn":    lambda s: float((s.get("avg_dwell_24h") or 0) * 0.8
                                       + (s.get("avg_scroll_24h") or 0) * 0.3),
        "min_signal":  lambda s: (s.get("avg_dwell_24h") or 0) >= 15,
    },
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def compose_nudge_variants(
    product_title:     str,
    product_url:       str,
    signals:           dict,
    data_window_hours: int = 72,
    shop_domain:       str = "",
) -> tuple[list[dict], dict]:
    """
    Generate 2 AI-composed copy variants for a nudge.

    Parameters
    ----------
    product_title     : human-readable product title from products table
    product_url       : canonical product path, e.g. /products/navy-hoodie
    signals           : dict of behavioral metrics from product_metrics row
                        (views_1h, views_24h, unique_visitors_24h, etc.)
    data_window_hours : window the signals cover; injected into copy_config
    shop_domain       : used for per-shop daily budget enforcement (optional —
                        if empty, budget check is per-process not per-shop)

    Returns
    -------
    (variants, meta)
    variants : list of exactly 2 dicts: [{variant_name, copy_config}, ...]
    meta     : {model, signal_basis, strategy_pair, fallback_used,
                variant_count, rejection_reason}
    """
    if not _OPENAI_API_KEY:
        log.warning("nudge_composer: OPENAI_API_KEY not configured — using rule-based fallback")
        return _rule_based_fallback(signals, data_window_hours), _meta(
            fallback_used=True, rejection_reason="OPENAI_API_KEY not configured"
        )

    # ── Budget guard — respect monthly €5 cap + provider backoff ──────
    from app.core.llm_budget import check_budget, record_blocked, is_provider_backed_off
    allowed, reason = check_budget("nudge_composer")
    if not allowed:
        record_blocked("nudge_composer", reason)
        log.info("nudge_composer: budget blocked (%s) — using rule-based fallback", reason)
        return _rule_based_fallback(signals, data_window_hours), _meta(
            fallback_used=True, rejection_reason=f"budget_blocked: {reason}"
        )

    if is_provider_backed_off("openai"):
        record_blocked("nudge_composer", "openai_429_backoff")
        log.info("nudge_composer: OpenAI backed off (429) — using rule-based fallback")
        return _rule_based_fallback(signals, data_window_hours), _meta(
            fallback_used=True, rejection_reason="openai_429_backoff"
        )

    # ── Cache check — serve cached AI response for identical inputs ──────
    # Hash the meaningful input payload to create a deterministic cache key.
    # Only product_url + signals + data_window_hours matter — product_title
    # is only used in the prompt, but similar titles produce similar copy,
    # so we include it for correctness.
    import hashlib as _hashlib
    _cache_payload = json.dumps({
        "p": product_url,
        "t": product_title,
        "s": signals,
        "w": data_window_hours,
    }, sort_keys=True)
    _cache_hash = _hashlib.sha256(_cache_payload.encode()).hexdigest()[:24]

    from app.core.redis_client import cache_get, cache_set, KEY_AI_COMPOSE, TTL_AI_COMPOSE
    _cache_key = KEY_AI_COMPOSE.format(hash=_cache_hash)

    cached = cache_get(_cache_key)
    if cached is not None and isinstance(cached, dict):
        cached_variants = cached.get("variants")
        if cached_variants and isinstance(cached_variants, list):
            log.info(
                "nudge_composer: cache HIT for product=%s (key=%s)",
                product_url, _cache_hash[:12],
            )
            return cached_variants, _meta(
                fallback_used=False,
                variant_count=len(cached_variants),
                cache_hit=True,
            )

    # Budget guard — checked before the API call to prevent cost overruns.
    # Counts the call even before it is made so concurrent requests are
    # counted conservatively (we'd rather refuse one extra than over-spend).
    budget_key = shop_domain or "global"
    if not _check_and_increment_budget(budget_key):
        return _rule_based_fallback(signals, data_window_hours), _meta(
            fallback_used=True, rejection_reason="daily_budget_exceeded"
        )

    # 1. Select 2 variant strategies based on available signals
    strategy_pair = _select_strategy_pair(signals)

    # 2. Build the signal summary to pass to the LLM
    signal_summary = _build_signal_summary(signals, data_window_hours)
    signal_basis   = [k for k in signal_summary if signal_summary[k] is not None
                      and k != "data_window_hours"]

    # 3. Build prompt messages
    messages = _build_messages(
        product_title     = product_title,
        product_url       = product_url,
        signal_summary    = signal_summary,
        strategy_pair     = strategy_pair,
        data_window_hours = data_window_hours,
    )

    # 4. Call OpenAI with retry
    raw_response = None
    try:
        raw_response = await _call_openai_with_retry(messages)
    except Exception as exc:
        log.warning(
            "nudge_composer: OpenAI API error for product=%s after retries: %s — falling back",
            product_url, exc,
        )
        return _rule_based_fallback(signals, data_window_hours), _meta(
            strategy_pair   = strategy_pair,
            signal_basis    = signal_basis,
            fallback_used   = True,
            rejection_reason = f"OpenAI API error after retries: {type(exc).__name__}",
        )

    # 5. Validate and sanitize output
    variants, rejection_reason = _validate_and_sanitize(
        raw_json          = raw_response,
        expected_names    = [s["variant_name"] for s in strategy_pair],
        real_visitor_count = _real_visitor_count(signals),
        data_window_hours  = data_window_hours,
    )

    if variants is None:
        log.warning(
            "nudge_composer: validation rejected AI output for product=%s — reason=%s",
            product_url, rejection_reason,
        )
        return _rule_based_fallback(signals, data_window_hours), _meta(
            strategy_pair    = strategy_pair,
            signal_basis     = signal_basis,
            fallback_used    = True,
            rejection_reason = rejection_reason,
        )

    log.info(
        "nudge_composer: AI variants generated for product=%s strategies=%s",
        product_url, [s["variant_name"] for s in strategy_pair],
    )

    # Cache the validated AI response for identical future requests
    try:
        cache_set(_cache_key, {"variants": variants}, TTL_AI_COMPOSE)
    except Exception as exc:
        log.warning("nudge_composer: cache write failed: %s", exc)

    return variants, _meta(
        strategy_pair    = strategy_pair,
        signal_basis     = signal_basis,
        fallback_used    = False,
        variant_count    = len(variants),
    )


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------

def _select_strategy_pair(signals: dict) -> list[dict]:
    """
    Select 2 variant strategies: social_proof (always first) + best available second.

    Scores each non-social-proof strategy by signal strength.
    Strategies with an unmet min_signal requirement are excluded.
    Falls back to high_interest if no other strategy qualifies.
    """
    # Variant 1: social_proof is always the control
    first = {"variant_name": "social_proof", **_STRATEGIES["social_proof"]}

    # Candidates for variant 2 (exclude social_proof)
    candidates = []
    for name, cfg in _STRATEGIES.items():
        if name == "social_proof":
            continue
        min_ok = cfg.get("min_signal", lambda _: True)(signals)
        if min_ok:
            score = cfg["score_fn"](signals)
            candidates.append((name, cfg, score))

    if not candidates:
        # Fallback: high_interest is always valid
        candidates = [("high_interest", _STRATEGIES["high_interest"], 0.0)]

    # Pick the highest-scoring candidate
    candidates.sort(key=lambda c: c[2], reverse=True)
    best_name, best_cfg, _ = candidates[0]
    second = {"variant_name": best_name, **best_cfg}

    return [first, second]


# ---------------------------------------------------------------------------
# Signal summary builder
# ---------------------------------------------------------------------------

def _build_signal_summary(signals: dict, data_window_hours: int) -> dict:
    """
    Extract and clean behavioral signals for the LLM prompt.
    Only include non-zero, non-None values — avoid polluting the prompt
    with zeroes that the model might misinterpret.
    """
    raw = {
        "unique_visitors_24h":     signals.get("unique_visitors_24h"),
        "views_24h":               signals.get("views_24h"),
        "views_7d":                signals.get("views_7d"),
        "views_1h":                signals.get("views_1h"),
        "cart_conversions_24h":    signals.get("cart_conversions_24h"),
        "return_visitor_count_7d": signals.get("return_visitor_count_7d"),
        "avg_dwell_seconds_24h":   signals.get("avg_dwell_24h"),
        "avg_scroll_pct_24h":      signals.get("avg_scroll_24h"),
        "data_window_hours":       data_window_hours,
    }
    # Strip None and 0 — only send meaningful signals
    return {k: v for k, v in raw.items() if v}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a conversion copywriter for Shopify product pages. Your job is to write
short, persuasive nudge messages that help hesitant shoppers make a decision.

STRICT RULES — violations will be rejected:
1. All claims must be directly derivable from the behavioral_signals provided.
   Do NOT invent visitor counts, view numbers, or engagement metrics.
2. NEVER claim: "X people viewing right now", "only X left in stock",
   "limited time", "today only", "ends tonight", "free shipping",
   "X reviews", "% off", "price drop", or any inventory scarcity claim.
3. visitor_count in copy_config must be null OR equal to a real number
   from behavioral_signals (do not round or modify it).
4. data_window_hours must equal the exact value from behavioral_signals.
5. Return ONLY valid JSON matching the exact schema. No extra keys.
6. headline: maximum 8 words. badge: maximum 3 words.
7. subtext: maximum 25 words. Be specific when data supports it.\
"""

def _build_messages(
    product_title:     str,
    product_url:       str,
    signal_summary:    dict,
    strategy_pair:     list[dict],
    data_window_hours: int,
) -> list[dict]:
    """Build the OpenAI messages list for one nudge generation request."""

    variants_spec = [
        {
            "variant_name":         s["variant_name"],
            "framing_instruction":  s["framing_instruction"],
        }
        for s in strategy_pair
    ]

    user_payload = {
        "product_title":       product_title,
        "product_url":         product_url,
        "behavioral_signals":  signal_summary,
        "variants_to_generate": variants_spec,
        "output_schema": {
            "type":  "object",
            "properties": {
                "variants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["variant_name", "copy_config"],
                        "properties": {
                            "variant_name": {"type": "string"},
                            "copy_config": {
                                "type": "object",
                                "required": ["headline", "data_window_hours"],
                                "properties": {
                                    "headline":          {"type": "string", "maxLength": 60},
                                    "subtext":           {"type": ["string", "null"]},
                                    "badge":             {"type": ["string", "null"]},
                                    "visitor_count":     {"type": ["integer", "null"]},
                                    "data_window_hours": {"type": "integer"},
                                },
                            },
                        },
                    },
                }
            },
        },
    }

    return [
        {"role": "system",  "content": _SYSTEM_PROMPT},
        {"role": "user",    "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


# ---------------------------------------------------------------------------
# OpenAI API call
# ---------------------------------------------------------------------------

async def _call_openai_with_retry(messages: list[dict]) -> str:
    """
    Call the OpenAI Chat Completions API with exponential-backoff retry.

    Retries on transient errors: HTTP 429 (rate limit), 500, 502, 503, 504.
    Raises immediately on permanent errors (400, 401, 403, etc.) since retrying
    won't help.

    Total attempts = 1 + len(_RETRY_DELAYS).  Uses the shared httpx async
    client for connection reuse within a single request.

    Raises the last exception if all attempts fail — callers handle this by
    falling back to rule-based copy.
    """
    last_exc: Exception | None = None

    for attempt, delay in enumerate([(None, *_RETRY_DELAYS)], start=1):
        if attempt > 1:
            await asyncio.sleep(delay)
            log.info(
                "nudge_composer: retry attempt %d/%d after %.1fs",
                attempt, 1 + len(_RETRY_DELAYS), delay,
            )

        try:
            async with httpx.AsyncClient(timeout=_OPENAI_TIMEOUT) as client:
                resp = await client.post(
                    _OPENAI_API_URL,
                    headers={
                        "Authorization": f"Bearer {_OPENAI_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":           _OPENAI_MODEL,
                        "messages":        messages,
                        "max_tokens":      _OPENAI_MAX_TOKENS,
                        "temperature":     0.3,
                        "response_format": {"type": "json_object"},
                    },
                )

                if resp.status_code == 429:
                    from app.core.llm_budget import record_429
                    record_429("openai")

                if resp.status_code in _RETRYABLE_STATUS and attempt <= len(_RETRY_DELAYS):
                    log.warning(
                        "nudge_composer: OpenAI returned %d (transient) — will retry",
                        resp.status_code,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    continue  # retry

                resp.raise_for_status()   # permanent errors raise immediately
                data = resp.json()

                # Record successful usage for budget tracking
                from app.core.llm_budget import record_usage
                tokens = data.get("usage", {}).get("total_tokens", _OPENAI_MAX_TOKENS)
                record_usage("nudge_composer", tokens_used=tokens, provider="openai", model=_OPENAI_MODEL)

                return data["choices"][0]["message"]["content"]

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt <= len(_RETRY_DELAYS):
                log.warning(
                    "nudge_composer: network/timeout error (attempt %d): %s — will retry",
                    attempt, exc,
                )
                continue
            # Last attempt failed
            raise

    # All attempts exhausted — raise the last captured exception
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation and sanitization
# ---------------------------------------------------------------------------

def _validate_and_sanitize(
    raw_json:           str,
    expected_names:     list[str],
    real_visitor_count: Optional[int],
    data_window_hours:  int,
) -> tuple[Optional[list[dict]], Optional[str]]:
    """
    Parse, validate, and sanitize the LLM's JSON output.

    Returns (variants_list, None) on success.
    Returns (None, rejection_reason) on any failure.
    """
    # 1. Parse JSON
    try:
        parsed = json.loads(raw_json)
    except Exception as exc:
        return None, f"JSON parse error: {exc}"

    # 2. Top-level structure
    if not isinstance(parsed, dict) or "variants" not in parsed:
        return None, "Missing top-level 'variants' key"

    raw_variants = parsed["variants"]
    if not isinstance(raw_variants, list) or len(raw_variants) < 2:
        return None, f"Expected ≥2 variants, got {len(raw_variants) if isinstance(raw_variants, list) else 'non-list'}"

    # 3. Build name → variant map from output
    output_map: dict[str, dict] = {}
    for item in raw_variants:
        if isinstance(item, dict) and "variant_name" in item:
            output_map[item["variant_name"]] = item

    # 4. Validate each expected variant
    validated: list[dict] = []
    for name in expected_names:
        if name not in output_map:
            return None, f"Missing variant '{name}' in output"

        item = output_map[name]
        cc   = item.get("copy_config", {})
        if not isinstance(cc, dict):
            return None, f"copy_config for '{name}' is not a dict"

        # Required fields
        headline = cc.get("headline")
        if not headline or not isinstance(headline, str) or not headline.strip():
            return None, f"Missing or empty headline for variant '{name}'"

        # Length caps
        headline = headline.strip()
        if len(headline) > 80:
            headline = " ".join(headline.split()[:8])

        subtext = cc.get("subtext")
        if subtext is not None:
            if not isinstance(subtext, str):
                subtext = None
            else:
                subtext = subtext.strip()[:200] or None

        badge = cc.get("badge")
        if badge is not None:
            if not isinstance(badge, str):
                badge = None
            else:
                badge = badge.strip()[:30] or None

        # visitor_count integrity — must be null or the real count
        vc = cc.get("visitor_count")
        if vc is not None:
            if not isinstance(vc, int):
                vc = None
            elif real_visitor_count is not None and vc != real_visitor_count:
                # Model returned a count that doesn't match the real signal value.
                # Replace with the real count — never let the model override behavioral data.
                log.warning(
                    "nudge_composer: visitor_count mismatch — model said %d, real is %d. "
                    "Replacing with real count to prevent false claim.",
                    vc, real_visitor_count,
                )
                vc = real_visitor_count

        # data_window_hours integrity
        dwh = cc.get("data_window_hours")
        if dwh != data_window_hours:
            # Override silently — model got the window wrong
            dwh = data_window_hours

        # Forbidden phrase check across all text fields
        all_text = " ".join(filter(None, [headline, subtext, badge]))
        if _FORBIDDEN_RE.search(all_text):
            match = _FORBIDDEN_RE.search(all_text)
            return None, f"Forbidden phrase detected in '{name}': '{match.group()}'"

        validated.append({
            "variant_name": name,
            "copy_config": {
                "headline":          headline,
                "subtext":           subtext,
                "badge":             badge,
                "visitor_count":     vc,
                "data_window_hours": dwh,
            },
        })

    return validated, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_visitor_count(signals: dict) -> Optional[int]:
    """
    Extract the most meaningful visitor count from signals.
    Prefer unique_visitors_24h; fall back to views_24h.
    Returns None if no meaningful count available.
    """
    uv = signals.get("unique_visitors_24h")
    if uv and uv >= 3:
        return int(uv)
    v = signals.get("views_24h")
    if v and v >= 3:
        return int(v)
    return None


def _rule_based_fallback(signals: dict, data_window_hours: int) -> list[dict]:
    """
    Fall back to the deterministic rule-based copy builder from nudge_engine.
    Used when OpenAI is unavailable or output fails validation.
    """
    visitor_count = _real_visitor_count(signals)
    variants = _build_all_variants(
        visitor_count  = visitor_count,
        revenue_window = None,
    )
    # Patch data_window_hours to match the input
    for v in variants:
        if isinstance(v.get("copy_config"), dict):
            v["copy_config"]["data_window_hours"] = data_window_hours
    return variants


def _meta(
    model:            str           = _OPENAI_MODEL,
    signal_basis:     list[str]     = None,
    strategy_pair:    list[dict]    = None,
    fallback_used:    bool          = False,
    variant_count:    int           = 2,
    rejection_reason: Optional[str] = None,
    cache_hit:        bool          = False,
) -> dict:
    return {
        "model":            model if not fallback_used else "rule_based_fallback",
        "signal_basis":     signal_basis or [],
        "strategy_pair":    [s["variant_name"] for s in strategy_pair] if strategy_pair else [],
        "fallback_used":    fallback_used,
        "variant_count":    variant_count,
        "rejection_reason": rejection_reason,
        "cache_hit":        cache_hit,
    }
