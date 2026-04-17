"""
benchmarks.py — Industry benchmark service (the Varos killer).

Gives every merchant a single-line answer to "am I underperforming peers?".
The pitch: "Your CVR is p37 vs peers in your revenue band — if you moved
to p75 you'd recover €1,240/month".

Design principles
-----------------
* Zero-schema: computed on-demand, cached in Redis 6h. No migration.
* Privacy: minimum N=10 peers per revenue band. Below N, return an
  explicit insufficient_peers response rather than fake numbers.
* Deterministic: no LLM, no randomness, pure SQL aggregation.
* Loss-framed: every metric includes a recovery_potential_eur hint.
* Competitive moat: the peer pool is OUR merchant base — a copycat
  architecture has zero peers and cannot produce these numbers.
* Self-healing integration: ops_alerts on refresh failure, data_integrity
  probe watches for snapshot freshness, project_brain domain 'benchmarks'.

Metrics benchmarked in v1
-------------------------
1. monthly_revenue    — total € revenue in last 30 days
2. aov                — average order value
3. orders_per_day     — order frequency (volume signal)
4. revenue_growth_30d — last 30d vs previous 30d (trend)

For each metric the benchmark returns p25/p50/p75/p90 + the merchant's
percentile rank + a loss-framed recovery estimate.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("benchmarks")

# ---------------------------------------------------------------------------
# Revenue bands — k-anonymity buckets. Merchants only compare to peers in
# the SAME band so a €2k/mo store is never measured against a €500k/mo store.
# ---------------------------------------------------------------------------

_REVENUE_BANDS: list[tuple[str, float, float]] = [
    ("micro",  0.0,     3_000.0),
    ("small",  3_000.0, 15_000.0),
    ("mid",    15_000.0, 50_000.0),
    ("large",  50_000.0, 150_000.0),
    ("xlarge", 150_000.0, float("inf")),
]

_MIN_PEERS_PER_BAND = 10   # k-anonymity floor — below this, return insufficient_peers
_CACHE_TTL_SECONDS = 6 * 3600
_CACHE_KEY_PREFIX = "hs:benchmarks:v1"


def _classify_band(monthly_revenue: float) -> str:
    for name, low, high in _REVENUE_BANDS:
        if low <= monthly_revenue < high:
            return name
    return "unknown"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Return shapes
# ---------------------------------------------------------------------------


@dataclass
class MetricPercentile:
    """Percentile distribution for one metric in one revenue band."""
    p25: float
    p50: float
    p75: float
    p90: float
    sample_size: int


@dataclass
class MerchantMetricReport:
    """A merchant's standing on one metric within their peer band."""
    metric: str
    value: float
    band: str
    peer_count: int
    percentile_rank: float  # 0-100, higher = better
    p50: float
    p75: float
    p90: float
    # Loss framing: if the merchant moved from current → p75, how much
    # more revenue would they recover per month? 0 if already above p75.
    recovery_to_p75_eur: float
    status: str  # "below_median" | "above_median" | "top_quartile" | "top_decile" | "insufficient_peers"
    narrative: str  # human-readable one-liner


# ---------------------------------------------------------------------------
# Compute the raw aggregate across all eligible merchants
# ---------------------------------------------------------------------------


def _gather_merchant_metrics(db: Session, lookback_days: int = 30) -> dict[str, dict]:
    """
    For every active merchant with data, compute the four benchmark
    metrics from shop_orders. Returns {shop_domain: {metric: value, ...}}.

    One pass through shop_orders → aggregates per shop. Cheap at SMB scale.
    """
    now = _now()
    cutoff_recent = now - timedelta(days=lookback_days)
    cutoff_prior = now - timedelta(days=lookback_days * 2)

    rows = db.execute(text("""
        SELECT
            o.shop_domain,
            SUM(CASE WHEN o.created_at >= :recent_cut THEN o.total_price ELSE 0 END) AS revenue_recent,
            SUM(CASE WHEN o.created_at >= :prior_cut AND o.created_at < :recent_cut THEN o.total_price ELSE 0 END) AS revenue_prior,
            COUNT(CASE WHEN o.created_at >= :recent_cut THEN 1 END) AS orders_recent
        FROM shop_orders o
        WHERE o.created_at >= :prior_cut
        GROUP BY o.shop_domain
        HAVING COUNT(CASE WHEN o.created_at >= :recent_cut THEN 1 END) >= 5
    """), {
        "recent_cut": cutoff_recent,
        "prior_cut": cutoff_prior,
    }).fetchall()

    per_shop: dict[str, dict] = {}
    for r in rows:
        shop = r[0]
        revenue_recent = float(r[1] or 0)
        revenue_prior = float(r[2] or 0)
        orders_recent = int(r[3] or 0)
        if orders_recent == 0:
            continue
        aov = revenue_recent / orders_recent
        orders_per_day = orders_recent / lookback_days
        # Growth: safe when prior=0 → skip trend, keep 0
        growth = ((revenue_recent - revenue_prior) / revenue_prior * 100) if revenue_prior > 0 else 0.0

        per_shop[shop] = {
            "monthly_revenue": revenue_recent,
            "aov": aov,
            "orders_per_day": orders_per_day,
            "revenue_growth_30d_pct": growth,
        }

    return per_shop


def _percentile(values: list[float], pct: float) -> float:
    """Compute a percentile from a sorted list. Pure Python, no numpy."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _compute_band_percentiles(
    per_shop: dict[str, dict],
) -> dict[str, dict[str, MetricPercentile]]:
    """
    Split merchants into revenue bands, then compute p25/p50/p75/p90 for
    each (band, metric). Returns {band: {metric: MetricPercentile}}.
    """
    by_band: dict[str, list[dict]] = {}
    for shop, m in per_shop.items():
        band = _classify_band(m["monthly_revenue"])
        by_band.setdefault(band, []).append(m)

    out: dict[str, dict[str, MetricPercentile]] = {}
    for band, metrics_list in by_band.items():
        if len(metrics_list) < _MIN_PEERS_PER_BAND:
            continue
        out[band] = {}
        for metric in ("monthly_revenue", "aov", "orders_per_day", "revenue_growth_30d_pct"):
            values = [m[metric] for m in metrics_list if m.get(metric) is not None]
            if not values:
                continue
            out[band][metric] = MetricPercentile(
                p25=_percentile(values, 25),
                p50=_percentile(values, 50),
                p75=_percentile(values, 75),
                p90=_percentile(values, 90),
                sample_size=len(values),
            )
    return out


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Return the percentile rank (0-100) of value within the sorted list."""
    if not sorted_values:
        return 50.0
    below = sum(1 for v in sorted_values if v < value)
    equal = sum(1 for v in sorted_values if v == value)
    return round((below + equal / 2) / len(sorted_values) * 100, 1)


# ---------------------------------------------------------------------------
# Loss framing — the killer copy that makes this feature pitch-ready
# ---------------------------------------------------------------------------

_STATUS_NARRATIVES = {
    "top_decile": "🏆 You're in the top 10% of peers in your band — keep defending this lead.",
    "top_quartile": "📈 You're in the top 25% of peers — one more push and you hit the top decile.",
    "above_median": "👍 You're above the median but below the top quartile — recoverable upside exists.",
    "below_median": "🔻 You're below the median. Moving to p50 would materially recover lost revenue.",
    "insufficient_peers": "Not enough peer data yet to benchmark this metric reliably.",
}


def _classify_status(rank: float) -> str:
    if rank >= 90:
        return "top_decile"
    if rank >= 75:
        return "top_quartile"
    if rank >= 50:
        return "above_median"
    return "below_median"


def _recovery_estimate_eur(
    metric: str,
    current_value: float,
    p75: float,
    per_shop_m: dict,
) -> float:
    """
    Estimate how much monthly revenue the merchant would recover by
    moving from their current value on this metric to the p75 peer level.

    Each metric has a different recovery semantics:
      - monthly_revenue: trivial — gap is itself the recovery
      - aov: gap * orders_per_month
      - orders_per_day: gap_per_day * 30 * current AOV
      - revenue_growth_30d_pct: projected monthly delta
    """
    if current_value >= p75:
        return 0.0
    gap = p75 - current_value
    if metric == "monthly_revenue":
        return round(gap, 2)
    if metric == "aov":
        orders_month = per_shop_m.get("orders_per_day", 0) * 30
        return round(gap * orders_month, 2)
    if metric == "orders_per_day":
        aov = per_shop_m.get("aov", 0)
        return round(gap * 30 * aov, 2)
    if metric == "revenue_growth_30d_pct":
        # Convert percentage gap into € on current monthly revenue
        return round(gap / 100.0 * per_shop_m.get("monthly_revenue", 0), 2)
    return 0.0


# ---------------------------------------------------------------------------
# Public API — per-merchant benchmark report
# ---------------------------------------------------------------------------


def get_merchant_benchmark_report(db: Session, shop_domain: str) -> dict:
    """
    Return the full benchmark report for a merchant. Cached in Redis
    for 6 hours per shop. Lost on Redis flush but trivially recomputable.

    Shape:
    {
        "shop_domain": str,
        "band": "small",
        "peer_count": 47,
        "metrics": {
            "monthly_revenue": {...MerchantMetricReport fields...},
            "aov": {...},
            "orders_per_day": {...},
            "revenue_growth_30d_pct": {...},
        },
        "total_recovery_potential_eur": 1843.50,
        "generated_at": "2026-04-11T12:00:00",
    }
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception as exc:
        log.warning("benchmarks: get_merchant_benchmark_report failed: %s", exc)
        pass  # fall through to compute

    try:
        per_shop = _gather_merchant_metrics(db)
    except Exception as exc:
        log.warning("benchmarks: gather failed: %s", exc)
        # Emit an ops_alert so the self-healing pipeline catches repeated failures
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="benchmarks",
                alert_type="benchmark_compute_failed",
                summary=f"Benchmark aggregation failed: {type(exc).__name__}",
                detail={"error": str(exc)[:500]},
            )
        except Exception as exc:
            log.warning("benchmarks: get_merchant_benchmark_report failed: %s", exc)
        return {
            "shop_domain": shop_domain,
            "error": "compute_failed",
            "metrics": {},
        }

    my_metrics = per_shop.get(shop_domain)
    if not my_metrics:
        return {
            "shop_domain": shop_domain,
            "band": None,
            "peer_count": 0,
            "metrics": {},
            "total_recovery_potential_eur": 0.0,
            "generated_at": _now().isoformat(),
            "note": "insufficient_shop_data: <5 orders in last 30 days",
        }

    band = _classify_band(my_metrics["monthly_revenue"])
    band_percentiles = _compute_band_percentiles(per_shop)
    my_band_dist = band_percentiles.get(band)

    if not my_band_dist:
        return {
            "shop_domain": shop_domain,
            "band": band,
            "peer_count": sum(
                1 for s, m in per_shop.items()
                if _classify_band(m["monthly_revenue"]) == band
            ),
            "metrics": {},
            "total_recovery_potential_eur": 0.0,
            "generated_at": _now().isoformat(),
            "note": f"insufficient_peers: need >={_MIN_PEERS_PER_BAND} peers in band '{band}'",
        }

    # For percentile_rank I need sorted values per (band, metric)
    peers_in_band = [
        m for s, m in per_shop.items()
        if _classify_band(m["monthly_revenue"]) == band
    ]
    peer_count = len(peers_in_band)

    metrics_out = {}
    total_recovery = 0.0

    for metric, pct in my_band_dist.items():
        value = my_metrics.get(metric, 0)
        values_sorted = sorted([p[metric] for p in peers_in_band if p.get(metric) is not None])
        rank = _percentile_rank(value, values_sorted)
        status = _classify_status(rank)
        recovery = _recovery_estimate_eur(metric, value, pct.p75, my_metrics)
        if recovery > 0:
            total_recovery += recovery

        narrative = _STATUS_NARRATIVES[status]
        metrics_out[metric] = {
            "value": round(value, 2),
            "band": band,
            "peer_count": peer_count,
            "percentile_rank": rank,
            "p25": round(pct.p25, 2),
            "p50": round(pct.p50, 2),
            "p75": round(pct.p75, 2),
            "p90": round(pct.p90, 2),
            "recovery_to_p75_eur": recovery,
            "status": status,
            "narrative": narrative,
        }

    # Shop's native currency for money-field rendering. Failures in
    # the lookup fall back to USD — the response MUST always carry a
    # valid ISO code so the dashboard never has to guess.
    try:
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"
    result = {
        "shop_domain": shop_domain,
        "band": band,
        "peer_count": peer_count,
        "metrics": metrics_out,
        "total_recovery_potential_eur": round(total_recovery, 2),
        "currency": currency,
        "generated_at": _now().isoformat(),
    }

    # Cache it
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("benchmarks: get_merchant_benchmark_report failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Extended benchmarks: CVR + product-level (Tier 2 moat)
# ---------------------------------------------------------------------------

def get_extended_benchmark_report(db: Session, shop_domain: str) -> dict:
    """
    Extended benchmarks beyond revenue metrics:
      - CVR (conversion rate vs peers)
      - Product diversity (how many products drive 80% of revenue)
      - Return visitor ratio (engagement depth vs peers)

    Falls back gracefully if data is insufficient.
    """
    base = get_merchant_benchmark_report(db, shop_domain)
    if base.get("error") or not base.get("band"):
        return base

    band = base["band"]
    now = _now()
    cutoff = now - timedelta(days=30)

    # Compute CVR for all merchants in the band
    try:
        cvr_rows = db.execute(text("""
            WITH shop_visitors AS (
                SELECT shop_domain,
                       COUNT(DISTINCT visitor_id) as unique_visitors
                FROM events
                WHERE to_timestamp(timestamp/1000) >= :cutoff
                  AND event_type = 'product_view'
                GROUP BY shop_domain
                HAVING COUNT(DISTINCT visitor_id) >= 20
            ),
            shop_purchases AS (
                SELECT shop_domain, COUNT(*) as purchase_count
                FROM shop_orders
                WHERE created_at >= :cutoff
                GROUP BY shop_domain
            )
            SELECT sv.shop_domain,
                   sp.purchase_count::float / sv.unique_visitors * 100 as cvr
            FROM shop_visitors sv
            JOIN shop_purchases sp ON sp.shop_domain = sv.shop_domain
        """), {"cutoff": cutoff}).fetchall()

        if cvr_rows:
            cvr_by_shop = {r[0]: r[1] for r in cvr_rows}
            my_cvr = cvr_by_shop.get(shop_domain)

            # Filter to same band peers
            per_shop = _gather_merchant_metrics(db)
            peer_cvrs = sorted([
                cvr_by_shop[s]
                for s in per_shop
                if _classify_band(per_shop[s]["monthly_revenue"]) == band
                and s in cvr_by_shop
            ])

            if my_cvr is not None and len(peer_cvrs) >= _MIN_PEERS_PER_BAND:
                rank = _percentile_rank(my_cvr, peer_cvrs)
                status = _classify_status(rank)
                base["metrics"]["cvr"] = {
                    "value": round(my_cvr, 2),
                    "band": band,
                    "peer_count": len(peer_cvrs),
                    "percentile_rank": rank,
                    "p25": round(_percentile(peer_cvrs, 25), 2),
                    "p50": round(_percentile(peer_cvrs, 50), 2),
                    "p75": round(_percentile(peer_cvrs, 75), 2),
                    "p90": round(_percentile(peer_cvrs, 90), 2),
                    "recovery_to_p75_eur": 0.0,
                    "status": status,
                    "narrative": _STATUS_NARRATIVES.get(status, ""),
                    "unit": "percent",
                }
    except Exception as exc:
        log.warning("extended benchmarks CVR failed: %s", exc)

    # Product concentration (how many products drive 80% of revenue)
    try:
        items_rows = db.execute(text("""
            SELECT line_items FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :cutoff
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchall()

        product_rev: dict[str, float] = {}
        for r in items_rows:
            items = r[0] or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("product_handle") or "")
                price = float(item.get("price") or 0) * int(item.get("quantity") or 1)
                if title:
                    product_rev[title] = product_rev.get(title, 0) + price

        if product_rev:
            total = sum(product_rev.values())
            sorted_products = sorted(product_rev.values(), reverse=True)
            cumulative = 0
            count_80 = 0
            for v in sorted_products:
                cumulative += v
                count_80 += 1
                if cumulative >= total * 0.8:
                    break

            base["product_concentration"] = {
                "total_products": len(product_rev),
                "products_for_80pct_revenue": count_80,
                "concentration_ratio": round(count_80 / len(product_rev) * 100, 1) if product_rev else 0,
                "narrative": (
                    f"{count_80} products drive 80% of revenue "
                    f"(out of {len(product_rev)} total)."
                ),
            }
    except Exception as exc:
        log.warning("benchmarks: get_extended_benchmark_report failed: %s", exc)

    return base
