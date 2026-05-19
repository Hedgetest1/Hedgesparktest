# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
contextual_bandit.py — Per-visitor nudge variant selection via
Thompson Sampling with a Beta-Bernoulli prior per (context, variant).

THE intelligence upgrade (δ1). Instead of picking nudge variants by
static rules, the bandit learns online which variant wins for each
(device × source × category × time-of-day) context. No external ML
dependencies — just closed-form conjugate posteriors.

Model
-----
For each (shop, context, variant) we track:
    alpha = successes + 1  (Bayesian prior = 1)
    beta  = failures  + 1

Thompson sampling: draw a sample from Beta(alpha, beta) for each
variant, pick the highest. High variance → exploration. Low variance
(after many trials) → exploitation.

Outcome definition
------------------
Success = visitor who saw a nudge variant AND converted to purchase
          within 48h.
Failure = visitor who saw a nudge variant AND did NOT convert.

Data sourced from nudge_events (shown/dismissed) joined with
visitor_purchase_sessions (confirmed_at).

Storage
-------
Redis hash: hs:bandit:{shop}:{context_key}:{variant}
    fields: alpha, beta, last_updated

context_key = f"{device}:{source}:{cat}:{tod_bucket}"
    device: desktop|mobile|tablet
    source: direct|google|meta|email|organic|...
    cat:    product category (from opportunity_signals)
    tod_bucket: morning|afternoon|evening|night

Public API
----------
    select_variant(shop, context, variants, strategy='thompson')
        → variant_name  (the chosen arm)

    record_outcome(shop, context, variant, success: bool)
        → None  (updates posterior)

    refresh_from_events(db, shop, window_hours=48)
        → {updated: int}  (replays recent events to update posteriors)

    get_arm_stats(shop, context, variant)
        → dict  (for dashboard / debug)

Integration point
-----------------
nudge_engine.create_or_refresh_nudge currently picks a variant
deterministically. Replace that with:

    from app.services.contextual_bandit import select_variant, make_context
    ctx = make_context(visitor)
    variant = select_variant(shop, ctx, available_variants)

Deterministic given same Redis state, so test suite remains stable
by stubbing the Redis client (`_client()`).
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("contextual_bandit")

_REDIS_PREFIX = "hs:bandit"
_TTL_SECONDS = 90 * 24 * 3600  # 90 days — bandit memory
_TOD_BUCKETS = (
    (0, 6, "night"),
    (6, 12, "morning"),
    (12, 18, "afternoon"),
    (18, 24, "evening"),
)
_DEFAULT_SOURCE = "unknown"
_DEFAULT_CATEGORY = "general"
_DEFAULT_DEVICE = "unknown"

# If a (shop, context, variant) has no history, start with prior (1,1).
# That's a uniform Beta — any draw is equally likely.
_PRIOR_ALPHA = 1.0
_PRIOR_BETA = 1.0


@dataclass
class ArmStats:
    alpha: float
    beta: float
    pulls: int
    successes: int

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        """Returns a 0-1 score of how confident we are in the arm."""
        # Variance of Beta: αβ / ((α+β)² (α+β+1))
        n = self.alpha + self.beta
        if n < 2:
            return 0.0
        var = (self.alpha * self.beta) / ((n * n) * (n + 1))
        # 1 - sqrt(variance) * 2 → maps 0..1
        return max(0.0, min(1.0, 1.0 - math.sqrt(var) * 2))


# ---------------------------------------------------------------------------
# Context derivation
# ---------------------------------------------------------------------------

def _tod_bucket(ts: datetime | None = None) -> str:
    hour = (ts or datetime.now(timezone.utc)).hour
    for lo, hi, name in _TOD_BUCKETS:
        if lo <= hour < hi:
            return name
    return "night"


def make_context(
    *,
    device: str | None = None,
    source: str | None = None,
    category: str | None = None,
    ts: datetime | None = None,
) -> dict[str, str]:
    """Produce a canonical context dict. NULL-safe, lower-cased."""
    return {
        "device": (device or _DEFAULT_DEVICE).lower(),
        "source": (source or _DEFAULT_SOURCE).lower(),
        "category": (category or _DEFAULT_CATEGORY).lower(),
        "tod": _tod_bucket(ts),
    }


def _context_key(ctx: dict[str, str]) -> str:
    return f"{ctx['device']}:{ctx['source']}:{ctx['category']}:{ctx['tod']}"


def _arm_key(shop_domain: str, ctx: dict[str, str], variant: str) -> str:
    return f"{_REDIS_PREFIX}:{shop_domain}:{_context_key(ctx)}:{variant}"


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _read_arm(shop_domain: str, ctx: dict[str, str], variant: str) -> ArmStats:
    rc = _redis()
    if rc is None:
        record_silent_return("contextual_bandit.read_arm")
        return ArmStats(_PRIOR_ALPHA, _PRIOR_BETA, 0, 0)
    try:
        raw = rc.hgetall(_arm_key(shop_domain, ctx, variant))
        if not raw:
            return ArmStats(_PRIOR_ALPHA, _PRIOR_BETA, 0, 0)
        # Redis returns bytes or str depending on client config
        def _to_float(v, default):
            try:
                return float(v.decode() if isinstance(v, bytes) else v)
            except (ValueError, TypeError, AttributeError):
                return default

        def _to_int(v, default):
            try:
                return int(v.decode() if isinstance(v, bytes) else v)
            except (ValueError, TypeError, AttributeError):
                return default

        alpha = _to_float(raw.get(b"alpha") or raw.get("alpha"), _PRIOR_ALPHA)
        beta = _to_float(raw.get(b"beta") or raw.get("beta"), _PRIOR_BETA)
        pulls = _to_int(raw.get(b"pulls") or raw.get("pulls"), 0)
        successes = _to_int(raw.get(b"successes") or raw.get("successes"), 0)
        return ArmStats(alpha, beta, pulls, successes)
    except Exception as exc:
        log.debug("bandit: read failed: %s", exc)
        return ArmStats(_PRIOR_ALPHA, _PRIOR_BETA, 0, 0)


def _write_arm(
    shop_domain: str, ctx: dict[str, str], variant: str, arm: ArmStats
) -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("contextual_bandit.write_arm")
        return
    try:
        key = _arm_key(shop_domain, ctx, variant)
        rc.hset(key, mapping={
            "alpha": arm.alpha,
            "beta": arm.beta,
            "pulls": arm.pulls,
            "successes": arm.successes,
            "last_updated": int(time.time()),
        })
        rc.expire(key, _TTL_SECONDS)
    except Exception as exc:
        log.debug("bandit: write failed: %s", exc)


# ---------------------------------------------------------------------------
# Selection (Thompson Sampling)
# ---------------------------------------------------------------------------

def select_variant(
    shop_domain: str,
    context: dict[str, str],
    variants: list[str],
    *,
    strategy: str = "thompson",
    rng: random.Random | None = None,
) -> str:
    """Select the best variant for this context. Defaults to Thompson
    sampling. Returns one of `variants` (never raises)."""
    if not variants:
        raise ValueError("no variants provided")
    if len(variants) == 1:
        return variants[0]

    rng = rng or random

    if strategy == "uniform":
        return rng.choice(variants)

    # Thompson Sampling — draw from each arm's Beta, pick max
    best_variant = variants[0]
    best_score = -1.0
    for v in variants:
        arm = _read_arm(shop_domain, context, v)
        # Beta(α, β) sample via random module — good enough without numpy
        score = rng.betavariate(arm.alpha, arm.beta)
        if score > best_score:
            best_score = score
            best_variant = v
    return best_variant


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------

def record_outcome(
    shop_domain: str,
    context: dict[str, str],
    variant: str,
    *,
    success: bool,
) -> None:
    """Update the arm's posterior with one observation."""
    arm = _read_arm(shop_domain, context, variant)
    if success:
        arm.alpha += 1
        arm.successes += 1
    else:
        arm.beta += 1
    arm.pulls += 1
    _write_arm(shop_domain, context, variant, arm)


def get_arm_stats(
    shop_domain: str, context: dict[str, str], variant: str
) -> dict:
    arm = _read_arm(shop_domain, context, variant)
    return {
        "alpha": round(arm.alpha, 2),
        "beta": round(arm.beta, 2),
        "pulls": arm.pulls,
        "successes": arm.successes,
        "est_success_rate": round(arm.mean, 3),
        "confidence": round(arm.confidence, 3),
    }


# ---------------------------------------------------------------------------
# Batch replay — rebuild posteriors from nudge_events history
# ---------------------------------------------------------------------------

def refresh_from_events(
    db: Session,
    shop_domain: str,
    window_hours: int = 48,
) -> dict:
    """Replay recent nudge impressions + outcomes to update posteriors.

    Strategy:
      - For each nudge_event with event_type='nudge_impression' in the
        window, determine if the visitor made a purchase within 48h of
        the impression.
      - Success = purchase within 48h; Failure = no purchase within 48h.
      - Context is derived from the event's device + source_type + tod
        (category falls back to 'general' — we'd need to join product
        metadata for richer context).
    """
    report = {"shop": shop_domain, "scanned": 0, "updated": 0}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(hours=window_hours)

    try:
        rows = db.execute(
            sql_text(
                """
                SELECT ne.id, ne.visitor_id, ne.created_at,
                       ne.event_meta->>'copy_variant' AS variant_name,
                       ne.event_type, ne.nudge_id, ne.event_meta
                FROM nudge_events ne
                WHERE ne.shop_domain = :shop
                  AND ne.created_at >= :since
                  AND ne.event_type = 'nudge_impression'
                ORDER BY ne.created_at ASC
                LIMIT 5000
                """
            ),
            {"shop": shop_domain, "since": since},
        ).fetchall()
    except Exception as exc:
        log.warning("bandit: event replay query failed: %s", exc)
        return report

    from app.core.database import rollback_quiet
    for row in rows:
        ne_id, visitor_id, ne_ts, variant, _etype, _nudge_id, meta = row
        report["scanned"] += 1
        if not variant or not visitor_id:
            continue

        # Was there a purchase in the 48h window after impression?
        try:
            purchase = db.execute(
                sql_text(
                    """
                    SELECT 1 FROM visitor_purchase_sessions
                    WHERE shop_domain = :shop
                      AND visitor_id = :vid
                      AND confirmed_at BETWEEN :lo AND :hi
                    LIMIT 1
                    """
                ),
                {
                    "shop": shop_domain,
                    "vid": visitor_id,
                    "lo": ne_ts,
                    "hi": ne_ts + timedelta(hours=48),
                },
            ).fetchone()
            success = purchase is not None
        except Exception:
            # write_no_rollback class (2026-05-19b): read-only purchase
            # probe, but a conn-death / PendingRollbackError mid-loop
            # would poison the shared session for every remaining row +
            # the caller. Un-poison before continuing (lower-prob
            # conn-death class — the sentry #239 trigger shape).
            rollback_quiet(db)
            continue

        # Derive context from event_meta JSON + event timestamp
        try:
            meta_obj = {} if meta is None else (
                meta if isinstance(meta, dict) else json.loads(meta)
            )
        except Exception:
            meta_obj = {}
        ctx = make_context(
            device=meta_obj.get("device"),
            source=meta_obj.get("source_type"),
            category=meta_obj.get("category"),
            ts=ne_ts,
        )
        record_outcome(shop_domain, ctx, variant, success=success)
        report["updated"] += 1

    return report
