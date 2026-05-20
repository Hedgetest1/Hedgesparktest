"""
abandoned_intent.py — Session-level intent analysis.

Goes beyond basic cart abandonment to answer:
  - Which products do visitors VIEW but never buy?
  - What's the LAST product they looked at before leaving?
  - How does a buyer's session path differ from a non-buyer's?
  - Where exactly in the funnel does intent die?

Data source: events table (product_view, add_to_cart, checkout, purchase).
No LLM. Pure behavioral analytics. Cached 3h per shop.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("abandoned_intent")

_CACHE_TTL = 3 * 3600
_CACHE_PREFIX = "hs:intent:v1"
_SESSION_GAP_MS = 30 * 60 * 1000  # 30 min gap = new session

# Maximum products returned per merchant per call (Pro sees up to this).
# Kept at the top of the module so it's a real compile-time constant
# rather than a forward-reference resolved at runtime — the previous
# position at line 311 worked but was a static-analysis smell caught
# by the 2026-04-19 mega audit.
_MAX_PRODUCTS = 15

# Tier cap for Lite — surfaces the most painful 3 leaks but
# leaves the tail as Pro moat. If founder decides to loosen/tighten,
# this is the single constant to tune.
_LITE_PRODUCT_CAP = 3


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _humanize_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return slug.replace("-", " ").replace("_", " ").title() or url


# ---------------------------------------------------------------------------
# compute_abandoned_intent — stage helpers
# Refactor 2026-05-13 (A3 close): 246-LOC god function → composer + 12
# pure stage helpers. Contract preserved byte-identical (proven by the
# 10 prior tests still green). SQL unchanged.
# ---------------------------------------------------------------------------


_EVENTS_SQL = text("""
    SELECT visitor_id, event_type, product_url, timestamp
    FROM events
    WHERE shop_domain = :shop
      AND to_timestamp(timestamp/1000) >= :cutoff
      AND event_type IN ('product_view', 'add_to_cart', 'checkout', 'purchase')
    ORDER BY visitor_id, timestamp
""")


def _cache_key_for(shop_domain: str) -> str:
    return f"{_CACHE_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"


def _load_cached_intent(shop_domain: str) -> dict | None:
    """Return cached payload or None on miss/error. Observed via
    record_silent_return so cache-degradation surfaces in metrics."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("abandoned_intent.cache.get.no_client")
            return None
        cached = rc.get(_cache_key_for(shop_domain))
        return json.loads(cached) if cached else None
    except Exception as exc:
        log.warning("abandoned_intent: redis cache read failed: %s", exc)
        record_silent_return("abandoned_intent.cache.get.exception")
        return None


def _save_cached_intent(shop_domain: str, result: dict) -> None:
    """Best-effort cache write — never raises. Observed via
    record_silent_return so cache-degradation surfaces in metrics."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("abandoned_intent.cache.set.no_client")
            return
        rc.setex(_cache_key_for(shop_domain), _CACHE_TTL,
                 json.dumps(result, default=str))
    except Exception as exc:
        log.warning("abandoned_intent: redis cache write failed: %s", exc)
        record_silent_return("abandoned_intent.cache.set.exception")


def _fetch_events(db: Session, shop_domain: str, cutoff: datetime) -> list:
    """7-day event fetch — visitor_id ordered + timestamp ordered."""
    return db.execute(
        _EVENTS_SQL, {"shop": shop_domain, "cutoff": cutoff},
    ).fetchall()


def _resolve_currency(db: Session, shop_domain: str) -> str:
    """USD fallback on any lookup failure — never raises.

    Observed via record_silent_return so a spike in currency
    resolution failures (broken revenue_metrics import, shop_currency
    table degradation) surfaces in metrics instead of hiding behind
    a sea of USD-by-default responses.
    """
    try:
        from app.services.revenue_metrics import get_shop_currency
        return get_shop_currency(db, shop_domain) or "USD"
    except Exception as exc:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("abandoned_intent.resolve_currency")
        log.warning("abandoned_intent: currency lookup failed: %s", exc)
        # data-truth-allowed: except-block last-resort fallback; failure surfaces via record_silent_return metric
        return "USD"


def _empty_intent_response(
    shop_domain: str, currency: str, now: datetime,
) -> dict:
    return {
        "shop_domain": shop_domain,
        "products": [],
        "total_products_count": 0,
        "session_insights": {},
        "headline": "Insufficient data for intent analysis.",
        "currency": currency,
        "generated_at": now.isoformat(),
    }


def _group_events_by_visitor(rows: list) -> dict[str, list]:
    """SQL rows → {visitor_id: [event dicts]}. Order preserved (rows
    are pre-sorted by visitor_id, timestamp in the SQL)."""
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        out[r[0]].append({
            "event_type": r[1],
            "product_url": r[2] or "",
            "timestamp": r[3],
        })
    return out


def _split_into_sessions(events: list) -> list[list]:
    """30-min gap → new session. Caller guarantees events is non-empty
    and timestamp-ordered."""
    sessions = []
    current = [events[0]]
    for e in events[1:]:
        if e["timestamp"] - current[-1]["timestamp"] > _SESSION_GAP_MS:
            sessions.append(current)
            current = [e]
        else:
            current.append(e)
    sessions.append(current)
    return sessions


def _classify_leak(view_to_cart: float, cart_to_purchase: float) -> tuple[str, str]:
    """Determine WHERE intent is dying for a product."""
    if view_to_cart < 5:
        return "browse_to_cart", "Visitors view but don't add to cart"
    if cart_to_purchase < 30:
        return "cart_to_purchase", "Added to cart but not purchased"
    return "none", "Funnel is healthy"


def _build_product_record(
    purl: str, ps: dict, exit_count: int,
) -> dict:
    """One per-product analysis record. Includes leak classification +
    abandon rate + cart-abandoner count."""
    view_to_cart = (ps["carts"] / ps["views"] * 100) if ps["views"] > 0 else 0
    cart_to_purchase = (ps["purchases"] / ps["carts"] * 100) if ps["carts"] > 0 else 0
    abandon_rate = 100 - (ps["purchases"] / ps["views"] * 100) if ps["views"] > 0 else 100
    leak, leak_label = _classify_leak(view_to_cart, cart_to_purchase)
    return {
        "product_url": purl,
        "product_name": _humanize_url(purl),
        "views_7d": ps["views"],
        "carts_7d": ps["carts"],
        "purchases_7d": ps["purchases"],
        "view_to_cart_pct": round(view_to_cart, 1),
        "cart_to_purchase_pct": round(cart_to_purchase, 1),
        "abandon_rate_pct": round(abandon_rate, 1),
        "exit_sessions": exit_count,
        "leak_point": leak,
        "leak_label": leak_label,
        "unique_viewers": (
            len(ps["view_only_visitors"])
            + len(ps["cart_abandon_visitors"])
            + len(ps["buyer_visitors"])
        ),
        "cart_abandoners": len(ps["cart_abandon_visitors"]),
    }


class _SessionAccumulator(NamedTuple):
    """Self-documenting return type for _accumulate_session_stats.

    Promoted from 6-tuple → NamedTuple 2026-05-13 (post-A3 polish):
    field access by name (`acc.product_stats`) is what a top-1 CTO
    would write — positional destructuring breaks silently when a
    future field is added in the wrong slot. The NamedTuple keeps
    backward-compatible positional access AND adds field validation
    at every callsite.
    """
    product_stats: dict[str, dict]
    exit_products: dict[str, int]
    buyer_session_lengths: list[int]
    nonbuyer_session_lengths: list[int]
    buyer_products_viewed: list[int]
    nonbuyer_products_viewed: list[int]


def _accumulate_session_stats(
    visitor_events: dict[str, list],
) -> _SessionAccumulator:
    """Walk every visitor session and accumulate:
      - product_stats[purl]: views/carts/purchases + 3 visitor sets
      - exit_products[purl]: count of non-buying sessions exiting on it
      - buyer/nonbuyer session_lengths (event counts)
      - buyer/nonbuyer products_viewed (unique URL counts)
    Returns a _SessionAccumulator NamedTuple with field-named access.
    """
    product_stats: dict[str, dict] = defaultdict(lambda: {
        "views": 0, "carts": 0, "purchases": 0,
        "view_only_visitors": set(),
        "cart_abandon_visitors": set(),
        "buyer_visitors": set(),
        "last_viewed_before_exit": 0,
    })
    exit_products: dict[str, int] = defaultdict(int)
    buyer_session_lengths: list[int] = []
    nonbuyer_session_lengths: list[int] = []
    buyer_products_viewed: list[int] = []
    nonbuyer_products_viewed: list[int] = []

    for vid, events in visitor_events.items():
        for session in _split_into_sessions(events):
            event_types = {e["event_type"] for e in session}
            products_viewed = [
                e["product_url"] for e in session
                if e["event_type"] == "product_view" and e["product_url"]
            ]
            products_carted = {
                e["product_url"] for e in session
                if e["event_type"] == "add_to_cart" and e["product_url"]
            }
            is_buyer = "purchase" in event_types
            unique_products = list(dict.fromkeys(products_viewed))

            if is_buyer:
                buyer_session_lengths.append(len(session))
                buyer_products_viewed.append(len(unique_products))
            else:
                nonbuyer_session_lengths.append(len(session))
                nonbuyer_products_viewed.append(len(unique_products))

            if not is_buyer and products_viewed:
                exit_products[products_viewed[-1]] += 1

            for purl in set(products_viewed):
                ps = product_stats[purl]
                ps["views"] += 1
                if is_buyer:
                    ps["buyer_visitors"].add(vid)
                elif purl in products_carted:
                    ps["cart_abandon_visitors"].add(vid)
                else:
                    ps["view_only_visitors"].add(vid)

            for purl in products_carted:
                product_stats[purl]["carts"] += 1
                if is_buyer:
                    product_stats[purl]["purchases"] += 1

    return _SessionAccumulator(
        product_stats=product_stats,
        exit_products=exit_products,
        buyer_session_lengths=buyer_session_lengths,
        nonbuyer_session_lengths=nonbuyer_session_lengths,
        buyer_products_viewed=buyer_products_viewed,
        nonbuyer_products_viewed=nonbuyer_products_viewed,
    )


def _build_products_list(
    product_stats: dict, exit_products: dict,
) -> tuple[list[dict], int]:
    """Filter products with >=3 views, sort by opportunity score, cap
    at _MAX_PRODUCTS. Returns (capped_list, true_leak_count_pre_cap).

    The pre-cap count is preserved separately because the UI's
    'Products leaking intent: N' drawer stat depends on it staying
    honest even when the visible list is truncated.
    """
    products: list[dict] = []
    for purl, ps in product_stats.items():
        if ps["views"] < 3:
            continue
        products.append(_build_product_record(purl, ps, exit_products.get(purl, 0)))

    products.sort(
        key=lambda p: p["views_7d"] * (p["abandon_rate_pct"] / 100),
        reverse=True,
    )
    true_leak_count = len(products)
    return products[:_MAX_PRODUCTS], true_leak_count


def _build_session_insights(
    *,
    exit_products: dict,
    buyer_session_lengths: list[int],
    nonbuyer_session_lengths: list[int],
    buyer_products_viewed: list[int],
    nonbuyer_products_viewed: list[int],
) -> dict:
    """Buyer vs non-buyer session shape + top-5 exit products."""
    def _avg(xs: list) -> float:
        return (sum(xs) / len(xs)) if xs else 0.0

    top_exits = sorted(exit_products.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "buyer_avg_events": round(_avg(buyer_session_lengths), 1),
        "nonbuyer_avg_events": round(_avg(nonbuyer_session_lengths), 1),
        "buyer_avg_products_viewed": round(_avg(buyer_products_viewed), 1),
        "nonbuyer_avg_products_viewed": round(_avg(nonbuyer_products_viewed), 1),
        "total_buyer_sessions": len(buyer_session_lengths),
        "total_nonbuyer_sessions": len(nonbuyer_session_lengths),
        "top_exit_products": [
            {"product_url": url, "product_name": _humanize_url(url), "exit_count": cnt}
            for url, cnt in top_exits
        ],
    }


def _build_intent_headline(products: list[dict]) -> str:
    if not products:
        return "Not enough data to identify abandoned intent patterns."
    worst = products[0]
    return (
        f"{worst['product_name']} has the highest abandoned intent: "
        f"{worst['views_7d']} views, {worst['abandon_rate_pct']:.0f}% abandon rate. "
        f"Leak point: {worst['leak_label'].lower()}."
    )


def compute_abandoned_intent(db: Session, shop_domain: str, plan: str = "pro") -> dict:
    """
    Compute abandoned intent analysis for the shop.

    Returns product-level abandon metrics + session path insights.

    Plan-aware response:
      plan = "pro"  → full product list (top 15) + session_insights
      plan != "pro" → top 3 products only, session_insights redacted
                      to {} (upgrade bridge in the Lite UI shows
                      what Pro unlocks). Hero count and headline stay
                      identical across tiers so the Lite merchant
                      still understands the scale of the leak.

    Refactored 2026-05-13 (A3 close): 246-LOC god function → 25-LOC
    composer + 12 pure helpers.
    """
    cache_hit = _load_cached_intent(shop_domain)
    if cache_hit is not None:
        return _apply_plan_filter(cache_hit, plan)

    now = _now()
    cutoff = now - timedelta(days=7)
    rows = _fetch_events(db, shop_domain, cutoff)
    currency = _resolve_currency(db, shop_domain)

    if not rows:
        return _apply_plan_filter(
            _empty_intent_response(shop_domain, currency, now), plan,
        )

    visitor_events = _group_events_by_visitor(rows)
    acc = _accumulate_session_stats(visitor_events)
    products, true_leak_count = _build_products_list(
        acc.product_stats, acc.exit_products,
    )

    result = {
        "shop_domain": shop_domain,
        "products": products,
        # true_leak_count is the pre-slice count (before _MAX_PRODUCTS
        # and before the Lite top-3 filter). Used by the drawer's
        # "Products leaking intent: N" stat to stay honest about scale
        # even when the list is truncated.
        "total_products_count": true_leak_count,
        "session_insights": _build_session_insights(
            exit_products=acc.exit_products,
            buyer_session_lengths=acc.buyer_session_lengths,
            nonbuyer_session_lengths=acc.nonbuyer_session_lengths,
            buyer_products_viewed=acc.buyer_products_viewed,
            nonbuyer_products_viewed=acc.nonbuyer_products_viewed,
        ),
        "headline": _build_intent_headline(products),
        "currency": currency,
        "generated_at": now.isoformat(),
    }

    _save_cached_intent(shop_domain, result)
    return _apply_plan_filter(result, plan)


def _apply_plan_filter(result: dict, plan: str) -> dict:
    """Reduce Abandoned Intent response fidelity for non-Pro tiers.

    Pro: full product list (up to _MAX_PRODUCTS) + session_insights.
    Lite: top 3 products only + session_insights redacted to
    {} — the upgrade bridge in the UI lists what Pro unlocks.

    Shallow-copies so we don't mutate a shared cached dict.
    """
    if plan == "pro":
        return result
    filtered = dict(result)
    filtered["products"] = list(result.get("products", []))[:_LITE_PRODUCT_CAP]
    filtered["session_insights"] = {}
    return filtered
