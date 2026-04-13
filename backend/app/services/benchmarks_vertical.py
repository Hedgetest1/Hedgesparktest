"""
benchmarks_vertical.py — Phase Ω moat #1.

Vertical-aware benchmark engine v2. Sits on top of `benchmarks.py` and
adds the vertical dimension: peers are drawn from (vertical, revenue_band)
buckets instead of revenue_band alone.

Why this is a moat
------------------
A €15k/mo beauty brand benchmarked against €15k/mo electronics shops
gets noise. Benchmarked against €15k/mo *beauty* shops, the comparison
is actionable: a beauty CVR of 3% vs the beauty p75 of 4.2% means
"recover X€ by closing this specific gap".

Once we have N>=8 peers per (vertical, band) bucket — a function of
the merchant base — competitors **cannot** replicate this without
matching our peer pool size in every bucket. Network effect = moat.

K-anonymity
-----------
* Minimum N=8 peers per (vertical, band). Below that, we fall back to
  vertical-only (drop the band) and finally to the band-only v1.
* Vertical = "other" merchants are *never* mixed into a named vertical.
* Shop_domain is hashed before any cache write.

Cache
-----
6h TTL, Redis. Cache key includes vertical + band so a re-classified
shop gets a fresh report.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.services.benchmarks import (
    _classify_band,
    _classify_status,
    _gather_merchant_metrics,
    _percentile,
    _percentile_rank,
    _recovery_estimate_eur,
    _STATUS_NARRATIVES,
    _MIN_PEERS_PER_BAND,
)
from app.services.vertical_classifier import classify_shop, get_vertical
from app.services.vertical_prompt_pack import get_profile

log = logging.getLogger("benchmarks_vertical")

_MIN_PEERS_PER_VERTICAL_BAND = 8  # k-anonymity floor
_CACHE_TTL_SECONDS = 6 * 3600
_CACHE_KEY_PREFIX = "hs:bench_v2:v1"
_METRICS = ("monthly_revenue", "aov", "orders_per_day", "revenue_growth_30d_pct")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _classify_all_shops(db: Session, per_shop: dict[str, dict]) -> dict[str, str]:
    """Bulk vertical classification. One pass, cached per shop in Redis."""
    out: dict[str, str] = {}
    for shop in per_shop.keys():
        try:
            out[shop] = get_vertical(db, shop)
        except Exception:
            out[shop] = "other"
    return out


def _bucket_key(vertical: str, band: str) -> str:
    return f"{vertical}::{band}"


def get_vertical_benchmark_report(db: Session, shop_domain: str) -> dict:
    """
    Vertical-aware benchmark for a single merchant.

    Resolution ladder:
      1. (vertical, band) bucket if peers >= 8
      2. (vertical, *) bucket if peers >= 8
      3. (*, band) bucket — falls back to v1 benchmark
      4. note: insufficient_peers
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        pass

    try:
        per_shop = _gather_merchant_metrics(db)
    except Exception as exc:
        log.warning("benchmarks_vertical: gather failed: %s", exc)
        return {"shop_domain": shop_domain, "error": "compute_failed", "metrics": {}}

    my_metrics = per_shop.get(shop_domain)
    if not my_metrics:
        return {
            "shop_domain": shop_domain,
            "vertical": None,
            "band": None,
            "peer_count": 0,
            "metrics": {},
            "total_recovery_potential_eur": 0.0,
            "generated_at": _now_iso(),
            "note": "insufficient_shop_data",
        }

    vertical = get_vertical(db, shop_domain)
    band = _classify_band(my_metrics["monthly_revenue"])
    profile = get_profile(vertical)

    classifications = _classify_all_shops(db, per_shop)

    # Bucket the peers
    bucket_vb: list[dict] = []  # (vertical, band)
    bucket_v: list[dict] = []   # (vertical, *)
    bucket_b: list[dict] = []   # (*, band) — for fallback

    for s, m in per_shop.items():
        if s == shop_domain:
            continue
        s_vert = classifications.get(s, "other")
        s_band = _classify_band(m["monthly_revenue"])
        if s_vert == vertical:
            bucket_v.append(m)
            if s_band == band:
                bucket_vb.append(m)
        if s_band == band:
            bucket_b.append(m)

    # Resolve which bucket to use
    if len(bucket_vb) >= _MIN_PEERS_PER_VERTICAL_BAND:
        peers = bucket_vb
        scope = "vertical_band"
    elif len(bucket_v) >= _MIN_PEERS_PER_VERTICAL_BAND:
        peers = bucket_v
        scope = "vertical_only"
    elif len(bucket_b) >= _MIN_PEERS_PER_BAND:
        peers = bucket_b
        scope = "band_only"
    else:
        return {
            "shop_domain": shop_domain,
            "vertical": vertical,
            "vertical_display": profile.display_name,
            "band": band,
            "peer_count": len(bucket_vb),
            "metrics": {},
            "total_recovery_potential_eur": 0.0,
            "generated_at": _now_iso(),
            "scope": "insufficient",
            "note": (
                f"insufficient_peers: need >={_MIN_PEERS_PER_VERTICAL_BAND} "
                f"in vertical={vertical} band={band}; have {len(bucket_vb)}"
            ),
            "fallback_baselines": {
                "cvr_baseline_pct": profile.cvr_baseline_pct,
                "aov_baseline_eur": profile.aov_baseline_eur,
            },
        }

    metrics_out: dict[str, dict] = {}
    total_recovery = 0.0
    for metric in _METRICS:
        values = [p[metric] for p in peers if p.get(metric) is not None]
        if not values or len(values) < 4:
            continue
        my_val = my_metrics.get(metric, 0)
        sorted_v = sorted(values)
        rank = _percentile_rank(my_val, sorted_v)
        status = _classify_status(rank)
        p25 = _percentile(values, 25)
        p50 = _percentile(values, 50)
        p75 = _percentile(values, 75)
        p90 = _percentile(values, 90)
        recovery = _recovery_estimate_eur(metric, my_val, p75, my_metrics)
        if recovery > 0:
            total_recovery += recovery
        metrics_out[metric] = {
            "value": round(my_val, 2),
            "vertical": vertical,
            "band": band,
            "scope": scope,
            "peer_count": len(values),
            "percentile_rank": rank,
            "p25": round(p25, 2),
            "p50": round(p50, 2),
            "p75": round(p75, 2),
            "p90": round(p90, 2),
            "recovery_to_p75_eur": recovery,
            "status": status,
            "narrative": _STATUS_NARRATIVES[status],
        }

    result = {
        "shop_domain": shop_domain,
        "vertical": vertical,
        "vertical_display": profile.display_name,
        "band": band,
        "scope": scope,
        "peer_count": len(peers),
        "metrics": metrics_out,
        "total_recovery_potential_eur": round(total_recovery, 2),
        "generated_at": _now_iso(),
        "note": (
            "Comparison against same-vertical peers in your revenue band."
            if scope == "vertical_band"
            else "Comparison against same-vertical peers across all revenue bands."
            if scope == "vertical_only"
            else "Comparison against same revenue band (all verticals) — vertical pool too small yet."
        ),
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
    except Exception:
        pass

    return result


def get_vertical_pool_stats(db: Session) -> dict:
    """
    Operator-facing view of the network-effect moat. Returns the count
    of peers per (vertical, band) bucket — the higher the numbers, the
    deeper the moat. Surfaced in /ops/benchmarks/pool.
    """
    try:
        per_shop = _gather_merchant_metrics(db)
    except Exception:
        return {"error": "compute_failed", "buckets": {}}

    classifications = _classify_all_shops(db, per_shop)
    buckets: dict[str, int] = {}
    for s, m in per_shop.items():
        v = classifications.get(s, "other")
        b = _classify_band(m["monthly_revenue"])
        buckets[_bucket_key(v, b)] = buckets.get(_bucket_key(v, b), 0) + 1

    return {
        "total_merchants": len(per_shop),
        "buckets": buckets,
        "buckets_above_k": sum(1 for c in buckets.values() if c >= _MIN_PEERS_PER_VERTICAL_BAND),
        "k_floor": _MIN_PEERS_PER_VERTICAL_BAND,
        "generated_at": _now_iso(),
    }
