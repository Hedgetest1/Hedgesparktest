"""
refund_ingest.py — Shopify refund webhook persistence.

Consumes `refunds/create` webhook payloads and stores per-product refund
rows in Redis keyed by shop. Keeps a 90-day rolling window so
refund_loss.py can prefer real refund data over the order-frequency
proxy when available.

Zero schema migrations: all data lives in Redis under
`hs:refunds:v1:{shop}` as a JSON list, capped at 500 entries per shop.

Each stored refund row:
    {
      "refund_id": str,
      "order_id": str,
      "created_at": iso,
      "product_id": str,        # may be ""
      "product_title": str,
      "quantity": int,
      "amount_eur": float,
      "reason": str | None,
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("refund_ingest")

_REDIS_KEY = "hs:refunds:v1"
_TTL_SECONDS = 95 * 24 * 3600
_MAX_REFUNDS_PER_SHOP = 500


def _key(shop: str) -> str:
    return f"{_REDIS_KEY}:{shop}"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _parse_refund_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a Shopify refunds/create payload into per-product rows.

    Shopify payload shape (the parts we care about):
      {
        "id": <refund_id>, "order_id": <order_id>, "created_at": "...",
        "note": "...",
        "refund_line_items": [
          {"line_item": {"product_id":..,"title":..,"price":..}, "quantity":..,
           "subtotal":..},
          ...
        ]
      }
    """
    rows: list[dict[str, Any]] = []
    refund_id = str(payload.get("id") or "")
    order_id = str(payload.get("order_id") or "")
    created_at = str(payload.get("created_at") or _now_iso())
    reason = payload.get("note") or payload.get("reason")

    items = payload.get("refund_line_items") or []
    if not isinstance(items, list) or not items:
        total = float(payload.get("total_refund_set", {}).get("shop_money", {}).get("amount", 0) or 0)
        if total <= 0:
            total = float(payload.get("amount") or 0)
        rows.append({
            "refund_id": refund_id,
            "order_id": order_id,
            "created_at": created_at,
            "product_id": "",
            "product_title": "Order refund (no line items)",
            "quantity": 1,
            "amount_eur": round(total, 2),
            "reason": reason,
        })
        return rows

    for rli in items:
        if not isinstance(rli, dict):
            continue
        li = rli.get("line_item") or {}
        qty = int(rli.get("quantity") or li.get("quantity") or 1)
        subtotal = rli.get("subtotal")
        if subtotal is None:
            price = float(li.get("price") or 0)
            subtotal = price * qty
        try:
            amount = float(subtotal or 0)
        except (TypeError, ValueError):
            amount = 0.0
        rows.append({
            "refund_id": refund_id,
            "order_id": order_id,
            "created_at": created_at,
            "product_id": str(li.get("product_id") or ""),
            "product_title": str(li.get("title") or li.get("name") or "Unknown product")[:200],
            "quantity": qty,
            "amount_eur": round(amount, 2),
            "reason": reason,
        })
    return rows


def ingest_refund(shop_domain: str, payload: dict[str, Any]) -> int:
    """Persist a refund webhook. Returns number of rows stored (0 on failure)."""
    rc = _redis()
    if rc is None:
        return 0

    rows = _parse_refund_rows(payload)
    if not rows:
        return 0

    try:
        raw = rc.get(_key(shop_domain))
        existing: list[dict[str, Any]] = json.loads(raw) if raw else []
        if not isinstance(existing, list):
            existing = []

        seen = {(r.get("refund_id"), r.get("product_id")) for r in existing}
        added = 0
        for row in rows:
            k = (row["refund_id"], row["product_id"])
            if k in seen:
                continue
            existing.append(row)
            seen.add(k)
            added += 1

        if added == 0:
            return 0

        if len(existing) > _MAX_REFUNDS_PER_SHOP:
            existing = existing[-_MAX_REFUNDS_PER_SHOP:]

        rc.setex(_key(shop_domain), _TTL_SECONDS, json.dumps(existing, default=str))
        return added
    except Exception as exc:
        log.warning("refund_ingest: store failed shop=%s: %s", shop_domain, exc)
        return 0


def list_recent_refunds(shop_domain: str, days: int = 28) -> list[dict[str, Any]]:
    """Return refunds from the last N days (default 28), newest first."""
    rc = _redis()
    if rc is None:
        return []
    try:
        raw = rc.get(_key(shop_domain))
        if not raw:
            return []
        rows = json.loads(raw)
        if not isinstance(rows, list):
            return []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                created = datetime.fromisoformat(str(r.get("created_at", "")).replace("Z", ""))
            except Exception:
                continue
            if created >= cutoff:
                out.append(r)
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out
    except Exception:
        return []


def aggregate_product_refunds(
    shop_domain: str, days: int = 28,
) -> dict[str, dict[str, Any]]:
    """Group recent refunds by product_id for refund_loss to consume.

    Returns { product_id: {title, refund_count, refund_qty, refund_eur} }.
    An empty dict means "no real refund data available, fall back to proxy".
    """
    refunds = list_recent_refunds(shop_domain, days=days)
    agg: dict[str, dict[str, Any]] = {}
    for r in refunds:
        pid = r.get("product_id") or "_unknown"
        row = agg.setdefault(pid, {
            "product_id": pid,
            "title": r.get("product_title", "Unknown product"),
            "refund_count": 0,
            "refund_qty": 0,
            "refund_eur": 0.0,
        })
        row["refund_count"] += 1
        row["refund_qty"] += int(r.get("quantity") or 1)
        row["refund_eur"] += float(r.get("amount_eur") or 0)
    for row in agg.values():
        row["refund_eur"] = round(row["refund_eur"], 2)
    return agg
