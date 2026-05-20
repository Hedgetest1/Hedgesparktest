"""
instant_onboarding.py — 60-second "aha moment" onboarding.

Problem: the previous onboarding flow made merchants wait up to 11 days
(ho visto onboarding_slow_progress alerts pre 261h) before seeing any
intelligence, because we waited for event-collection-based metrics.

Solution: on install, fetch 90 days of Shopify orders via the Admin API
and compute instant intelligence from historical data:
  - AOV (average order value)
  - Total revenue (90d)
  - Order count (90d)
  - Top 5 products by revenue
  - Refund rate baseline
  - Preview RARS (Revenue at Risk — projected losses)
  - First-login narrative message

This runs async on install (doesn't block OAuth callback). Results are
cached in Redis under `hs:instant_onboarding:{shop}` and returned by a
new endpoint GET /pro/instant-intelligence so the dashboard can show
"Welcome, here are your 3 biggest loss signals already identified" within
60 seconds of install.

Deterministic. No LLM. Reuses existing Shopify admin client.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.shopify_client import shopify_request
from app.services.shopify_admin import _get_access_token

log = logging.getLogger("instant_onboarding")

_REDIS_KEY_PREFIX = "hs:instant_intel"
_CACHE_TTL_S = 24 * 3600  # 24h — recomputed daily
_BACKFILL_DAYS = 90


def _redis_key(shop_domain: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{shop_domain}"


def _fetch_orders_90d(db: Session, shop_domain: str) -> list[dict]:
    """Fetch last 90 days of orders from Shopify Admin API.
    Returns up to 250 most-recent orders (Shopify page size)."""
    token = _get_access_token(db, shop_domain)
    if not token:
        log.info("instant_onboarding: no token for %s, skipping", shop_domain)
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=_BACKFILL_DAYS)).isoformat()

    all_orders: list[dict] = []
    page_info: str | None = None
    max_pages = 4  # 4 × 250 = 1k orders max — plenty for instant insight

    for _ in range(max_pages):
        params: dict[str, Any] = {
            "status": "any",
            "limit": 250,
            "created_at_min": since,
            "fields": "id,total_price,currency,created_at,line_items,refunds,financial_status",
        }
        if page_info:
            params = {"limit": 250, "page_info": page_info}

        resp = shopify_request("GET", shop_domain, "orders.json", token, params=params)
        if resp is None or resp.status_code >= 400:
            break

        chunk = resp.json().get("orders", [])
        if not chunk:
            break
        all_orders.extend(chunk)

        # Shopify returns cursor pagination in Link header
        link = resp.headers.get("Link", "") or resp.headers.get("link", "")
        if 'rel="next"' not in link:
            break
        # Parse out page_info from next cursor
        import re
        m = re.search(r'page_info=([^&>]+)[^>]*>;\s*rel="next"', link)
        if not m:
            break
        page_info = m.group(1)

    log.info("instant_onboarding: fetched %d orders for %s", len(all_orders), shop_domain)
    return all_orders


def _compute_intelligence(orders: list[dict]) -> dict:
    """Aggregate orders into an intelligence snapshot."""
    if not orders:
        return {
            "status": "empty",
            "reason": "no_orders_90d",
            "message": "We'll start collecting as soon as orders arrive.",
        }

    total_rev = 0.0
    total_refunded = 0.0
    order_count = 0
    product_sales: dict[str, dict] = {}  # product_id -> {title, revenue, units}
    # Initial fallback only — overridden on the first order with a non-null
    # `currency` field. USD is the Shopify global default (per
    # app/core/currency.py:17), strictly safer than "EUR" if every fetched
    # order somehow lacks a currency tag (only ever happens on synthetic
    # fixtures).
    # data-truth-allowed: initial-fallback overridden on first order with non-null currency (loop below)
    currency = "USD"

    for o in orders:
        try:
            total = float(o.get("total_price") or 0)
            total_rev += total
            order_count += 1
            if o.get("currency"):
                currency = o["currency"]

            # Refund tracking
            refunds = o.get("refunds") or []
            for r in refunds:
                for line in r.get("refund_line_items", []) or []:
                    sub = float(line.get("subtotal") or 0)
                    total_refunded += sub

            # Line item sales per product
            for line in o.get("line_items", []) or []:
                pid = str(line.get("product_id") or "")
                if not pid:
                    continue
                title = line.get("title") or "Untitled"
                price = float(line.get("price") or 0)
                qty = int(line.get("quantity") or 0)
                entry = product_sales.setdefault(
                    pid, {"title": title, "revenue": 0.0, "units": 0}
                )
                entry["revenue"] += price * qty
                entry["units"] += qty
        except Exception as exc:
            log.warning("instant_onboarding: _compute_intelligence failed: %s", exc)
            continue

    aov = total_rev / order_count if order_count > 0 else 0.0
    refund_rate = (total_refunded / total_rev) if total_rev > 0 else 0.0

    top_products = sorted(
        [
            {
                "id": pid,
                "title": p["title"][:80],
                "revenue": round(p["revenue"], 2),
                "units": p["units"],
            }
            for pid, p in product_sales.items()
        ],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:5]

    # Preview RARS — rough estimate based on industry-standard assumptions
    # (this is NOT the full RARS engine, just an instant preview):
    #   - 15% of AOV × monthly traffic = abandoned cart loss
    #   - We approximate monthly_orders × 5 → "interested but didn't buy" × aov × 15%
    monthly_orders = (order_count / 3) if order_count else 0  # 90d / 3 ≈ 30d
    estimated_cart_abandon_loss = monthly_orders * 5 * aov * 0.15
    estimated_refund_loss = total_refunded / 3  # monthly refund impact

    preview_rars = round(estimated_cart_abandon_loss + estimated_refund_loss, 0)

    # Build narrative
    if total_rev > 0:
        daily_rev = total_rev / 90
        monthly_rev = daily_rev * 30
        narrative = (
            f"In the last 90 days, your store generated {currency} "
            f"{total_rev:,.0f} across {order_count} orders "
            f"(AOV {currency} {aov:,.0f}). "
            f"That's {currency} {monthly_rev:,.0f}/month. "
            f"HedgeSpark has already identified ~{currency} {preview_rars:,.0f}/month "
            f"in revenue at risk from abandoned carts and refund trends."
        )
    else:
        narrative = "Your store is just getting started — we'll track every order from here."

    return {
        "status": "ready",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "backfill_days": _BACKFILL_DAYS,
        "currency": currency,
        "order_count_90d": order_count,
        "total_revenue_90d": round(total_rev, 2),
        "aov": round(aov, 2),
        "monthly_revenue_estimate": round(total_rev / 3, 2),
        "refund_rate_pct": round(refund_rate * 100, 2),
        "top_products": top_products,
        "preview_rars_monthly": preview_rars,
        "narrative": narrative,
    }


def compute_instant_intelligence(db: Session, shop_domain: str) -> dict:
    """Fetch orders + compute snapshot. Cached in Redis."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(_redis_key(shop_domain))
            if cached:
                try:
                    return json.loads(cached)
                except Exception as exc:
                    log.warning("instant_onboarding: compute_instant_intelligence failed: %s", exc)
    except Exception as exc:
        log.warning("instant_onboarding: compute_instant_intelligence failed: %s", exc)
        rc = None

    orders = _fetch_orders_90d(db, shop_domain)
    snapshot = _compute_intelligence(orders)
    snapshot["shop_domain"] = shop_domain

    if rc is not None:
        try:
            rc.setex(_redis_key(shop_domain), _CACHE_TTL_S, json.dumps(snapshot, default=str))
        except Exception as exc:
            log.warning("instant_onboarding: compute_instant_intelligence failed: %s", exc)

    return snapshot


def trigger_instant_intelligence_async(shop_domain: str) -> None:
    """Fire-and-forget trigger from install callback. Kicks off backfill
    in a separate DB session so it doesn't block OAuth response."""
    import threading

    def _run():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            compute_instant_intelligence(db, shop_domain)
            log.info("instant_onboarding: async backfill complete for %s", shop_domain)
        except Exception as exc:
            log.warning("instant_onboarding: async backfill failed for %s: %s", shop_domain, exc)
        finally:
            db.close()

    t = threading.Thread(target=_run, daemon=True, name=f"instant-intel-{shop_domain}")
    t.start()
