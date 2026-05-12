"""
revenue_autopsy.py — Product-level "why did revenue change?" analyzer.

For each product, decomposes the revenue delta into 3 root causes:
  1. Traffic delta (views changed)
  2. Conversion delta (CVR changed)
  3. Value delta (AOV / refund impact)

This is the unified view that no competitor offers. Most analytics tools
show THAT revenue changed. We show WHY.

Data sources: product_metrics (aggregation worker), shop_orders, refund_ingest.
No LLM. Pure math. Cached 3h per shop.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("revenue_autopsy")

_CACHE_TTL = 3 * 3600
_CACHE_PREFIX = "hs:autopsy:v1"
_MAX_PRODUCTS = 15


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _humanize_url(product_url: str) -> str:
    """'/products/premium-leather-wallet' → 'Premium Leather Wallet'."""
    slug = product_url.rstrip("/").rsplit("/", 1)[-1] if product_url else ""
    return slug.replace("-", " ").replace("_", " ").title() or product_url


# ---------------------------------------------------------------------------
# Revenue-autopsy building blocks
# ---------------------------------------------------------------------------
# Refactor 2026-05-12 (A3 medium close): 265-LOC god function → composer
# + 7 pure helpers + 2 SQL constants. Identical contract preserved (cache
# format, response keys, decomposition formula).

_TRAFFIC_SQL = text("""
    SELECT product_url,
           COUNT(*) FILTER (WHERE to_timestamp(timestamp/1000) >= :recent) as views_recent,
           COUNT(*) FILTER (WHERE to_timestamp(timestamp/1000) < :recent) as views_prior,
           COUNT(DISTINCT visitor_id) FILTER (WHERE to_timestamp(timestamp/1000) >= :recent) as uniques_recent,
           COUNT(DISTINCT visitor_id) FILTER (WHERE to_timestamp(timestamp/1000) < :recent) as uniques_prior
    FROM events
    WHERE shop_domain = :shop
      AND event_type = 'product_view'
      AND to_timestamp(timestamp/1000) >= :prior
      AND product_url IS NOT NULL
      AND product_url != ''
    GROUP BY product_url
    HAVING COUNT(*) >= 3
""")


_ORDERS_SQL = text("""
    SELECT line_items, total_price, created_at
    FROM shop_orders
    WHERE shop_domain = :shop
      AND created_at >= :prior
""")


_ZERO_TRAFFIC = {"views_recent": 0, "views_prior": 0, "uniques_recent": 0, "uniques_prior": 0}
_ZERO_REVENUE = {"orders_recent": 0, "orders_prior": 0, "revenue_recent": 0.0, "revenue_prior": 0.0}


def _cache_key(shop_domain: str) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"


def _load_cached_autopsy(shop_domain: str) -> dict | None:
    """Return cached autopsy JSON or None on miss/error. Observed via
    record_silent_return so cache-degradation is visible in metrics."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("revenue_autopsy.cache.get.no_client")
            return None
        cached = rc.get(_cache_key(shop_domain))
        return json.loads(cached) if cached else None
    except Exception as exc:
        log.warning("revenue_autopsy: cache read failed: %s", exc)
        record_silent_return("revenue_autopsy.cache.get.exception")
        return None


def _save_cached_autopsy(shop_domain: str, result: dict) -> None:
    """Best-effort cache write — never raises. Failures observed via
    record_silent_return; cache-write degradation visible in metrics."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("revenue_autopsy.cache.set.no_client")
            return
        rc.setex(_cache_key(shop_domain), _CACHE_TTL, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("revenue_autopsy: cache write failed: %s", exc)
        record_silent_return("revenue_autopsy.cache.set.exception")


def _fetch_traffic_data(db, shop_domain: str, recent_start, prior_start) -> dict[str, dict]:
    """Aggregate product_view events into per-URL recent/prior counts."""
    rows = db.execute(_TRAFFIC_SQL, {
        "shop": shop_domain, "recent": recent_start, "prior": prior_start,
    }).fetchall()
    return {
        r[0]: {
            "views_recent": r[1], "views_prior": r[2],
            "uniques_recent": r[3], "uniques_prior": r[4],
        }
        for r in rows
    }


def _fetch_revenue_data(db, shop_domain: str, recent_start, prior_start) -> dict[str, dict]:
    """Walk shop_orders line items, aggregate per-product revenue recent/prior."""
    rows = db.execute(_ORDERS_SQL, {
        "shop": shop_domain, "prior": prior_start,
    }).fetchall()

    revenue_map: dict[str, dict] = {}
    for r in rows:
        items = r[0] or []
        is_recent = r[2] >= recent_start if r[2] else False
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("product_handle") or item.get("handle") or "")
            product_url = f"/products/{handle}" if handle else ""
            if not product_url or product_url == "/products/":
                continue

            price = float(item.get("price") or 0)
            qty = int(item.get("quantity") or 1)
            agg = revenue_map.setdefault(product_url, dict(_ZERO_REVENUE))
            if is_recent:
                agg["orders_recent"] += qty
                agg["revenue_recent"] += price * qty
            else:
                agg["orders_prior"] += qty
                agg["revenue_prior"] += price * qty
    return revenue_map


def _compute_one_autopsy(product_url: str, t: dict, r: dict) -> dict | None:
    """
    Per-product revenue autopsy. Returns dict OR None when:
    - data is below minimum threshold (views<5 AND orders<2), OR
    - change is negligible (|rev_delta|<1 AND |traffic_change|<10%).

    Revenue decomposition: R = Views × CVR × AOV →
    dR ≈ (dViews × CVR₀ × AOV₀) + (Views₀ × dCVR × AOV₀) + (Views₀ × CVR₀ × dAOV).
    """
    total_views = t["views_recent"] + t["views_prior"]
    total_orders = r["orders_recent"] + r["orders_prior"]
    if total_views < 5 and total_orders < 2:
        return None

    rev_delta = r["revenue_recent"] - r["revenue_prior"]

    views_recent = max(t["views_recent"], 1)
    views_prior = max(t["views_prior"], 1)
    traffic_change_pct = ((views_recent - views_prior) / views_prior) * 100

    cvr_recent = (r["orders_recent"] / views_recent * 100) if views_recent > 0 else 0
    cvr_prior = (r["orders_prior"] / views_prior * 100) if views_prior > 0 else 0
    cvr_delta = cvr_recent - cvr_prior

    aov_recent = r["revenue_recent"] / r["orders_recent"] if r["orders_recent"] > 0 else 0
    aov_prior = r["revenue_prior"] / r["orders_prior"] if r["orders_prior"] > 0 else 0
    aov_delta_pct = ((aov_recent - aov_prior) / aov_prior * 100) if aov_prior > 0 else 0

    base_cvr = cvr_prior / 100 if cvr_prior > 0 else 0.01
    base_aov = aov_prior if aov_prior > 0 else aov_recent

    traffic_impact = (views_recent - views_prior) * base_cvr * base_aov
    conversion_impact = views_prior * (cvr_delta / 100) * base_aov
    value_impact = rev_delta - traffic_impact - conversion_impact

    impacts = {
        "traffic": abs(traffic_impact),
        "conversion": abs(conversion_impact),
        "value": abs(value_impact),
    }
    primary_cause = max(impacts, key=impacts.get)

    if abs(rev_delta) < 1 and abs(traffic_change_pct) < 10:
        return None

    direction = "growing" if rev_delta > 0 else "declining"
    if primary_cause == "traffic":
        narrative = (
            f"{'More' if traffic_change_pct > 0 else 'Fewer'} visitors "
            f"({traffic_change_pct:+.0f}%) is the main driver."
        )
    elif primary_cause == "conversion":
        narrative = (
            f"Conversion rate {'improved' if cvr_delta > 0 else 'dropped'} "
            f"({cvr_prior:.1f}% → {cvr_recent:.1f}%)."
        )
    else:
        narrative = (
            f"Average order value shifted "
            f"(€{aov_prior:.0f} → €{aov_recent:.0f}, {aov_delta_pct:+.0f}%)."
        )

    return {
        "product_url": product_url,
        "product_name": _humanize_url(product_url),
        "revenue_recent_7d": round(r["revenue_recent"], 2),
        "revenue_prior_7d": round(r["revenue_prior"], 2),
        "revenue_delta_eur": round(rev_delta, 2),
        "direction": direction,
        "primary_cause": primary_cause,
        "narrative": narrative,
        "traffic": {
            "views_recent": t["views_recent"],
            "views_prior": t["views_prior"],
            "change_pct": round(traffic_change_pct, 1),
            "impact_eur": round(traffic_impact, 2),
        },
        "conversion": {
            "cvr_recent_pct": round(cvr_recent, 2),
            "cvr_prior_pct": round(cvr_prior, 2),
            "delta_pp": round(cvr_delta, 2),
            "impact_eur": round(conversion_impact, 2),
        },
        "value": {
            "aov_recent": round(aov_recent, 2),
            "aov_prior": round(aov_prior, 2),
            "change_pct": round(aov_delta_pct, 1),
            "impact_eur": round(value_impact, 2),
        },
    }


def _summarize_autopsies(autopsies: list[dict]) -> tuple[list[dict], list[dict], float, float, str]:
    """Split into declining/growing, sum revenue, identify top decline cause."""
    declining = [a for a in autopsies if a["direction"] == "declining"]
    growing = [a for a in autopsies if a["direction"] == "growing"]
    total_loss = sum(abs(a["revenue_delta_eur"]) for a in declining)
    total_gain = sum(a["revenue_delta_eur"] for a in growing)

    cause_counts: dict[str, int] = {}
    for a in declining:
        cause_counts[a["primary_cause"]] = cause_counts.get(a["primary_cause"], 0) + 1
    top_cause = max(cause_counts, key=cause_counts.get) if cause_counts else "none"

    return declining, growing, total_loss, total_gain, top_cause


def _resolve_currency_formatter(db, shop_domain: str):
    """
    Return (currency_iso, format_money_fn). USD fallback ensures the
    response always carries a valid ISO code even when lookup fails.
    Failures logged for observability — never silently swallowed.
    """
    try:
        from app.services.revenue_metrics import get_shop_currency
        from app.core.currency import format_money as _fmt_money
        currency = get_shop_currency(db, shop_domain) or "USD"
        return currency, _fmt_money
    except Exception as exc:
        log.warning(
            "revenue_autopsy: currency resolution failed (using USD fallback): %s",
            exc,
        )
        return "USD", lambda v, c: f"{v:.0f}"


def _build_headline(declining, growing, total_loss, total_gain, top_cause, currency, fmt_money) -> str:
    if declining:
        return (
            f"{len(declining)} products declining (−{fmt_money(total_loss, currency)}/week). "
            f"Main cause: {top_cause}."
        )
    if growing:
        return (
            f"All {len(growing)} tracked products are growing "
            f"(+{fmt_money(total_gain, currency)}/week)."
        )
    return "Insufficient data for revenue autopsy this period."


def compute_product_autopsy(db: Session, shop_domain: str) -> dict:
    """
    Compute revenue autopsy for all products with enough data.

    Returns:
      {shop_domain, products: [...], summary, headline, currency, generated_at}

    Refactored 2026-05-12 (A3 medium close): 265-LOC god function → 35-LOC
    composer + 7 pure helpers + 2 module-level SQL constants. Cache-on-hit
    short-circuit preserved; cache key + TTL byte-identical.
    """
    cached = _load_cached_autopsy(shop_domain)
    if cached is not None:
        return cached

    now = _now()
    recent_start = now - timedelta(days=7)
    prior_start = now - timedelta(days=14)

    traffic_map = _fetch_traffic_data(db, shop_domain, recent_start, prior_start)
    revenue_map = _fetch_revenue_data(db, shop_domain, recent_start, prior_start)

    all_products = set(traffic_map.keys()) | set(revenue_map.keys())
    autopsies = []
    for product_url in all_products:
        t = traffic_map.get(product_url, dict(_ZERO_TRAFFIC))
        r = revenue_map.get(product_url, dict(_ZERO_REVENUE))
        record = _compute_one_autopsy(product_url, t, r)
        if record is not None:
            autopsies.append(record)

    autopsies.sort(key=lambda a: abs(a["revenue_delta_eur"]), reverse=True)
    autopsies = autopsies[:_MAX_PRODUCTS]

    declining, growing, total_loss, total_gain, top_cause = _summarize_autopsies(autopsies)
    currency, fmt_money = _resolve_currency_formatter(db, shop_domain)
    headline = _build_headline(
        declining, growing, total_loss, total_gain, top_cause, currency, fmt_money
    )

    result = {
        "shop_domain": shop_domain,
        "products": autopsies,
        "summary": {
            "declining_count": len(declining),
            "growing_count": len(growing),
            "total_loss_per_week": round(total_loss, 2),
            "total_gain_per_week": round(total_gain, 2),
            "top_decline_cause": top_cause,
        },
        "headline": headline,
        "currency": currency,
        "generated_at": now.isoformat(),
    }

    _save_cached_autopsy(shop_domain, result)
    return result
