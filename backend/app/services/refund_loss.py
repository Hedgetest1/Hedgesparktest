"""
refund_loss.py — Product-level loss signal analyzer.

Detects products with declining order momentum (a proxy for refund/return
impact + customer abandonment) and surfaces them with loss-framed copy.

Why proxy?
----------
The existing shop_orders ingestion comes from the pixel, which does NOT
fire on refunds. Real refund data requires the Shopify `refunds/create`
webhook which is not yet ingested (tracked in project_competitive_feature_roadmap.md
as F2 v2 work). For v1 we use the strongest proxy we have: products
whose order frequency is declining sharply.

The API contract is future-proof: when real refund data arrives, only
the underlying signal computation changes — the API response shape stays
identical.

Self-healing integration
------------------------
* ops_alert on compute failure (source='refund_loss')
* cached via Redis 3h per shop
* exposed via /pro/refund-losses
* data_integrity_probe (existing) watches for AOV drift which overlaps
  with refund-driven loss
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("refund_loss")

_CACHE_TTL_SECONDS = 3 * 3600
_CACHE_KEY_PREFIX = "hs:refund_loss:v1"
_MIN_ORDERS_FOR_SIGNAL = 3  # need at least 3 orders to compute momentum
_MAX_ROWS_RETURNED = 10


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _extract_product_rows(raw_line_items: list) -> list[dict]:
    """Normalize shop_orders.line_items JSONB into (product_id, title, price)."""
    out = []
    if not isinstance(raw_line_items, list):
        return out
    for li in raw_line_items:
        if not isinstance(li, dict):
            continue
        out.append({
            "product_id": str(li.get("product_id") or li.get("product_handle") or ""),
            "title": str(li.get("title") or li.get("name") or "Unknown product"),
            "price": float(li.get("price") or 0),
            "quantity": int(li.get("quantity") or 1),
        })
    return out


def _compute_product_loss_signals(db: Session, shop_domain: str) -> list[dict]:
    """
    Scan the shop's last 28 days of orders and identify products whose
    order frequency declined from the prior 14d to the recent 14d window.
    Returns a list of signal rows sorted by loss_eur desc.
    """
    now = _now()
    recent_cut = now - timedelta(days=14)
    prior_cut = now - timedelta(days=28)

    rows = db.execute(text("""
        SELECT shopify_order_id, total_price, line_items, created_at
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :prior_cut
        ORDER BY created_at DESC
    """), {
        "shop": shop_domain,
        "prior_cut": prior_cut,
    }).fetchall()

    # Aggregate per product across the two windows
    per_product: dict[str, dict] = {}
    for r in rows:
        order_price = float(r[1] or 0)
        raw_items = r[2] or []
        created = r[3]
        items = _extract_product_rows(raw_items)

        # If no line items, attribute the whole order to a synthetic
        # "_whole_order" bucket so at least some signal surfaces.
        if not items:
            items = [{
                "product_id": "_whole_order",
                "title": "Order (pixel ingestion, no line items)",
                "price": order_price,
                "quantity": 1,
            }]

        for it in items:
            pid = it["product_id"]
            is_recent = created >= recent_cut
            agg = per_product.setdefault(pid, {
                "product_id": pid,
                "title": it["title"],
                "orders_recent": 0,
                "orders_prior": 0,
                "revenue_recent": 0.0,
                "revenue_prior": 0.0,
            })
            if is_recent:
                agg["orders_recent"] += it["quantity"]
                agg["revenue_recent"] += it["price"] * it["quantity"]
            else:
                agg["orders_prior"] += it["quantity"]
                agg["revenue_prior"] += it["price"] * it["quantity"]

    # Compute decline signals
    signals: list[dict] = []
    for pid, agg in per_product.items():
        total_orders = agg["orders_recent"] + agg["orders_prior"]
        if total_orders < _MIN_ORDERS_FOR_SIGNAL:
            continue
        if agg["orders_prior"] == 0:
            continue  # can't compute decline without prior data

        decline_pct = (
            (agg["orders_prior"] - agg["orders_recent"]) / agg["orders_prior"] * 100
        )
        if decline_pct <= 10:  # only surface meaningful declines
            continue

        # Monthly normalization — 14d windows * 2.17 to project per-month impact
        revenue_loss = (agg["revenue_prior"] - agg["revenue_recent"]) * (30 / 14)
        if revenue_loss <= 0:
            continue

        avg_price_recent = (
            agg["revenue_recent"] / agg["orders_recent"]
            if agg["orders_recent"] > 0 else 0
        )
        avg_price_prior = agg["revenue_prior"] / agg["orders_prior"]

        reason = "order_frequency_decline"
        if avg_price_recent > 0 and avg_price_recent < avg_price_prior * 0.9:
            reason = "order_frequency_decline_plus_price_drop"

        signals.append({
            "product_title": agg["title"][:120],
            "product_id": pid if pid != "_whole_order" else None,
            "orders_recent_14d": agg["orders_recent"],
            "orders_prior_14d": agg["orders_prior"],
            "avg_price_recent": round(avg_price_recent, 2),
            "avg_price_prior": round(avg_price_prior, 2),
            "revenue_recent_14d": round(agg["revenue_recent"], 2),
            "revenue_prior_14d": round(agg["revenue_prior"], 2),
            "loss_eur": round(revenue_loss, 2),
            "decline_pct": round(decline_pct, 1),
            "reason": reason,
        })

    signals.sort(key=lambda s: s["loss_eur"], reverse=True)
    return signals[:_MAX_ROWS_RETURNED]


def _merge_real_refund_data(shop_domain: str, signals: list[dict]) -> list[dict]:
    """Prefer real Shopify refund data over the order-frequency proxy.

    If the shop has ingested any refunds via the shopify/refunds webhook,
    each matching product gets its `loss_eur` replaced by the 28d → 30d
    monthly-normalized real refund total, and `reason` is flagged as
    `real_shopify_refund`. Products with refunds but no proxy signal are
    appended. Net effect: when we have real data, we use it; when we
    don't, the proxy still works.
    """
    try:
        from app.services.refund_ingest import aggregate_product_refunds
        agg = aggregate_product_refunds(shop_domain, days=28)
    except Exception:
        return signals

    if not agg:
        return signals

    by_pid = {s.get("product_id"): s for s in signals if s.get("product_id")}

    for pid, row in agg.items():
        if pid in ("", "_unknown"):
            continue
        monthly = round(float(row["refund_eur"]) * (30 / 28), 2)
        if monthly <= 0:
            continue
        if pid in by_pid:
            by_pid[pid]["loss_eur"] = monthly
            by_pid[pid]["reason"] = "real_shopify_refund"
            by_pid[pid]["refund_count_28d"] = row["refund_count"]
            by_pid[pid]["refund_qty_28d"] = row["refund_qty"]
        else:
            signals.append({
                "product_title": row["title"][:120],
                "product_id": pid,
                "orders_recent_14d": 0,
                "orders_prior_14d": 0,
                "avg_price_recent": 0.0,
                "avg_price_prior": 0.0,
                "revenue_recent_14d": 0.0,
                "revenue_prior_14d": 0.0,
                "loss_eur": monthly,
                "decline_pct": 0.0,
                "reason": "real_shopify_refund",
                "refund_count_28d": row["refund_count"],
                "refund_qty_28d": row["refund_qty"],
            })

    signals.sort(key=lambda s: s["loss_eur"], reverse=True)
    return signals[:_MAX_ROWS_RETURNED]


def get_refund_loss_report(db: Session, shop_domain: str) -> dict:
    """
    Return the full refund/loss report for the merchant. Cached 3h.

    Returns a dict suitable for BuildingModel.model_validate:
      {
        shop_domain, total_loss_eur_per_month, product_count,
        products: [ProductLossRow...], generated_at, method, headline
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
    except Exception:
        pass

    try:
        signals = _compute_product_loss_signals(db, shop_domain)
        signals = _merge_real_refund_data(shop_domain, signals)
    except Exception as exc:
        log.warning("refund_loss: compute failed shop=%s: %s", shop_domain, exc)
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="refund_loss",
                alert_type="refund_loss_compute_failed",
                summary=f"Refund loss compute failed for shop {shop_domain}: {type(exc).__name__}",
                shop_domain=shop_domain,
                detail={"error": str(exc)[:500]},
            )
        except Exception:
            pass
        return {
            "shop_domain": shop_domain,
            "error": "compute_failed",
            "products": [],
            "total_loss_eur_per_month": 0.0,
            "product_count": 0,
        }

    total_loss = sum(s["loss_eur"] for s in signals)

    if not signals:
        headline = (
            "No significant product loss signals in the last 28 days. "
            "Product order momentum is stable."
        )
    elif total_loss >= 500:
        headline = (
            f"⚠️ {len(signals)} products are losing momentum — "
            f"projected loss €{total_loss:.0f}/month if the decline continues."
        )
    else:
        headline = (
            f"{len(signals)} products showing early decline — "
            f"projected loss €{total_loss:.0f}/month."
        )

    result = {
        "shop_domain": shop_domain,
        "total_loss_eur_per_month": round(total_loss, 2),
        "product_count": len(signals),
        "products": signals,
        "generated_at": _now().isoformat(),
        "method": "order_frequency_decline_proxy_v1",
        "headline": headline,
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
    except Exception:
        pass

    return result
