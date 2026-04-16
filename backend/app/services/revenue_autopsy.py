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


def compute_product_autopsy(db: Session, shop_domain: str) -> dict:
    """
    Compute revenue autopsy for all products with enough data.

    Returns:
      {
        shop_domain, products: [...], summary, generated_at
      }
    """
    cache_key = f"{_CACHE_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception as exc:
        log.warning("revenue_autopsy: cache read failed: %s", exc)

    now = _now()
    recent_start = now - timedelta(days=7)
    prior_start = now - timedelta(days=14)

    # --- Traffic data from events ---
    traffic_rows = db.execute(text("""
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
    """), {
        "shop": shop_domain,
        "recent": recent_start,
        "prior": prior_start,
    }).fetchall()

    traffic_map = {}
    for r in traffic_rows:
        traffic_map[r[0]] = {
            "views_recent": r[1], "views_prior": r[2],
            "uniques_recent": r[3], "uniques_prior": r[4],
        }

    # --- Revenue data from shop_orders ---
    order_rows = db.execute(text("""
        SELECT line_items, total_price, created_at
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :prior
    """), {
        "shop": shop_domain,
        "prior": prior_start,
    }).fetchall()

    revenue_map: dict[str, dict] = {}
    for r in order_rows:
        items = r[0] or []
        created = r[1]
        is_recent = r[2] >= recent_start if r[2] else False

        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            # Match by product handle or title
            handle = str(item.get("product_handle") or item.get("handle") or "")
            product_url = f"/products/{handle}" if handle else ""
            if not product_url or product_url == "/products/":
                continue

            price = float(item.get("price") or 0)
            qty = int(item.get("quantity") or 1)

            agg = revenue_map.setdefault(product_url, {
                "orders_recent": 0, "orders_prior": 0,
                "revenue_recent": 0.0, "revenue_prior": 0.0,
            })
            if is_recent:
                agg["orders_recent"] += qty
                agg["revenue_recent"] += price * qty
            else:
                agg["orders_prior"] += qty
                agg["revenue_prior"] += price * qty

    # --- Merge and compute autopsy ---
    all_products = set(traffic_map.keys()) | set(revenue_map.keys())
    autopsies = []

    for product_url in all_products:
        t = traffic_map.get(product_url, {
            "views_recent": 0, "views_prior": 0,
            "uniques_recent": 0, "uniques_prior": 0,
        })
        r = revenue_map.get(product_url, {
            "orders_recent": 0, "orders_prior": 0,
            "revenue_recent": 0.0, "revenue_prior": 0.0,
        })

        # Need minimum data to be meaningful
        total_views = t["views_recent"] + t["views_prior"]
        total_orders = r["orders_recent"] + r["orders_prior"]
        if total_views < 5 and total_orders < 2:
            continue

        # Revenue delta
        rev_delta = r["revenue_recent"] - r["revenue_prior"]

        # Traffic delta contribution
        views_recent = max(t["views_recent"], 1)
        views_prior = max(t["views_prior"], 1)
        traffic_change_pct = ((views_recent - views_prior) / views_prior) * 100

        # CVR (conversion rate)
        cvr_recent = (r["orders_recent"] / views_recent * 100) if views_recent > 0 else 0
        cvr_prior = (r["orders_prior"] / views_prior * 100) if views_prior > 0 else 0
        cvr_delta = cvr_recent - cvr_prior

        # AOV (average order value)
        aov_recent = r["revenue_recent"] / r["orders_recent"] if r["orders_recent"] > 0 else 0
        aov_prior = r["revenue_prior"] / r["orders_prior"] if r["orders_prior"] > 0 else 0
        aov_delta_pct = ((aov_recent - aov_prior) / aov_prior * 100) if aov_prior > 0 else 0

        # Decompose revenue delta into causes
        # R = Views * CVR * AOV
        # dR ≈ (dViews * CVR₀ * AOV₀) + (Views₀ * dCVR * AOV₀) + (Views₀ * CVR₀ * dAOV)
        base_cvr = cvr_prior / 100 if cvr_prior > 0 else 0.01
        base_aov = aov_prior if aov_prior > 0 else aov_recent

        traffic_impact = (views_recent - views_prior) * base_cvr * base_aov
        conversion_impact = views_prior * (cvr_delta / 100) * base_aov
        value_impact = rev_delta - traffic_impact - conversion_impact

        # Determine primary cause
        impacts = {
            "traffic": abs(traffic_impact),
            "conversion": abs(conversion_impact),
            "value": abs(value_impact),
        }
        primary_cause = max(impacts, key=impacts.get)

        # Skip products with negligible change
        if abs(rev_delta) < 1 and abs(traffic_change_pct) < 10:
            continue

        # Generate narrative
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

        autopsies.append({
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
        })

    # Sort: biggest absolute delta first
    autopsies.sort(key=lambda a: abs(a["revenue_delta_eur"]), reverse=True)
    autopsies = autopsies[:_MAX_PRODUCTS]

    # Summary
    declining = [a for a in autopsies if a["direction"] == "declining"]
    growing = [a for a in autopsies if a["direction"] == "growing"]
    total_loss = sum(abs(a["revenue_delta_eur"]) for a in declining)
    total_gain = sum(a["revenue_delta_eur"] for a in growing)

    cause_counts = {}
    for a in declining:
        cause_counts[a["primary_cause"]] = cause_counts.get(a["primary_cause"], 0) + 1
    top_cause = max(cause_counts, key=cause_counts.get) if cause_counts else "none"

    if declining:
        headline = (
            f"{len(declining)} products declining (−€{total_loss:.0f}/week). "
            f"Main cause: {top_cause}."
        )
    elif growing:
        headline = f"All {len(growing)} tracked products are growing (+€{total_gain:.0f}/week)."
    else:
        headline = "Insufficient data for revenue autopsy this period."

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
        "generated_at": now.isoformat(),
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("revenue_autopsy: cache write failed: %s", exc)

    return result
