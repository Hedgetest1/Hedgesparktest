"""
inventory.py — Inventory KPIs backend (Gap #4, 2026-04-28).

Endpoints (all `require_merchant_session` — Lite full parity per
`feedback_0_60_parity_doctrine.md`; chrome-style availability per
`feedback_settings_is_tier_agnostic_chrome.md`):

  GET  /merchant/inventory/kpis              Headline numbers + at-risk top-3
  GET  /merchant/inventory/details           Paginated full table for the drawer
  GET  /merchant/inventory/snapshot-status   Last_fetched + worker health

Voice: calm, merchant-friendly per founder direction 2026-04-28.

Scale posture (10k merchants):
  - Reads from `inventory_snapshots` (latest per shop+product, indexed)
  - Sales rate joins `shop_orders` (already indexed by shop+created_at)
  - Cached 10min via Redis: hs:inv_kpis:v1:{shop}
  - Drawer page size capped at 100 rows
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.core.redis_client import cache_get, cache_set

router = APIRouter(tags=["inventory"])
log = logging.getLogger("inventory")

_KPI_CACHE_KEY = "hs:inv_kpis:v1:{shop}"
_KPI_CACHE_TTL = 600  # 10 minutes
_DEFAULT_LEAD_TIME_DAYS = 14
_MAX_DETAIL_PAGE_SIZE = 100
_DEFAULT_DETAIL_PAGE_SIZE = 25
_TOP_AT_RISK_LIMIT = 3


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class AtRiskRow(BaseModel):
    product_url: str
    product_title: str
    days_of_cover: float | None
    inventory_quantity: int


class InventoryKpisOut(BaseModel):
    shop_domain: str
    products_tracked: int
    out_of_stock_count: int
    low_stock_count: int
    days_of_cover_top: float | None     # days-of-cover for top-revenue product
    top_at_risk: list[AtRiskRow]
    headline: str                       # one-line merchant-friendly summary
    lead_time_days: int
    last_snapshot_at: datetime | None


class InventoryDetailRow(BaseModel):
    product_url: str
    product_title: str
    inventory_quantity: int
    sales_rate_per_day: float
    days_of_cover: float | None
    sell_through_30d_pct: float
    reorder_hint: str                   # "Reorder soon" | "OK" | "No recent sales"


class InventoryDetailsOut(BaseModel):
    shop_domain: str
    rows: list[InventoryDetailRow]
    total: int
    page: int
    page_size: int
    lead_time_days: int


class SnapshotStatusOut(BaseModel):
    shop_domain: str
    last_snapshot_at: datetime | None
    products_tracked: int
    is_fresh: bool                      # True if last snapshot < 36h


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lead_time_for_shop(db: Session, shop: str) -> int:
    row = db.execute(text(
        "SELECT inventory_lead_time_days FROM merchants WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    if row and row[0] is not None and int(row[0]) > 0:
        return int(row[0])
    return _DEFAULT_LEAD_TIME_DAYS


def _latest_per_product(db: Session, shop: str) -> list[dict[str, Any]]:
    """Return the latest snapshot row per (product_url, variant_id)
    for the shop. Aggregates variant rows up to product totals because
    the dashboard surfaces are per-product, not per-variant."""
    rows = db.execute(text(
        """
        WITH ranked AS (
            SELECT
                product_url,
                product_title,
                variant_id,
                inventory_quantity,
                snapshot_date,
                fetched_at,
                ROW_NUMBER() OVER (
                    PARTITION BY product_url, variant_id
                    ORDER BY snapshot_date DESC, fetched_at DESC
                ) AS rn
            FROM inventory_snapshots
            WHERE shop_domain = :shop
        )
        SELECT
            product_url,
            MAX(product_title) AS product_title,
            SUM(inventory_quantity)::int AS inventory_quantity,
            MAX(fetched_at) AS fetched_at
        FROM ranked
        WHERE rn = 1
        GROUP BY product_url
        """
    ), {"shop": shop}).fetchall()
    return [
        {
            "product_url": r.product_url,
            "product_title": r.product_title or "(untitled)",
            "inventory_quantity": int(r.inventory_quantity or 0),
            "fetched_at": r.fetched_at,
        }
        for r in rows
    ]


def _sales_rate_30d(db: Session, shop: str) -> dict[str, float]:
    """Map product_url → units sold per day over the last 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    cutoff_naive = cutoff.replace(tzinfo=None)
    # Approximation: shop_orders.line_items[0].title is the canonical
    # product surface (matches what the survey/forecast pipelines use).
    # When line_items has no title we skip (defensive).
    rows = db.execute(text(
        """
        SELECT
            (line_items->0->>'title') AS title,
            SUM(COALESCE((line_items->0->>'quantity')::int, 1))::float AS units
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
          AND line_items->0->>'title' IS NOT NULL
        GROUP BY title
        """
    ), {"shop": shop, "cutoff": cutoff_naive}).fetchall()
    return {r.title: float(r.units or 0) / 30.0 for r in rows}


def _days_of_cover(qty: int, sales_rate_per_day: float) -> float | None:
    if sales_rate_per_day <= 0:
        return None
    return round(qty / sales_rate_per_day, 1)


def _reorder_hint(qty: int, sales_rate: float, lead_time_days: int) -> str:
    if sales_rate <= 0:
        return "No recent sales"
    runway = qty / sales_rate
    if runway <= lead_time_days:
        return "Reorder soon"
    return "OK"


def _sell_through_30d(qty: int, units_sold_30d: float) -> float:
    denom = qty + units_sold_30d
    if denom <= 0:
        return 0.0
    return round(100.0 * units_sold_30d / denom, 1)


def _build_rows(
    snapshots: list[dict[str, Any]],
    sales_rates: dict[str, float],
    lead_time: int,
) -> list[dict[str, Any]]:
    out = []
    for s in snapshots:
        # Sales rate keyed on title (the order-line title is what we have);
        # fall back to product_url if no exact title match.
        title = s["product_title"]
        rate = sales_rates.get(title, 0.0)
        units_30d = rate * 30.0
        out.append({
            "product_url": s["product_url"],
            "product_title": title,
            "inventory_quantity": s["inventory_quantity"],
            "sales_rate_per_day": round(rate, 2),
            "days_of_cover": _days_of_cover(s["inventory_quantity"], rate),
            "sell_through_30d_pct": _sell_through_30d(s["inventory_quantity"], units_30d),
            "reorder_hint": _reorder_hint(s["inventory_quantity"], rate, lead_time),
        })
    return out


# ---------------------------------------------------------------------------
# /merchant/inventory/kpis
# ---------------------------------------------------------------------------

@router.get("/merchant/inventory/kpis", response_model=InventoryKpisOut)
def get_inventory_kpis(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    cache_key = _KPI_CACHE_KEY.format(shop=shop)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    lead_time = _lead_time_for_shop(db, shop)
    snapshots = _latest_per_product(db, shop)
    sales_rates = _sales_rate_30d(db, shop)
    rows = _build_rows(snapshots, sales_rates, lead_time)

    products_tracked = len(rows)
    out_of_stock = [r for r in rows if r["inventory_quantity"] == 0]
    low_stock = [
        r for r in rows
        if r["inventory_quantity"] > 0
        and r["days_of_cover"] is not None
        and r["days_of_cover"] <= lead_time
    ]
    last_snapshot = max((s["fetched_at"] for s in snapshots), default=None)

    # Top-at-risk: 3 products with lowest days_of_cover but qty > 0
    at_risk_pool = sorted(
        (r for r in rows if r["inventory_quantity"] > 0 and r["days_of_cover"] is not None),
        key=lambda r: r["days_of_cover"] or float("inf"),
    )[:_TOP_AT_RISK_LIMIT]

    days_of_cover_top: float | None = None
    if at_risk_pool:
        days_of_cover_top = at_risk_pool[0]["days_of_cover"]

    if products_tracked == 0:
        headline = "We're listening — your first snapshot lands within 24h."
    elif len(out_of_stock) == 0 and len(low_stock) == 0:
        headline = "All products have healthy stock right now."
    elif len(out_of_stock) > 0 and len(low_stock) == 0:
        headline = f"{len(out_of_stock)} SKU{'s' if len(out_of_stock) != 1 else ''} out of stock — restock to recover sales."
    else:
        plural = "s" if (len(low_stock) + len(out_of_stock)) != 1 else ""
        headline = f"{len(low_stock) + len(out_of_stock)} SKU{plural} need a reorder soon."

    payload = InventoryKpisOut(
        shop_domain=shop,
        products_tracked=products_tracked,
        out_of_stock_count=len(out_of_stock),
        low_stock_count=len(low_stock),
        days_of_cover_top=days_of_cover_top,
        top_at_risk=[AtRiskRow(**{
            "product_url": r["product_url"],
            "product_title": r["product_title"],
            "days_of_cover": r["days_of_cover"],
            "inventory_quantity": r["inventory_quantity"],
        }) for r in at_risk_pool],
        headline=headline,
        lead_time_days=lead_time,
        last_snapshot_at=last_snapshot,
    ).model_dump(mode="json")

    cache_set(cache_key, payload, _KPI_CACHE_TTL)
    return payload


# ---------------------------------------------------------------------------
# /merchant/inventory/details
# ---------------------------------------------------------------------------

@router.get("/merchant/inventory/details", response_model=InventoryDetailsOut)
def get_inventory_details(
    page: int = Query(default=1, ge=1, le=200),
    page_size: int = Query(default=_DEFAULT_DETAIL_PAGE_SIZE, ge=1, le=_MAX_DETAIL_PAGE_SIZE),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    lead_time = _lead_time_for_shop(db, shop)
    snapshots = _latest_per_product(db, shop)
    sales_rates = _sales_rate_30d(db, shop)
    rows = _build_rows(snapshots, sales_rates, lead_time)
    # Sort by days_of_cover ascending (most-at-risk first; None at end)
    rows.sort(key=lambda r: (r["days_of_cover"] is None, r["days_of_cover"] or float("inf")))

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    paged = rows[start:end]

    return {
        "shop_domain": shop,
        "rows": paged,
        "total": total,
        "page": page,
        "page_size": page_size,
        "lead_time_days": lead_time,
    }


# ---------------------------------------------------------------------------
# /merchant/inventory/snapshot-status
# ---------------------------------------------------------------------------

@router.get("/merchant/inventory/snapshot-status", response_model=SnapshotStatusOut)
def get_snapshot_status(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    row = db.execute(text(
        """
        SELECT
            MAX(fetched_at) AS last_at,
            COUNT(DISTINCT product_url) AS products_tracked
        FROM inventory_snapshots
        WHERE shop_domain = :shop
        """
    ), {"shop": shop}).fetchone()

    last_at = row.last_at if row else None
    products_tracked = int(row.products_tracked or 0) if row else 0
    is_fresh = False
    if last_at:
        # last_at is timezone-aware (DateTime(timezone=True)); compare in UTC.
        delta = datetime.now(timezone.utc) - last_at
        is_fresh = delta < timedelta(hours=36)

    return {
        "shop_domain": shop,
        "last_snapshot_at": last_at,
        "products_tracked": products_tracked,
        "is_fresh": is_fresh,
    }
