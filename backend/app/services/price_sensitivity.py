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


# ---------------------------------------------------------------------------
# compute_price_sensitivity — stage helpers
# Refactor 2026-05-13 (A3 close): 210-LOC god function → composer + 13
# pure stage helpers. Contract preserved byte-identical. SQL unchanged.
# ---------------------------------------------------------------------------


_PRICE_ROWS_SQL = text("""
    SELECT line_items FROM shop_orders
    WHERE shop_domain = :shop
      AND created_at >= :cutoff
""")


_BEHAVIORAL_SQL = text("""
    SELECT product_url, views_7d, cart_conversions_7d, purchases_7d,
           avg_dwell_24h, avg_scroll_24h, return_visitor_count_7d,
           unique_visitors_7d
    FROM product_metrics
    WHERE shop_domain = :shop
      AND views_7d >= 3
""")


def _cache_key_for(shop_domain: str) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"


def _load_cached_sensitivity(shop_domain: str) -> dict | None:
    """Return cached payload or None. Observed via record_silent_return."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("price_sensitivity.cache.get.no_client")
            return None
        cached = rc.get(_cache_key_for(shop_domain))
        return json.loads(cached) if cached else None
    except Exception as exc:
        log.warning("price_sensitivity: cache read failed: %s", exc)
        record_silent_return("price_sensitivity.cache.get.exception")
        return None


def _save_cached_sensitivity(shop_domain: str, result: dict) -> None:
    """Best-effort cache write. Observed via record_silent_return."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("price_sensitivity.cache.set.no_client")
            return
        rc.setex(_cache_key_for(shop_domain), _CACHE_TTL,
                 json.dumps(result, default=str))
    except Exception as exc:
        log.warning("price_sensitivity: cache write failed: %s", exc)
        record_silent_return("price_sensitivity.cache.set.exception")


def _resolve_currency_sensitivity(db: Session, shop_domain: str) -> str | None:
    """Returns shop currency or None on failure (NOT 'USD'-by-default).
    USD fallback happens at the response-assembly site so the
    empty-state branch can use a different fallback path."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.services.revenue_metrics import get_shop_currency
        return get_shop_currency(db, shop_domain)
    except Exception as exc:
        record_silent_return("price_sensitivity.resolve_currency")
        log.warning("price_sensitivity: currency lookup failed: %s", exc)
        return None


def _fetch_price_rows(db: Session, shop_domain: str, cutoff: datetime) -> list:
    return db.execute(
        _PRICE_ROWS_SQL, {"shop": shop_domain, "cutoff": cutoff},
    ).fetchall()


def _fetch_behavioral_rows(db: Session, shop_domain: str) -> list:
    return db.execute(_BEHAVIORAL_SQL, {"shop": shop_domain}).fetchall()


def _build_product_prices(price_rows: list) -> dict[str, float]:
    """Walk shop_orders.line_items rows → {product_url: latest price}.
    Defensive against non-list payloads + non-dict items."""
    out: dict[str, float] = {}
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
                out[f"/products/{handle}"] = price
    return out


def _empty_sensitivity_response(
    shop_domain: str, currency: str, now: datetime,
) -> dict:
    return {
        "shop_domain": shop_domain,
        "bands": [],
        "products": [],
        "headline": "Insufficient order data for price sensitivity analysis.",
        "currency": currency,
        "generated_at": now.isoformat(),
    }


def _build_band_buckets(bands_with_labels: list[tuple]) -> dict[str, dict]:
    """Initialize per-band aggregator dicts keyed by display label."""
    return {
        label: {
            "band": label, "lo": lo, "hi": hi,
            "products": 0, "total_views": 0, "total_carts": 0,
            "total_purchases": 0, "total_dwell": 0.0, "total_scroll": 0.0,
            "dwell_samples": 0, "return_visitors": 0,
        }
        for lo, hi, label in bands_with_labels
    }


def _classify_band(price: float, bands_with_labels: list[tuple]) -> str | None:
    """Map price → band label. Returns None if price below 0 (caller
    short-circuits before reaching here)."""
    for lo, hi, label in bands_with_labels:
        if lo <= price < hi:
            return label
    return None


def _compute_interest_score(
    dwell: float | None, scroll: float | None,
    return_visitors: int, unique_visitors: int,
) -> int:
    """Behavioral interest signal — sum of 4 deterministic bands."""
    score = 0
    if dwell and dwell > 20:
        score += 30
    if scroll and scroll > 50:
        score += 20
    if return_visitors > 2:
        score += 30
    if unique_visitors > 5:
        score += 20
    return score


def _build_product_barrier_record(
    *, purl: str, price: float, views: int, carts: int, purchases: int,
    dwell: float | None, scroll: float | None,
    return_visitors: int, interest_score: int, purchase_score: float, gap: int,
) -> dict:
    cvr = (purchases / views * 100) if views > 0 else 0
    cart_rate = (carts / views * 100) if views > 0 else 0
    return {
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
    }


def _accumulate_band_and_products(
    pm_rows: list, product_prices: dict[str, float],
    band_stats: dict[str, dict], bands_with_labels: list[tuple],
) -> list[dict]:
    """For each (product, price, behavioral metrics) tuple, accumulate
    band-level totals and emit a product_barrier record if the
    interest-vs-purchase gap exceeds 30 with >=5 views. Returns the
    list of barrier records (unsorted, uncapped)."""
    product_analysis: list[dict] = []
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

        band_label = _classify_band(price, bands_with_labels)
        if band_label is not None:
            bs = band_stats[band_label]
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

        cvr = (purchases / views * 100) if views > 0 else 0
        interest_score = _compute_interest_score(
            dwell, scroll, return_visitors, unique_visitors,
        )
        purchase_score = min(100, cvr * 10)
        gap = max(0, interest_score - purchase_score)

        if views >= 5 and gap > 30:
            product_analysis.append(_build_product_barrier_record(
                purl=purl, price=price, views=views, carts=carts,
                purchases=purchases, dwell=dwell, scroll=scroll,
                return_visitors=return_visitors,
                interest_score=interest_score,
                purchase_score=purchase_score, gap=gap,
            ))
    return product_analysis


def _compute_band_summaries(
    band_stats: dict[str, dict], bands_with_labels: list[tuple],
) -> list[dict]:
    """Per-band conversion/cart/dwell summaries. Filters empty bands."""
    out: list[dict] = []
    for _, _, label in bands_with_labels:
        bs = band_stats[label]
        if bs["products"] == 0:
            continue
        cvr = (bs["total_purchases"] / bs["total_views"] * 100) if bs["total_views"] > 0 else 0
        cart_rate = (bs["total_carts"] / bs["total_views"] * 100) if bs["total_views"] > 0 else 0
        avg_dwell = (bs["total_dwell"] / bs["dwell_samples"]) if bs["dwell_samples"] > 0 else 0
        out.append({
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
    return out


def _build_sensitivity_headline(
    bands: list[dict], product_analysis: list[dict],
) -> str:
    """Sweet-spot + ceiling narrative. 3 branches: both differ, single
    best band, no bands."""
    if not bands:
        return "Insufficient data for price band analysis."
    best_band = max(bands, key=lambda b: b["cvr_pct"])
    worst_band = min(
        (b for b in bands if b["views"] >= 10),
        key=lambda b: b["cvr_pct"],
        default=None,
    )
    if worst_band and best_band["band"] != worst_band["band"]:
        return (
            f"Sweet spot: {best_band['band']} ({best_band['cvr_pct']:.1f}% CVR). "
            f"Ceiling: {worst_band['band']} ({worst_band['cvr_pct']:.1f}% CVR). "
            f"{len(product_analysis)} products show price barrier signals."
        )
    return f"Best converting band: {best_band['band']} at {best_band['cvr_pct']:.1f}% CVR."


def compute_price_sensitivity(db: Session, shop_domain: str) -> dict:
    """
    Compute behavioral price sensitivity analysis.

    Returns per-band conversion rates + per-product elasticity signals.

    Refactored 2026-05-13 (A3 close): 210-LOC god function → 30-LOC
    composer + 13 pure helpers.
    """
    cache_hit = _load_cached_sensitivity(shop_domain)
    if cache_hit is not None:
        return cache_hit

    now = _now()
    price_rows = _fetch_price_rows(db, shop_domain, now - timedelta(days=30))
    product_prices = _build_product_prices(price_rows)

    if not product_prices:
        currency_empty = _resolve_currency_sensitivity(db, shop_domain) or "USD"
        return _empty_sensitivity_response(shop_domain, currency_empty, now)

    pm_rows = _fetch_behavioral_rows(db, shop_domain)
    currency = _resolve_currency_sensitivity(db, shop_domain)
    bands_with_labels = [(lo, hi, _band_label(lo, hi, currency)) for lo, hi in _PRICE_BANDS]
    band_stats = _build_band_buckets(bands_with_labels)

    product_analysis = _accumulate_band_and_products(
        pm_rows, product_prices, band_stats, bands_with_labels,
    )
    product_analysis.sort(key=lambda p: p["price_barrier_gap"], reverse=True)
    product_analysis = product_analysis[:10]

    bands = _compute_band_summaries(band_stats, bands_with_labels)

    result = {
        "shop_domain": shop_domain,
        "bands": bands,
        "products": product_analysis,
        "headline": _build_sensitivity_headline(bands, product_analysis),
        "currency": currency or "USD",
        "generated_at": now.isoformat(),
    }
    _save_cached_sensitivity(shop_domain, result)
    return result
