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

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("abandoned_intent")

_CACHE_TTL = 3 * 3600
_CACHE_PREFIX = "hs:intent:v1"
_SESSION_GAP_MS = 30 * 60 * 1000  # 30 min gap = new session


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _humanize_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return slug.replace("-", " ").replace("_", " ").title() or url


def compute_abandoned_intent(db: Session, shop_domain: str) -> dict:
    """
    Compute abandoned intent analysis for the shop.

    Returns product-level abandon metrics + session path insights.
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
        log.warning("abandoned_intent: redis cache read failed: %s", exc)

    now = _now()
    cutoff = now - timedelta(days=7)

    # Fetch all events for the shop in the last 7 days
    rows = db.execute(text("""
        SELECT visitor_id, event_type, product_url, timestamp
        FROM events
        WHERE shop_domain = :shop
          AND to_timestamp(timestamp/1000) >= :cutoff
          AND event_type IN ('product_view', 'add_to_cart', 'checkout', 'purchase')
        ORDER BY visitor_id, timestamp
    """), {"shop": shop_domain, "cutoff": cutoff}).fetchall()

    if not rows:
        return {
            "shop_domain": shop_domain,
            "products": [],
            "session_insights": {},
            "headline": "Insufficient data for intent analysis.",
            "generated_at": now.isoformat(),
        }

    # --- Build visitor sessions ---
    visitor_events: dict[str, list] = defaultdict(list)
    for r in rows:
        visitor_events[r[0]].append({
            "event_type": r[1],
            "product_url": r[2] or "",
            "timestamp": r[3],
        })

    # --- Per-product analysis ---
    product_stats: dict[str, dict] = defaultdict(lambda: {
        "views": 0, "carts": 0, "purchases": 0,
        "view_only_visitors": set(),
        "cart_abandon_visitors": set(),
        "buyer_visitors": set(),
        "last_viewed_before_exit": 0,  # how often this was the exit product
    })

    # --- Session path analysis ---
    buyer_session_lengths = []
    nonbuyer_session_lengths = []
    buyer_products_viewed = []
    nonbuyer_products_viewed = []
    exit_products: dict[str, int] = defaultdict(int)

    for vid, events in visitor_events.items():
        # Split into sessions by 30-min gap
        sessions = []
        current_session = [events[0]]
        for e in events[1:]:
            if e["timestamp"] - current_session[-1]["timestamp"] > _SESSION_GAP_MS:
                sessions.append(current_session)
                current_session = [e]
            else:
                current_session.append(e)
        sessions.append(current_session)

        for session in sessions:
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

            # Track exit product (last product viewed in non-buying session)
            if not is_buyer and products_viewed:
                exit_url = products_viewed[-1]
                exit_products[exit_url] += 1

            # Per-product stats
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

    # --- Build product reports ---
    products = []
    for purl, ps in product_stats.items():
        if ps["views"] < 3:
            continue

        view_to_cart = (ps["carts"] / ps["views"] * 100) if ps["views"] > 0 else 0
        cart_to_purchase = (ps["purchases"] / ps["carts"] * 100) if ps["carts"] > 0 else 0
        abandon_rate = 100 - (ps["purchases"] / ps["views"] * 100) if ps["views"] > 0 else 100
        exit_count = exit_products.get(purl, 0)

        # Determine the "leak" — where is intent dying?
        if view_to_cart < 5:
            leak = "browse_to_cart"
            leak_label = "Visitors view but don't add to cart"
        elif cart_to_purchase < 30:
            leak = "cart_to_purchase"
            leak_label = "Added to cart but not purchased"
        else:
            leak = "none"
            leak_label = "Funnel is healthy"

        products.append({
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
            "unique_viewers": len(ps["view_only_visitors"]) + len(ps["cart_abandon_visitors"]) + len(ps["buyer_visitors"]),
            "cart_abandoners": len(ps["cart_abandon_visitors"]),
        })

    # Sort by opportunity: high views + high abandon = highest opportunity
    products.sort(key=lambda p: p["views_7d"] * (p["abandon_rate_pct"] / 100), reverse=True)
    products = products[:_MAX_PRODUCTS]

    # --- Session insights ---
    avg_buyer_length = (sum(buyer_session_lengths) / len(buyer_session_lengths)) if buyer_session_lengths else 0
    avg_nonbuyer_length = (sum(nonbuyer_session_lengths) / len(nonbuyer_session_lengths)) if nonbuyer_session_lengths else 0
    avg_buyer_products = (sum(buyer_products_viewed) / len(buyer_products_viewed)) if buyer_products_viewed else 0
    avg_nonbuyer_products = (sum(nonbuyer_products_viewed) / len(nonbuyer_products_viewed)) if nonbuyer_products_viewed else 0

    # Top exit products
    top_exits = sorted(exit_products.items(), key=lambda x: x[1], reverse=True)[:5]
    top_exit_list = [
        {"product_url": url, "product_name": _humanize_url(url), "exit_count": cnt}
        for url, cnt in top_exits
    ]

    session_insights = {
        "buyer_avg_events": round(avg_buyer_length, 1),
        "nonbuyer_avg_events": round(avg_nonbuyer_length, 1),
        "buyer_avg_products_viewed": round(avg_buyer_products, 1),
        "nonbuyer_avg_products_viewed": round(avg_nonbuyer_products, 1),
        "total_buyer_sessions": len(buyer_session_lengths),
        "total_nonbuyer_sessions": len(nonbuyer_session_lengths),
        "top_exit_products": top_exit_list,
    }

    # Narrative
    if products:
        worst = products[0]
        headline = (
            f"{worst['product_name']} has the highest abandoned intent: "
            f"{worst['views_7d']} views, {worst['abandon_rate_pct']:.0f}% abandon rate. "
            f"Leak point: {worst['leak_label'].lower()}."
        )
    else:
        headline = "Not enough data to identify abandoned intent patterns."

    result = {
        "shop_domain": shop_domain,
        "products": products,
        "session_insights": session_insights,
        "headline": headline,
        "generated_at": now.isoformat(),
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("abandoned_intent: redis cache write failed: %s", exc)

    return result


_MAX_PRODUCTS = 15
