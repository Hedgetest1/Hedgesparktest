"""
feature_usage.py — Shipped-vs-used telemetry.

The honest foundation against "35 features shipped per day, 0 validated".
Every feature we care about emits a usage event when a real merchant
actually interacts with it. The ops dashboard then shows a **shipped
vs used** ratio — the only metric that distinguishes product from
portfolio theatre.

Storage
-------
Two Redis counters per feature:
    hs:fusage:{feature}:shipped   → static (set at registration time)
    hs:fusage:{feature}:uses      → incremented on each call
    hs:fusage:{feature}:shops     → set of unique shops that used it

API
---
    track(feature, shop) -> None                  # fast path, never raises
    stats(feature) -> dict                        # inspection
    all_stats() -> list[dict]                     # ops overview
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("feature_usage")

_PREFIX = "hs:fusage"
_TTL = 45 * 24 * 3600  # 45 days — long enough to spot dormant features


@dataclass(frozen=True)
class FeatureRegistration:
    name: str
    description: str
    shipped_at: str  # ISO date


# Central registry of features we want to measure. Every entry here
# represents a promise: we shipped it AND we're willing to have the data
# show whether merchants use it.
REGISTRY: list[FeatureRegistration] = [
    FeatureRegistration("night_shift_agent", "Phase Ω⁵ nightly AI report", "2026-04-13"),
    FeatureRegistration("community_marketplace", "Template marketplace page", "2026-04-13"),
    FeatureRegistration("public_roi_counter", "Landing network ROI counter", "2026-04-13"),
    FeatureRegistration("causal_why_engine", "Causal explainer dashboard card", "2026-04-13"),
    FeatureRegistration("anomaly_fusion", "Cross-signal anomaly radar", "2026-04-13"),
    FeatureRegistration("vertical_benchmarks", "Vertical-aware peer benchmarks", "2026-04-13"),
    FeatureRegistration("revenue_autopsy", "R-series revenue autopsy", "2026-04-12"),
    FeatureRegistration("abandoned_intent", "R-series abandoned intent", "2026-04-12"),
    FeatureRegistration("price_sensitivity", "R-series price sensitivity", "2026-04-12"),
    FeatureRegistration("customer_churn", "R-series customer churn", "2026-04-12"),
    FeatureRegistration("mta_compare", "Multi-touch attribution comparison", "2026-04-12"),
    FeatureRegistration("cac_ltv", "CAC:LTV unit economics", "2026-04-12"),
    FeatureRegistration("margin_guard", "Margin guard + COGS gate", "2026-04-12"),
    FeatureRegistration("rule_builder", "Custom rule builder", "2026-04-12"),
    FeatureRegistration("agency_mode", "White-label agency dashboard", "2026-04-13"),
    FeatureRegistration("merchant_groups", "Multi-store groups", "2026-04-13"),
    # Phase Ω⁷ — the Unreachable Three
    FeatureRegistration("anomaly_replay", "Anomaly event-window replay", "2026-04-13"),
    FeatureRegistration("counterfactual_explorer", "Counterfactual revenue scenarios", "2026-04-13"),
    FeatureRegistration("competitor_playbook", "Peer playbook for signal action outcomes", "2026-04-13"),
]


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("feature_usage: redis client init failed: %s", exc)
        return None


def track(feature: str, shop: str | None = None) -> None:
    """Fire-and-forget: record one use of `feature`."""
    rc = _redis()
    if rc is None:
        record_silent_return("feature_usage.track")
        return
    try:
        uses_key = f"{_PREFIX}:{feature}:uses"
        shops_key = f"{_PREFIX}:{feature}:shops"
        ts_key = f"{_PREFIX}:{feature}:last_used_ts"
        pipe = rc.pipeline()
        pipe.incr(uses_key)
        pipe.expire(uses_key, _TTL)
        if shop:
            pipe.sadd(shops_key, shop)
            pipe.expire(shops_key, _TTL)
        pipe.set(ts_key, str(int(time.time())), ex=_TTL)
        pipe.execute()
    except Exception as exc:
        log.debug("feature_usage: track failed: %s", exc)


def stats(feature: str) -> dict:
    """Return per-feature counters + metadata."""
    reg = next((r for r in REGISTRY if r.name == feature), None)
    rc = _redis()
    uses = 0
    unique_shops = 0
    last_used_ts: int | None = None
    if rc is not None:
        try:
            uses_raw = rc.get(f"{_PREFIX}:{feature}:uses")
            uses = int(uses_raw) if uses_raw else 0
            shops_count = rc.scard(f"{_PREFIX}:{feature}:shops")
            unique_shops = int(shops_count or 0)
            ts_raw = rc.get(f"{_PREFIX}:{feature}:last_used_ts")
            if ts_raw:
                if isinstance(ts_raw, bytes):
                    ts_raw = ts_raw.decode()
                last_used_ts = int(ts_raw)
        except Exception as exc:
            log.warning("feature_usage: stats read failed for %s: %s", feature, exc)
    return {
        "feature": feature,
        "description": reg.description if reg else None,
        "shipped_at": reg.shipped_at if reg else None,
        "uses_45d": uses,
        "unique_shops_45d": unique_shops,
        "last_used_ts": last_used_ts,
        "dormant": uses == 0,
    }


def all_stats() -> list[dict]:
    return [stats(reg.name) for reg in REGISTRY]


def dormant_features() -> list[dict]:
    return [s for s in all_stats() if s["dormant"]]
