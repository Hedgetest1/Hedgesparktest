"""
revenue_genome.py — The Revenue DNA of each merchant.

THE unreachable feature. Synthesizes ALL behavioral signals into a
unified merchant profile:

  1. TRAFFIC GENOME — where visitors come from, when they visit, device mix
  2. CONVERSION GENOME — funnel strengths/weaknesses, cart behavior
  3. PRODUCT GENOME — catalog health, hero products, concentration risk
  4. CUSTOMER GENOME — repeat rate, LTV trajectory, cohort retention
  5. INTERVENTION GENOME — nudge effectiveness, recommendation response
  6. RISK GENOME — revenue-at-risk profile, churn signals, seasonal patterns

Each gene has a score (0-100) and a prescriptive recommendation.

Why unreachable:
  - Google Analytics: no purchase data
  - Triple Whale: no behavioral tracking
  - Lifetimely: no intent signals
  - Polar Analytics: no intervention capability
  - NOBODY has holdout measurement to verify recommendations

Cost: Zero LLM. Pure aggregation. Cached 6h.

Architecture (refactor 2026-05-12 — A3 close):
  Each gene cluster is a pure function (db, shop, currency, *, now) -> dict.
  The composer compute_revenue_genome iterates _CLUSTERS in order,
  swallows per-cluster exceptions into "insufficient_data" stubs, then
  aggregates the overall score + archetype + priority actions.
  Adding a cluster: write a _cluster_X function + register in _CLUSTERS.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("revenue_genome")

_CACHE_TTL = 6 * 3600
_CACHE_PREFIX = "hs:genome:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _score(value: float, low: float, high: float) -> int:
    """Scale a value to 0-100 between low (bad) and high (good)."""
    if high <= low:
        return 50
    return max(0, min(100, int((value - low) / (high - low) * 100)))


def _gene(name: str, score: int, value, unit: str, insight: str, action: str) -> dict:
    return {
        "name": name,
        "score": score,
        "value": value,
        "unit": unit,
        "status": "strong" if score >= 70 else "moderate" if score >= 40 else "weak",
        "insight": insight,
        "action": action,
    }


# ---------------------------------------------------------------------------
# Cache helpers — single source of truth for Redis read/write.
# ---------------------------------------------------------------------------

def _cache_key(shop_domain: str) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"


def _cache_get(shop_domain: str) -> dict | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("revenue_genome.cache_read")
            return None
        cached = rc.get(_cache_key(shop_domain))
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("revenue_genome: redis cache read failed: %s", exc)
    return None


def _cache_set(shop_domain: str, payload: dict) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("revenue_genome.cache_write")
            return
        rc.setex(_cache_key(shop_domain), _CACHE_TTL, json.dumps(payload, default=str))
    except Exception as exc:
        log.warning("revenue_genome: redis cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# Gene clusters — each is a pure (db, shop, currency, *, now) -> dict.
# Cluster body always returns the dict shape {cluster, genes, [error]}.
# Exceptions propagate; the composer wraps them into a default stub.
# ---------------------------------------------------------------------------

_Cluster = Callable[[Session, str, str | None], dict]


def _cluster_traffic(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    # events.source_type is the real column. Values are coarse buckets
    # produced by tracker classification: paid_search / paid_social /
    # organic / direct / email / ... — anything starting with 'paid'
    # is classified paid to preserve the original intent.
    cutoff_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    row = db.execute(text("""
        SELECT
            COUNT(*) as total_events,
            COUNT(DISTINCT visitor_id) as unique_visitors,
            COUNT(*) FILTER (WHERE device_type = 'mobile') as mobile,
            COUNT(*) FILTER (WHERE device_type = 'desktop') as desktop,
            COUNT(*) FILTER (WHERE source_type LIKE 'paid%%') as paid,
            COUNT(*) FILTER (WHERE source_type = 'organic') as organic,
            COUNT(*) FILTER (WHERE source_type = 'direct') as direct
        FROM events
        WHERE shop_domain = :shop
          AND timestamp >= :cutoff_ms
          AND event_type = 'product_view'
    """), {"shop": shop, "cutoff_ms": cutoff_ms}).fetchone()

    total = row[0] or 1
    uniques = row[1] or 0
    mobile_pct = (row[2] or 0) / total * 100
    paid_pct = (row[4] or 0) / total * 100
    organic_pct = (row[5] or 0) / total * 100

    # Shannon-style entropy across 3 source buckets.
    source_entropy = 0.0
    for src_pct in (paid_pct, organic_pct, 100 - paid_pct - organic_pct):
        p = max(src_pct / 100, 0.001)
        source_entropy -= p * math.log2(p)
    diversity_score = _score(source_entropy, 0.5, 1.5)

    return {
        "cluster": "Traffic DNA",
        "genes": [
            _gene("volume", _score(uniques, 50, 5000), uniques, "visitors/30d",
                  f"{uniques} unique visitors in 30 days.",
                  "Increase traffic through SEO and content marketing." if uniques < 500 else "Traffic volume is healthy."),
            _gene("mobile_mix", _score(mobile_pct, 20, 70), round(mobile_pct, 1), "%",
                  f"{mobile_pct:.0f}% mobile traffic.",
                  "Optimize mobile UX — your mobile traffic is significant." if mobile_pct > 50 else "Desktop-heavy traffic — ensure desktop experience is polished."),
            _gene("source_diversity", diversity_score, round(source_entropy, 2), "entropy",
                  f"Traffic source diversity: {source_entropy:.2f} (higher = more balanced).",
                  "Diversify traffic sources to reduce dependency on any single channel." if diversity_score < 50 else "Good traffic diversification."),
        ],
    }


def _cluster_conversion(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    cutoff_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    row = db.execute(text("""
        SELECT
            COUNT(DISTINCT visitor_id) FILTER (WHERE event_type = 'product_view') as viewers,
            COUNT(DISTINCT visitor_id) FILTER (WHERE event_type = 'add_to_cart') as carters,
            COUNT(DISTINCT visitor_id) FILTER (WHERE event_type = 'purchase') as buyers
        FROM events
        WHERE shop_domain = :shop
          AND timestamp >= :cutoff_ms
    """), {"shop": shop, "cutoff_ms": cutoff_ms}).fetchone()

    viewers = row[0] or 1
    carters = row[1] or 0
    buyers = row[2] or 0

    view_to_cart = carters / viewers * 100
    cart_to_purchase = (buyers / carters * 100) if carters > 0 else 0
    overall_cvr = buyers / viewers * 100

    return {
        "cluster": "Conversion DNA",
        "genes": [
            _gene("overall_cvr", _score(overall_cvr, 0.5, 5), round(overall_cvr, 2), "%",
                  f"Overall conversion: {overall_cvr:.2f}%.",
                  "Focus on reducing friction in the checkout flow." if overall_cvr < 2 else "Conversion rate is competitive."),
            _gene("browse_to_cart", _score(view_to_cart, 2, 15), round(view_to_cart, 2), "%",
                  f"Browse-to-cart: {view_to_cart:.1f}%.",
                  "Products aren't compelling enough to add to cart. Improve photos, descriptions, or social proof." if view_to_cart < 5 else "Product pages are converting well to cart."),
            _gene("cart_to_purchase", _score(cart_to_purchase, 20, 70), round(cart_to_purchase, 2), "%",
                  f"Cart-to-purchase: {cart_to_purchase:.1f}%.",
                  "Cart abandonment is high. Consider trust signals, clearer shipping costs, or exit-intent nudges." if cart_to_purchase < 40 else "Checkout completion is strong."),
        ],
    }


def _cluster_product(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    row = db.execute(text("""
        SELECT
            COUNT(*) as total_products,
            SUM(views_7d) as total_views,
            SUM(purchases_7d) as total_purchases,
            MAX(views_7d) as max_views,
            AVG(CASE WHEN views_7d >= 5 THEN
                purchases_7d::float / views_7d * 100
            ELSE NULL END) as avg_cvr
        FROM product_metrics
        WHERE shop_domain = :shop AND views_7d > 0
    """), {"shop": shop}).fetchone()

    total_products = row[0] or 0
    total_views = row[1] or 0
    max_views = row[3] or 0
    hero_concentration = (max_views / total_views * 100) if total_views > 0 else 0

    zero_purchase = db.execute(text("""
        SELECT COUNT(*) FROM product_metrics
        WHERE shop_domain = :shop AND views_7d >= 10 AND purchases_7d = 0
    """), {"shop": shop}).scalar() or 0

    return {
        "cluster": "Product DNA",
        "genes": [
            _gene("catalog_depth", _score(total_products, 3, 50), total_products, "products",
                  f"{total_products} products with traffic.",
                  "Expand your catalog to capture more search intent." if total_products < 10 else "Good product catalog depth."),
            _gene("hero_dependency", _score(100 - hero_concentration, 20, 80), round(hero_concentration, 1), "% to top product",
                  f"Top product gets {hero_concentration:.0f}% of all views.",
                  "Revenue too concentrated in one product. Diversify or cross-sell." if hero_concentration > 50 else "Healthy product view distribution."),
            _gene("dead_stock",
                  _score(max(0, total_products - zero_purchase), 0, total_products) if total_products > 0 else 50,
                  zero_purchase, "products",
                  f"{zero_purchase} products have views but zero purchases.",
                  f"Investigate {zero_purchase} products getting traffic but no sales — price, description, or trust issue." if zero_purchase > 3 else "Most viewed products are converting."),
        ],
    }


def _cluster_customer(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    row = db.execute(text("""
        SELECT
            COUNT(DISTINCT customer_email) as total_customers,
            COUNT(*) as total_orders,
            AVG(total_price) FILTER (WHERE total_price > 0) as avg_aov,
            SUM(total_price) as total_revenue
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
          AND (:currency IS NULL OR currency = :currency)
          AND customer_email IS NOT NULL
          AND customer_email != ''
    """), {"shop": shop, "cutoff": now - timedelta(days=90), "currency": currency}).fetchone()

    total_customers = row[0] or 0
    total_orders = row[1] or 0
    avg_aov = float(row[2] or 0)
    total_revenue = float(row[3] or 0)
    orders_per_customer = total_orders / max(total_customers, 1)
    repeat_rate = (
        max(0, (orders_per_customer - 1) / orders_per_customer * 100)
        if orders_per_customer > 1 else 0
    )
    rpc = total_revenue / max(total_customers, 1)
    ccy = currency or "USD"

    return {
        "cluster": "Customer DNA",
        "genes": [
            _gene("repeat_rate", _score(repeat_rate, 5, 40), round(repeat_rate, 1), "%",
                  f"Repeat purchase rate: {repeat_rate:.1f}%.",
                  "Invest in post-purchase nurture sequences to drive repeat orders." if repeat_rate < 15 else "Strong repeat customer base."),
            _gene("aov", _score(avg_aov, 20, 150), round(avg_aov, 2), ccy,
                  f"Average order value: {ccy} {avg_aov:.0f}.",
                  "Increase AOV with bundles, upsells, or free shipping thresholds." if avg_aov < 50 else "AOV is healthy."),
            _gene("revenue_per_customer", _score(rpc, 30, 300), round(rpc, 2), f"{ccy}/90d",
                  f"Revenue per customer (90d): {ccy} {rpc:.0f}.",
                  "Low customer lifetime value. Focus on retention and cross-selling." if rpc < 80 else "Good per-customer revenue."),
        ],
    }


def _cluster_intervention(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'shown') as shown,
            COUNT(*) FILTER (WHERE event_type = 'clicked') as clicked,
            COUNT(*) FILTER (WHERE event_type = 'dismissed') as dismissed,
            COUNT(*) FILTER (WHERE event_type = 'purchase_after_exposed') as converted
        FROM nudge_events
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
    """), {"shop": shop, "cutoff": now - timedelta(days=30)}).fetchone()

    shown = row[0] or 0
    clicked = row[1] or 0
    converted = row[3] or 0
    nudge_ctr = (clicked / shown * 100) if shown > 0 else 0
    nudge_cvr = (converted / shown * 100) if shown > 0 else 0

    return {
        "cluster": "Intervention DNA",
        "genes": [
            _gene("nudge_reach", _score(shown, 10, 1000), shown, "shown/30d",
                  f"{shown} nudges shown in 30 days.",
                  "Enable more nudge variants to reach more visitors." if shown < 100 else "Good nudge coverage."),
            _gene("nudge_engagement", _score(nudge_ctr, 1, 10), round(nudge_ctr, 2), "% CTR",
                  f"Nudge click-through: {nudge_ctr:.1f}%.",
                  "Improve nudge copy and targeting to increase engagement." if nudge_ctr < 3 else "Strong nudge engagement."),
            _gene("nudge_conversion", _score(nudge_cvr, 0.1, 3), round(nudge_cvr, 2), "% CVR",
                  f"Nudge-to-purchase: {nudge_cvr:.2f}%.",
                  "Nudges aren't driving purchases. Test different triggers and timing." if nudge_cvr < 0.5 else "Nudges are effectively driving revenue."),
        ],
    }


def _cluster_risk(db: Session, shop: str, currency: str | None, *, now: datetime) -> dict:
    # Revenue volatility (coefficient of variation of weekly revenue).
    rows = db.execute(text("""
        SELECT date_trunc('week', created_at) as week, SUM(total_price) as rev
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
          AND (:currency IS NULL OR currency = :currency)
        GROUP BY week
        ORDER BY week
    """), {"shop": shop, "cutoff": now - timedelta(days=90), "currency": currency}).fetchall()

    revs = [float(r[1] or 0) for r in rows]
    if len(revs) < 4:
        return {
            "cluster": "Risk DNA",
            "genes": [
                _gene("stability", 50, 0, "",
                      "Insufficient data (need 4+ weeks).",
                      "Keep tracking — genome needs more data."),
            ],
        }

    mean_rev = sum(revs) / len(revs)
    std_rev = (sum((r - mean_rev) ** 2 for r in revs) / len(revs)) ** 0.5
    cv = (std_rev / mean_rev * 100) if mean_rev > 0 else 0
    volatility_score = _score(100 - cv, 0, 80)  # lower CV = better

    # Trend: second half mean vs first half mean.
    mid = len(revs) // 2
    first_half = sum(revs[:mid]) / max(mid, 1)
    second_half = sum(revs[mid:]) / max(len(revs) - mid, 1)
    trend = ((second_half - first_half) / first_half * 100) if first_half > 0 else 0

    return {
        "cluster": "Risk DNA",
        "genes": [
            _gene("stability", volatility_score, round(cv, 1), "% CV",
                  f"Revenue volatility: {cv:.0f}% coefficient of variation.",
                  "High revenue volatility. Diversify acquisition channels and build recurring revenue." if cv > 50 else "Revenue is relatively stable."),
            _gene("trajectory", _score(trend, -30, 30), round(trend, 1), "% trend",
                  f"Revenue trend: {trend:+.1f}% (recent vs earlier).",
                  "Revenue is declining. Investigate traffic, conversion, or competitive pressure." if trend < -10
                  else "Revenue trajectory is positive." if trend > 5
                  else "Revenue is flat — look for growth levers."),
        ],
    }


_CLUSTERS: tuple[tuple[str, str, _Cluster], ...] = (
    ("traffic",      "Traffic DNA",      _cluster_traffic),
    ("conversion",   "Conversion DNA",   _cluster_conversion),
    ("product",      "Product DNA",      _cluster_product),
    ("customer",     "Customer DNA",     _cluster_customer),
    ("intervention", "Intervention DNA", _cluster_intervention),
    ("risk",         "Risk DNA",         _cluster_risk),
)


def _aggregate_genes(genes_by_cluster: dict) -> dict:
    """Roll up scores + extract priority actions across all clusters."""
    all_scores = [
        g["score"]
        for cluster in genes_by_cluster.values()
        for g in cluster.get("genes", [])
    ]
    overall = round(sum(all_scores) / len(all_scores)) if all_scores else 0

    all_genes = [
        (g, cluster["cluster"])
        for cluster in genes_by_cluster.values()
        for g in cluster.get("genes", [])
    ]
    all_genes.sort(key=lambda x: x[0]["score"])
    priority_actions = [
        {"gene": g["name"], "cluster": c, "score": g["score"], "action": g["action"]}
        for g, c in all_genes[:3]
        if g["score"] < 60
    ]

    return {
        "overall": overall,
        "total": len(all_scores),
        "strong": len([s for s in all_scores if s >= 70]),
        "weak": len([s for s in all_scores if s < 40]),
        "priority_actions": priority_actions,
    }


_ARCHETYPE_BANDS: tuple[tuple[int, str, str], ...] = (
    (80, "Revenue Machine",
     "Your store is firing on all cylinders. Focus on scaling what works."),
    (60, "Growth Ready",
     "Strong foundation with clear opportunities. Execute the priority actions below."),
    (40, "Emerging",
     "Good potential but several leaks need fixing. Address weak genes first."),
    (0,  "Early Stage",
     "Focus on fundamentals: traffic, product-market fit, and basic conversion."),
)


def _archetype(overall: int) -> tuple[str, str]:
    """Map an overall score to (archetype, description)."""
    for threshold, name, desc in _ARCHETYPE_BANDS:
        if overall >= threshold:
            return name, desc
    return _ARCHETYPE_BANDS[-1][1], _ARCHETYPE_BANDS[-1][2]


def compute_revenue_genome(db: Session, shop_domain: str) -> dict:
    """Compute the full Revenue Genome for a merchant.

    Returns a structured profile with 6 gene clusters, overall health
    score, archetype classification, and priority actions. Cached 6h.
    """
    cached = _cache_get(shop_domain)
    if cached is not None:
        return cached

    now = _now()
    currency = get_shop_currency(db, shop_domain)

    genes: dict = {}
    for slot, label, fn in _CLUSTERS:
        try:
            genes[slot] = fn(db, shop_domain, currency, now=now)
        except Exception as exc:
            log.warning("revenue_genome: cluster %s failed: %s", slot, exc)
            genes[slot] = {
                "cluster": label,
                "genes": [],
                "error": "insufficient_data",
            }

    agg = _aggregate_genes(genes)
    archetype, desc = _archetype(agg["overall"])

    result = {
        "shop_domain": shop_domain,
        "overall_score": agg["overall"],
        "archetype": archetype,
        "archetype_description": desc,
        "gene_clusters": genes,
        "priority_actions": agg["priority_actions"],
        "total_genes": agg["total"],
        "strong_genes": agg["strong"],
        "weak_genes": agg["weak"],
        "generated_at": now.isoformat(),
    }
    _cache_set(shop_domain, result)
    return result
