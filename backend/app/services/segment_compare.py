"""
segment_compare.py — Side-by-side comparison of two product segments.

Merchants want to know: "Is product A doing better than product B among
my hot visitors?" This service reuses the existing audience segmentation
data and wraps it in a loss-framed delta view: one winner, one loser,
with monetary gap quantified.

Built on top of segments.py — no duplication of the scoring logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("segment_compare")


@dataclass
class SegmentSnapshot:
    """Compact view of one side of the comparison."""
    product_url: str
    hot_visitors: int
    warm_visitors: int
    cold_visitors: int
    hot_cvr_estimate: float | None
    estimated_revenue_window: float

    def to_dict(self) -> dict:
        return {
            "product_url": self.product_url,
            "hot_visitors": self.hot_visitors,
            "warm_visitors": self.warm_visitors,
            "cold_visitors": self.cold_visitors,
            "hot_cvr_estimate": self.hot_cvr_estimate,
            "estimated_revenue_window": round(self.estimated_revenue_window, 2),
            "total_active": self.hot_visitors + self.warm_visitors + self.cold_visitors,
        }


def _snapshot_from_segments_response(resp: dict, product_url: str) -> SegmentSnapshot:
    """Reduce the full SegmentsResponse dict into a compact SegmentSnapshot."""
    hot = resp.get("hot", {}) or {}
    warm = resp.get("warm", {}) or {}
    cold = resp.get("cold", {}) or {}
    total_rev = (
        float(hot.get("estimated_revenue_window", 0) or 0)
        + float(warm.get("estimated_revenue_window", 0) or 0)
        + float(cold.get("estimated_revenue_window", 0) or 0)
    )
    return SegmentSnapshot(
        product_url=product_url,
        hot_visitors=int(hot.get("visitor_count", 0) or 0),
        warm_visitors=int(warm.get("visitor_count", 0) or 0),
        cold_visitors=int(cold.get("visitor_count", 0) or 0),
        hot_cvr_estimate=hot.get("cvr_estimate"),
        estimated_revenue_window=total_rev,
    )


def compare_two_products(
    db: Session, shop_domain: str, product_a: str, product_b: str,
    hours: int = 72,
) -> dict:
    """
    Return a side-by-side comparison of two products' audience segments.

    Shape:
    {
        "shop_domain": str,
        "window_hours": int,
        "product_a": SegmentSnapshot,
        "product_b": SegmentSnapshot,
        "delta": {
            "hot_visitors_delta": int,    # A - B
            "revenue_delta_eur": float,
            "winner": "A" | "B" | "tie",
            "loss_gap_eur": float,         # how much the loser is behind
            "narrative": str,
        },
        "generated_at": ISO timestamp,
    }
    """
    from app.services.audience_segments import segment_product_visitors

    try:
        snap_a_raw = segment_product_visitors(db, shop_domain=shop_domain, product_url=product_a, hours=hours)
    except Exception as exc:
        log.debug("segment_compare: snap A failed: %s", exc)
        snap_a_raw = {}
    try:
        snap_b_raw = segment_product_visitors(db, shop_domain=shop_domain, product_url=product_b, hours=hours)
    except Exception as exc:
        log.debug("segment_compare: snap B failed: %s", exc)
        snap_b_raw = {}

    a = _snapshot_from_segments_response(snap_a_raw, product_a)
    b = _snapshot_from_segments_response(snap_b_raw, product_b)

    hot_delta = a.hot_visitors - b.hot_visitors
    revenue_delta = a.estimated_revenue_window - b.estimated_revenue_window

    # Resolve shop currency so the narrative prose uses native symbol.
    try:
        from app.core.currency import format_money
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"
        from app.core.currency import format_money

    if abs(revenue_delta) < 10:
        winner = "tie"
        loss_gap = 0.0
        narrative = (
            "Both products perform equivalently in the active window — "
            "no clear winner."
        )
    elif revenue_delta > 0:
        winner = "A"
        loss_gap = revenue_delta
        narrative = (
            f"Product A is pulling {format_money(revenue_delta, currency, compact=True)} "
            f"more in the current {hours}h window than Product B. Investigate "
            f"what drives A (traffic source, landing page, pricing) and apply to B."
        )
    else:
        winner = "B"
        loss_gap = abs(revenue_delta)
        gap_money = format_money(abs(revenue_delta), currency, compact=True)
        narrative = (
            f"Product B is pulling {gap_money} more in the current {hours}h "
            f"window than Product A. Product A is the underperformer — "
            f"{gap_money} at risk until you close the gap."
        )

    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    return {
        "shop_domain": shop_domain,
        "window_hours": hours,
        "product_a": a.to_dict(),
        "product_b": b.to_dict(),
        "delta": {
            "hot_visitors_delta": hot_delta,
            "revenue_delta_eur": round(revenue_delta, 2),
            "winner": winner,
            "loss_gap_eur": round(loss_gap, 2),
            "narrative": narrative,
        },
        "currency": currency,
        "generated_at": now_iso,
    }
