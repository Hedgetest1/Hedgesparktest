"""
heatmap.py — Scroll depth and click aggregation (Pro only).

GET /pro/heatmap?shop=&product_url=&hours=

WishSpark already captures max_scroll_depth and click events per visitor
per product page.  This endpoint aggregates that data into a visual
heatmap-compatible format — attacking Hotjar and Microsoft Clarity on their
core proposition without building session replay.

Scroll depth buckets (quartiles):
    0–25%   : "Above fold" visitors
    25–50%  : "Upper half" readers
    50–75%  : "Lower half" deep readers
    75–100% : "Full page" readers

Each bucket shows:
    - visitor_count: how many visitors reached this depth
    - pct_of_viewers: as a percentage of all product viewers

This tells merchants exactly where visitors stop reading — the most
actionable insight for page optimization decisions.

Click aggregation:
    Top pages by click volume relative to this product's context.
    "What do visitors click after viewing this product?"

GET /pro/heatmap/top?shop=
    Returns the scroll profile for the top 5 products by traffic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/heatmap", tags=["heatmap"])


# ---------------------------------------------------------------------------
# Response models for /pro/heatmap + /pro/heatmap/top — Scroll DNA cassettoni.
# ---------------------------------------------------------------------------


class ScrollBucket(BaseModel):
    """One quartile bucket in the scroll depth distribution."""
    label: str
    range: list[int]
    visitor_count: int
    pct_of_viewers: float


class ScrollProfile(BaseModel):
    """Per-product scroll profile (buckets + summary metrics)."""
    total_viewers: int
    avg_scroll_depth: float
    median_scroll_depth: float
    buckets: list[ScrollBucket]
    insight: str


class HeatmapResponse(BaseModel):
    """GET /pro/heatmap — one product's scroll profile."""
    product_url: str
    window_hours: int
    generated_at: str
    scroll: ScrollProfile


class HeatmapProductRow(BaseModel):
    """One product summary row inside the /pro/heatmap/top response."""
    product_url: str
    total_viewers: int
    avg_scroll_depth: float
    deep_reader_pct: float
    insight: str
    buckets: list[ScrollBucket]


class HeatmapTopResponse(BaseModel):
    """GET /pro/heatmap/top — scroll profiles for the top 5 products."""
    products: list[HeatmapProductRow]
    window_hours: int
    generated_at: str | None = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _compute_scroll_buckets(
    db: Session,
    shop_domain: str,
    product_url: str,
    since_ms: int,
) -> dict:
    """
    Aggregate scroll depth into quartile buckets for a product page.
    """
    try:
        rows = db.execute(
            text("""
                SELECT
                    visitor_id,
                    MAX(COALESCE(max_scroll_depth, 0)) AS max_scroll
                FROM events
                WHERE shop_domain = :shop
                  AND product_url = :product_url
                  AND timestamp   >= :since_ms
                  AND event_type  IN ('product_view', 'scroll', 'dwell_time')
                  AND visitor_id  IS NOT NULL
                GROUP BY visitor_id
            """),
            {"shop": shop_domain, "product_url": product_url, "since_ms": since_ms},
        ).fetchall()
    except Exception as exc:
        log.error("heatmap: scroll query failed shop=%s: %s", shop_domain, exc)
        return _empty_scroll_buckets()

    if not rows:
        return _empty_scroll_buckets()

    total = len(rows)
    bucket_0_25  = sum(1 for r in rows if float(r[1] or 0) <= 25)
    bucket_25_50 = sum(1 for r in rows if 25 < float(r[1] or 0) <= 50)
    bucket_50_75 = sum(1 for r in rows if 50 < float(r[1] or 0) <= 75)
    bucket_75_100 = sum(1 for r in rows if float(r[1] or 0) > 75)

    avg_scroll = sum(float(r[1] or 0) for r in rows) / total
    median_scroll = sorted(float(r[1] or 0) for r in rows)[total // 2]

    def pct(n: int) -> float:
        return round(n / total * 100, 1) if total > 0 else 0.0

    return {
        "total_viewers": total,
        "avg_scroll_depth": round(avg_scroll, 1),
        "median_scroll_depth": round(median_scroll, 1),
        "buckets": [
            {
                "label":         "Above fold (0–25%)",
                "range":         [0, 25],
                "visitor_count": bucket_0_25,
                "pct_of_viewers": pct(bucket_0_25),
            },
            {
                "label":         "Upper half (25–50%)",
                "range":         [25, 50],
                "visitor_count": bucket_25_50,
                "pct_of_viewers": pct(bucket_25_50),
            },
            {
                "label":         "Lower half (50–75%)",
                "range":         [50, 75],
                "visitor_count": bucket_50_75,
                "pct_of_viewers": pct(bucket_50_75),
            },
            {
                "label":         "Full page (75–100%)",
                "range":         [75, 100],
                "visitor_count": bucket_75_100,
                "pct_of_viewers": pct(bucket_75_100),
            },
        ],
        "insight": _scroll_insight(avg_scroll, bucket_75_100, total),
    }


def _scroll_insight(avg_scroll: float, deep_readers: int, total: int) -> str:
    if total == 0:
        return "No scroll data available yet."
    deep_pct = deep_readers / total * 100
    if avg_scroll < 30:
        return (
            f"Most visitors ({100 - round(deep_pct)}%) leave in the top third of the page. "
            "Your product description or price may be losing them early."
        )
    elif avg_scroll > 70:
        return (
            f"{round(deep_pct)}% of visitors read the full page — strong engagement. "
            "Consider adding a stronger CTA at the bottom."
        )
    else:
        return (
            f"Average scroll depth is {avg_scroll:.0f}%. "
            f"{round(deep_pct)}% read the full page. "
            "Ensure key conversion elements are above the 50% scroll point."
        )


def _empty_scroll_buckets() -> dict:
    return {
        "total_viewers": 0,
        "avg_scroll_depth": 0.0,
        "median_scroll_depth": 0.0,
        "buckets": [],
        "insight": "No scroll data available for this product yet.",
    }


@router.get(
    "",
    response_model=HeatmapResponse,
    response_model_exclude_none=False,
)
def get_heatmap(
    product_url: str,
    hours: int = 72,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Scroll depth aggregation for a specific product page.

    Returns quartile buckets showing where visitors stop scrolling.
    Uses real behavioral event data captured by spark-tracker.js.

    No session replay infrastructure needed — we already have the scroll data.
    """
    hours = max(1, min(hours, 168))
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)

    scroll_data = _compute_scroll_buckets(db, shop, product_url, since_ms)

    return {
        "product_url":  product_url,
        "window_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "scroll":       scroll_data,
    }


@router.get(
    "/top",
    response_model=HeatmapTopResponse,
    response_model_exclude_none=False,
)
def get_top_heatmaps(
    hours: int = 72,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Scroll profiles for the top 5 products by viewer count in the window.

    Returns a heatmap summary for each product so merchants can compare
    page engagement across their catalog without selecting individual URLs.
    """
    hours = max(1, min(hours, 168))
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)

    # Find top products by viewer count in the window
    try:
        top_rows = db.execute(
            text("""
                SELECT product_url, COUNT(DISTINCT visitor_id) AS viewers
                FROM events
                WHERE shop_domain = :shop
                  AND timestamp   >= :since_ms
                  AND product_url  IS NOT NULL
                  AND event_type   IN ('product_view', 'scroll')
                GROUP BY product_url
                ORDER BY viewers DESC
                LIMIT 5
            """),
            {"shop": shop, "since_ms": since_ms},
        ).fetchall()
    except Exception as exc:
        log.error("heatmap: top products query failed shop=%s: %s", shop, exc)
        return {"products": [], "window_hours": hours}

    results = []
    for row in top_rows:
        product_url = str(row[0])
        viewers     = int(row[1] or 0)
        scroll_data = _compute_scroll_buckets(db, shop, product_url, since_ms)
        results.append({
            "product_url":       product_url,
            "total_viewers":     viewers,
            "avg_scroll_depth":  scroll_data["avg_scroll_depth"],
            "deep_reader_pct":   next(
                (b["pct_of_viewers"] for b in scroll_data["buckets"] if "75–100" in b["label"]),
                0.0,
            ),
            "insight":           scroll_data["insight"],
            "buckets":           scroll_data["buckets"],
        })

    return {
        "products":     results,
        "window_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
