"""
rfm.py — RFM (Recency, Frequency, Monetary) customer segmentation.

G2 Lite parity gap close (2026-04-29). Putler $20, Glew (free tier),
Mipler ship 11-named-segment RFM at entry tier; HedgeSpark Lite €39
matches with this deterministic compute.

Method
------
- Recency: days since customer's last order (lower = more recent)
- Frequency: total orders by customer
- Monetary: total revenue from customer (in shop's primary_currency)

Each customer scored 1-5 on each axis via QUINTILES of the shop's
own customer base (n=1 customer → score=5 on every axis; n=5 → 5
quintiles of 1 each). Quintile-based, NOT global thresholds, so a
small store and a large store both produce a usable segmentation.

The 11 named segments (Putler convention):
  Champions, Loyal, Potential Loyalists, New Customers, Promising,
  Need Attention, About to Sleep, At Risk, Can't Lose Them,
  Hibernating, Lost

Cached 5min per shop in Redis (`hs:rfm:v1:{shop}`).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "hs:rfm:v1"
_CACHE_TTL_S = 5 * 60

# Each segment's positioning in the (R,F,M) cube — coarse R + FM cells.
# R in 1..5 (5 = most recent), FM_avg = (F + M) / 2 in 1..5.
# We classify on R bucket × FM bucket → 11 unique cells.
_SEGMENT_NAMES: dict[tuple[int, int], str] = {
    # (R_bucket, FM_bucket): segment_name
    # R buckets: 5=very recent, 4=recent, 3=neutral, 2=lapsing, 1=lost
    # FM buckets: 5=very high value, 4=high, 3=mid, 2=low, 1=very low
    (5, 5): "Champions",
    (5, 4): "Champions",
    (5, 3): "Loyal",
    (5, 2): "Potential Loyalists",
    (5, 1): "New Customers",
    (4, 5): "Loyal",
    (4, 4): "Loyal",
    (4, 3): "Potential Loyalists",
    (4, 2): "Promising",
    (4, 1): "New Customers",
    (3, 5): "Need Attention",
    (3, 4): "Need Attention",
    (3, 3): "Need Attention",
    (3, 2): "About to Sleep",
    (3, 1): "About to Sleep",
    (2, 5): "Can't Lose Them",
    (2, 4): "At Risk",
    (2, 3): "At Risk",
    (2, 2): "Hibernating",
    (2, 1): "Hibernating",
    (1, 5): "Can't Lose Them",
    (1, 4): "At Risk",
    (1, 3): "Hibernating",
    (1, 2): "Lost",
    (1, 1): "Lost",
}

# Order matters for UI display + Spark Action priority.
SEGMENT_ORDER = [
    "Champions",
    "Loyal",
    "Potential Loyalists",
    "New Customers",
    "Promising",
    "Need Attention",
    "About to Sleep",
    "At Risk",
    "Can't Lose Them",
    "Hibernating",
    "Lost",
]

# Short, idiot-proof copy per segment (§5 storytelling rules).
SEGMENT_COPY: dict[str, str] = {
    "Champions": "Your best buyers. Keep them happy.",
    "Loyal": "Steady regulars who buy often.",
    "Potential Loyalists": "Recent buyers with promise.",
    "New Customers": "Bought recently, not yet repeat.",
    "Promising": "Recent first-timers worth nurturing.",
    "Need Attention": "Used to buy more — re-engage.",
    "About to Sleep": "Slowing down — pull them back.",
    "At Risk": "High value but going quiet.",
    "Can't Lose Them": "Big spenders haven't bought lately.",
    "Hibernating": "Long-quiet, lower-value customers.",
    "Lost": "Inactive — likely gone.",
}


def _quintile(value: float, sorted_values: list[float], reverse: bool = False) -> int:
    """Return 1..5 quintile rank of `value` in `sorted_values`.

    `reverse=True` for recency (lower days = better = 5).
    Edge case: empty list → 1. Single value → 5.
    """
    n = len(sorted_values)
    if n == 0:
        return 1
    if n == 1:
        return 5
    # Find rank position (0-indexed)
    rank = 0
    for v in sorted_values:
        if reverse:
            if v < value:
                rank += 1
        else:
            if v < value:
                rank += 1
    pct = rank / n
    if pct < 0.2:
        score = 1
    elif pct < 0.4:
        score = 2
    elif pct < 0.6:
        score = 3
    elif pct < 0.8:
        score = 4
    else:
        score = 5
    if reverse:
        # Invert so most-recent = 5, oldest = 1
        score = 6 - score
    return score


def _safe_email_hash(email: str) -> str:
    """Compact non-PII tag for an email — 8-hex prefix of sha1, never the
    raw value. Used for sample-customer lists in API responses so the
    merchant gets a per-row identifier without HedgeSpark exposing PII."""
    import hashlib
    return "cust_" + hashlib.sha1(email.encode("utf-8")).hexdigest()[:8]


def compute_rfm_segments(
    db: Session,
    shop: str,
    *,
    sample_per_segment: int = 5,
) -> dict:
    """Compute RFM segments for a shop's customer base.

    Returns:
        {
          "shop_domain": str,
          "currency": str,
          "total_customers": int,
          "generated_at": str,
          "segments": [
            {
              "name": str,
              "count": int,
              "revenue": float,
              "share_pct": float,
              "copy": str,
              "sample_customers": [{"id": "cust_<hash>", "orders": int,
                                    "revenue": float, "last_order_days_ago": int}],
            },
            ...
          ],
        }
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:{shop}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        rc = None

    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop) or "USD"

    # Pull per-customer rollups in shop's native currency. customer_email
    # is the join key; we filter NULL/empty + multi-currency divergence.
    rows = db.execute(text("""
        SELECT
            customer_email,
            COUNT(*) AS orders,
            COALESCE(SUM(total_price), 0) AS revenue,
            EXTRACT(EPOCH FROM (now() - MAX(created_at)))::bigint / 86400 AS last_order_days_ago
        FROM shop_orders
        WHERE shop_domain = :shop
          AND customer_email IS NOT NULL
          AND customer_email <> ''
          AND currency = :currency
        GROUP BY customer_email
    """), {"shop": shop, "currency": currency}).fetchall()

    if not rows:
        result = {
            "shop_domain": shop,
            "currency": currency,
            "total_customers": 0,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "segments": [],
        }
        if rc is not None:
            try:
                rc.setex(cache_key, _CACHE_TTL_S, json.dumps(result, default=str))
            except Exception as exc:
                log.warning("rfm: empty-shop cache write failed: %s", exc)
        return result

    customers = [
        {
            "email": r[0],
            "orders": int(r[1] or 0),
            "revenue": float(r[2] or 0),
            "recency_days": int(r[3] or 0),
        }
        for r in rows
    ]

    # Quintile thresholds — one pass per axis.
    sorted_recency = sorted(c["recency_days"] for c in customers)
    sorted_freq = sorted(c["orders"] for c in customers)
    sorted_mon = sorted(c["revenue"] for c in customers)

    for c in customers:
        r_score = _quintile(c["recency_days"], sorted_recency, reverse=True)
        f_score = _quintile(c["orders"], sorted_freq)
        m_score = _quintile(c["revenue"], sorted_mon)
        fm_avg = round((f_score + m_score) / 2)
        fm_avg = max(1, min(5, fm_avg))
        segment_name = _SEGMENT_NAMES.get((r_score, fm_avg), "Need Attention")
        c["segment"] = segment_name

    # Aggregate by segment.
    by_segment: dict[str, dict] = {}
    for c in customers:
        seg = c["segment"]
        bucket = by_segment.setdefault(
            seg,
            {"count": 0, "revenue": 0.0, "sample": []},
        )
        bucket["count"] += 1
        bucket["revenue"] += c["revenue"]
        if len(bucket["sample"]) < sample_per_segment:
            bucket["sample"].append({
                "id": _safe_email_hash(c["email"]),
                "orders": c["orders"],
                "revenue": round(c["revenue"], 2),
                "last_order_days_ago": c["recency_days"],
            })

    total_customers = len(customers)
    segments_out = []
    for name in SEGMENT_ORDER:
        b = by_segment.get(name)
        if not b:
            continue
        share_pct = round((b["count"] / total_customers) * 100, 1) if total_customers else 0.0
        segments_out.append({
            "name": name,
            "count": b["count"],
            "revenue": round(b["revenue"], 2),
            "share_pct": share_pct,
            "description": SEGMENT_COPY.get(name, ""),
            "sample_customers": b["sample"],
        })

    result = {
        "shop_domain": shop,
        "currency": currency,
        "total_customers": total_customers,
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "segments": segments_out,
    }
    if rc is not None:
        try:
            rc.setex(cache_key, _CACHE_TTL_S, json.dumps(result, default=str))
        except Exception as exc:
            log.warning("rfm: cache write failed: %s", exc)
    return result
