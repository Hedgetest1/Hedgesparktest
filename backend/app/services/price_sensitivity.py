"""
price_sensitivity.py — Behavioral price elasticity detector.

Correlates visitor engagement depth (views, dwell time, scroll depth)
with actual purchase decisions at different price points to identify:
  1. Price ceilings — where conversion drops off sharply
  2. Sweet spots — price ranges with highest conversion
  3. Browse-but-don't-buy products — high interest, price barrier

Builds on existing SIP price_sensitivity_bands (cart rate by band)
and adds the behavioral dimension that no competitor has.

Data: product_metrics (prices inferred from revenue/purchases) + events.
No LLM. Pure analytics. Cached 6h per shop.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("price_sensitivity")

_CACHE_TTL = 6 * 3600
_CACHE_PREFIX = "hs:pricesens:v1"

# Band buckets are fixed ranges; the display label is currency-aware and
# resolved at serialization time, not at module-import time. The numeric
# bounds below are interpreted in the shop's native currency — a GBP shop
# sees "£0-15"/"£15-30"/..., a JPY shop sees "¥0-15"/..., etc.
_PRICE_BANDS = [
    (0, 15),
    (15, 30),
    (30, 50),
    (50, 100),
    (100, 250),
    (250, 99999),
]


def _band_label(lo: int, hi: int, currency: str | None) -> str:
    from app.core.currency import currency_symbol
    sym = currency_symbol(currency)
    if hi >= 99999:
        return f"{sym}{lo}+"
    return f"{sym}{lo}-{hi}"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _humanize_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return slug.replace("-", " ").replace("_", " ").title() or url


def compute_price_sensitivity(db: Session, shop_domain: str) -> dict:
    """
    Compute behavioral price sensitivity analysis.

    Returns per-band conversion rates + per-product elasticity signals.
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
        log.warning("price_sensitivity: compute_price_sensitivity failed: %s", exc)

    now = _now()

    # Get product prices from recent orders
    price_rows = db.execute(text("""
        SELECT line_items FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
    """), {"shop": shop_domain, "cutoff": now - timedelta(days=30)}).fetchall()

    product_prices: dict[str, float] = {}
    for r in price_rows:
        items = r[0] or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("product_handle") or "")
            price = float(item.get("price") or 0)
            if handle and price > 0:
                url = f"/products/{handle}"
                product_prices[url] = price

    if not product_prices:
        return {
            "shop_domain": shop_domain,
            "bands": [],
            "products": [],
            "headline": "Insufficient order data for price sensitivity analysis.",
            "generated_at": now.isoformat(),
        }

    # Get behavioral data from product_metrics
    pm_rows = db.execute(text("""
        SELECT product_url, views_7d, cart_conversions_7d, purchases_7d,
               avg_dwell_24h, avg_scroll_24h, return_visitor_count_7d,
               unique_visitors_7d
        FROM product_metrics
        WHERE shop_domain = :shop
          AND views_7d >= 3
    """), {"shop": shop_domain}).fetchall()

    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop_domain)
    bands_with_labels = [(lo, hi, _band_label(lo, hi, currency)) for lo, hi in _PRICE_BANDS]

    # Per-band aggregation
    band_stats: dict[str, dict] = {}
    for lo, hi, label in bands_with_labels:
        band_stats[label] = {
            "band": label, "lo": lo, "hi": hi,
            "products": 0, "total_views": 0, "total_carts": 0,
            "total_purchases": 0, "total_dwell": 0.0, "total_scroll": 0.0,
            "dwell_samples": 0, "return_visitors": 0,
        }

    product_analysis = []

    for r in pm_rows:
        purl = r[0]
        price = product_prices.get(purl, 0)
        if price <= 0:
            continue

        views = r[1] or 0
        carts = r[2] or 0
        purchases = r[3] or 0
        dwell = r[4]
        scroll = r[5]
        return_visitors = r[6] or 0
        unique_visitors = r[7] or 0

        # Find band
        for lo, hi, label in bands_with_labels:
            if lo <= price < hi:
                bs = band_stats[label]
                bs["products"] += 1
                bs["total_views"] += views
                bs["total_carts"] += carts
                bs["total_purchases"] += purchases
                bs["return_visitors"] += return_visitors
                if dwell and dwell > 0:
                    bs["total_dwell"] += dwell
                    bs["dwell_samples"] += 1
                if scroll and scroll > 0:
                    bs["total_scroll"] += scroll
                break

        # Per-product analysis
        cvr = (purchases / views * 100) if views > 0 else 0
        cart_rate = (carts / views * 100) if views > 0 else 0

        # Interest-to-purchase ratio — high interest + low purchase = price barrier
        interest_score = 0
        if dwell and dwell > 20:
            interest_score += 30
        if scroll and scroll > 50:
            interest_score += 20
        if return_visitors > 2:
            interest_score += 30
        if unique_visitors > 5:
            interest_score += 20

        purchase_score = min(100, cvr * 10)
        gap = max(0, interest_score - purchase_score)

        if views >= 5 and gap > 30:
            product_analysis.append({
                "product_url": purl,
                "product_name": _humanize_url(purl),
                "price": round(price, 2),
                "views_7d": views,
                "cvr_pct": round(cvr, 2),
                "cart_rate_pct": round(cart_rate, 2),
                "interest_score": interest_score,
                "purchase_score": round(purchase_score),
                "price_barrier_gap": round(gap),
                "avg_dwell_sec": round(dwell, 1) if dwell else None,
                "avg_scroll_pct": round(scroll, 1) if scroll else None,
                "return_visitors": return_visitors,
                "signal": (
                    "High visitor interest but low conversion — "
                    "price may be above the willingness threshold."
                ),
            })

    # Compute band summaries
    bands = []
    for label in [l for _, _, l in bands_with_labels]:
        bs = band_stats[label]
        if bs["products"] == 0:
            continue
        cvr = (bs["total_purchases"] / bs["total_views"] * 100) if bs["total_views"] > 0 else 0
        cart_rate = (bs["total_carts"] / bs["total_views"] * 100) if bs["total_views"] > 0 else 0
        avg_dwell = (bs["total_dwell"] / bs["dwell_samples"]) if bs["dwell_samples"] > 0 else 0

        bands.append({
            "band": label,
            "products": bs["products"],
            "views": bs["total_views"],
            "carts": bs["total_carts"],
            "purchases": bs["total_purchases"],
            "cvr_pct": round(cvr, 2),
            "cart_rate_pct": round(cart_rate, 2),
            "avg_dwell_sec": round(avg_dwell, 1),
            "return_visitors": bs["return_visitors"],
        })

    # Find sweet spot and ceiling
    product_analysis.sort(key=lambda p: p["price_barrier_gap"], reverse=True)
    product_analysis = product_analysis[:10]

    if bands:
        best_band = max(bands, key=lambda b: b["cvr_pct"])
        worst_band = min((b for b in bands if b["views"] >= 10), key=lambda b: b["cvr_pct"], default=None)

        if worst_band and best_band["band"] != worst_band["band"]:
            headline = (
                f"Sweet spot: {best_band['band']} ({best_band['cvr_pct']:.1f}% CVR). "
                f"Ceiling: {worst_band['band']} ({worst_band['cvr_pct']:.1f}% CVR). "
                f"{len(product_analysis)} products show price barrier signals."
            )
        else:
            headline = f"Best converting band: {best_band['band']} at {best_band['cvr_pct']:.1f}% CVR."
    else:
        headline = "Insufficient data for price band analysis."

    result = {
        "shop_domain": shop_domain,
        "bands": bands,
        "products": product_analysis,
        "headline": headline,
        "generated_at": now.isoformat(),
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("price_sensitivity: compute_price_sensitivity failed: %s", exc)

    return result
