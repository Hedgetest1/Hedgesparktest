"""
behavioral_cohorts.py — Behavioral LTV segmentation engine.

Segments customers by pre-purchase behavioral quality and measures
post-purchase outcomes (retention, revenue, orders) per segment.

This is the feature that structurally cannot exist in Lifetimely / Peel
because they have no storefront behavioral data. They can only segment
by purchase attributes (date, product, value). We segment by HOW the
customer behaved BEFORE their first purchase.

Segmentation dimensions:
    1. Engagement tier: HIGH / MEDIUM / LOW (from behavioral_index)
    2. Visit pattern: REPEAT_VISITOR / SINGLE_VISIT (visited before purchase day)
    3. Source quality: PAID / ORGANIC / DIRECT / REFERRAL / UNKNOWN

Each segment gets:
    - customer count
    - repeat rate
    - avg revenue per customer
    - avg orders per customer
    - 30-day retention indicator
    - interpretive insight

Public interface:
    get_behavioral_cohort_analysis(db, shop_domain, days=90) -> dict
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("behavioral_cohorts")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Behavioral index computation (matches empirical_calibration.py formula)
# ---------------------------------------------------------------------------

def _behavioral_index(avg_scroll: float, avg_dwell: float, visit_count: int) -> float:
    """
    Compute normalized 0-1 behavioral engagement index.
    Same formula as empirical_calibration.compute_behavioral_index_from_features().
    """
    scroll_norm = min(avg_scroll / 100.0, 1.0)
    dwell_norm = min(avg_dwell / 120.0, 1.0)
    visit_norm = min(max(visit_count - 1, 0) / 4.0, 1.0)
    return 0.40 * scroll_norm + 0.40 * dwell_norm + 0.20 * visit_norm


def _engagement_tier(index: float) -> str:
    """Classify behavioral index into engagement tier."""
    if index >= 0.55:
        return "HIGH"
    elif index >= 0.20:
        return "MEDIUM"
    return "LOW"


def _visit_pattern(first_event_ts: int | None, purchase_ts: datetime | None, event_count: int) -> str:
    """Classify whether customer was a repeat visitor before purchase."""
    if event_count >= 3:
        return "REPEAT_VISITOR"
    if first_event_ts and purchase_ts:
        first_dt = datetime.fromtimestamp(first_event_ts / 1000.0)
        if (purchase_ts - first_dt).days >= 1:
            return "REPEAT_VISITOR"
    return "SINGLE_VISIT"


def _source_bucket(source: str | None) -> str:
    """Bucket traffic source into broad categories."""
    if not source:
        return "UNKNOWN"
    s = source.lower()
    if s in ("direct",):
        return "DIRECT"
    if s in ("google", "bing", "yahoo", "duckduckgo", "baidu", "organic", "paid_search", "google_shopping"):
        return "SEARCH"
    if s in ("facebook", "instagram", "meta", "tiktok", "pinterest", "twitter", "snapchat", "paid_social"):
        return "SOCIAL"
    if s in ("email", "klaviyo", "sms"):
        return "EMAIL_SMS"
    if s in ("referral",):
        return "REFERRAL"
    return "OTHER"


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def get_behavioral_cohort_analysis(
    db: Session,
    shop_domain: str,
    days: int = 90,
) -> dict:
    """
    Segment customers by pre-purchase behavioral quality and measure outcomes.

    Returns:
        {
            "window_days": int,
            "generated_at": str,
            "data_coverage": {
                "total_customers": int,
                "segmentable_customers": int,
                "coverage_rate": float,
            },
            "segments": {
                "by_engagement": [...],
                "by_visit_pattern": [...],
                "by_source": [...],
            },
            "insights": [str, ...],
        }
    """
    days = max(7, min(days, 180))
    cutoff = _now() - timedelta(days=days)

    # Step 1: Get all purchasing visitors with their order data
    try:
        customers = db.execute(text("""
            SELECT
                vps.visitor_id,
                vps.first_source,
                vps.confirmed_at,
                so.total_price,
                so.customer_id,
                so.customer_email
            FROM visitor_purchase_sessions vps
            JOIN shop_orders so
                ON so.shopify_order_id = vps.shopify_order_id
               AND so.shop_domain = vps.shop_domain
            WHERE vps.shop_domain = :shop
              AND vps.confirmed_at >= :cutoff
            ORDER BY vps.visitor_id, vps.confirmed_at
        """), {"shop": shop_domain, "cutoff": cutoff}).fetchall()
    except Exception as exc:
        log.error("behavioral_cohorts: customer query failed: %s", exc)
        return _empty_response(days)

    if not customers:
        return _empty_response(days)

    # Step 2: Build per-customer order aggregates
    # customer_key → {orders, revenue, first_source, confirmed_at, visitor_id}
    customer_data: dict[str, dict] = {}
    for row in customers:
        vid = row[0]
        # Use visitor_id as customer key (most reliable for behavioral join)
        key = vid
        if key not in customer_data:
            customer_data[key] = {
                "visitor_id": vid,
                "first_source": row[1],
                "first_purchase": row[2],
                "orders": 0,
                "revenue": 0.0,
            }
        customer_data[key]["orders"] += 1
        customer_data[key]["revenue"] += float(row[3] or 0)

    total_customers = len(customer_data)

    # Step 3: Get pre-purchase behavioral data for these visitors
    visitor_ids = list({cd["visitor_id"] for cd in customer_data.values()})

    behavior_map: dict[str, dict] = {}
    if visitor_ids:
        try:
            # Get aggregate behavioral data per visitor from visitor_product_state
            behavior_rows = db.execute(text("""
                SELECT
                    visitor_id,
                    COALESCE(AVG(max_scroll_depth), 0) AS avg_scroll,
                    COALESCE(SUM(total_dwell_seconds), 0) AS total_dwell,
                    COALESCE(SUM(total_views), 0) AS total_views,
                    MAX(CASE WHEN wishlist_added THEN 1 ELSE 0 END) AS any_wishlist
                FROM visitor_product_state
                WHERE shop_domain = :shop
                  AND visitor_id = ANY(:vids)
                GROUP BY visitor_id
            """), {"shop": shop_domain, "vids": visitor_ids}).fetchall()

            for br in behavior_rows:
                behavior_map[br[0]] = {
                    "avg_scroll": float(br[1] or 0),
                    "total_dwell": float(br[2] or 0),
                    "total_views": int(br[3] or 0),
                    "any_wishlist": bool(br[4]),
                }
        except Exception as exc:
            log.warning("behavioral_cohorts: behavior query failed: %s", exc)

        # Get first event timestamp per visitor (for visit pattern)
        try:
            first_events = db.execute(text("""
                SELECT visitor_id, MIN(timestamp) AS first_ts, COUNT(*) AS event_count
                FROM events
                WHERE shop_domain = :shop
                  AND visitor_id = ANY(:vids)
                GROUP BY visitor_id
            """), {"shop": shop_domain, "vids": visitor_ids}).fetchall()

            for fe in first_events:
                if fe[0] in behavior_map:
                    behavior_map[fe[0]]["first_event_ts"] = fe[1]
                    behavior_map[fe[0]]["event_count"] = int(fe[2] or 0)
                else:
                    behavior_map[fe[0]] = {
                        "avg_scroll": 0, "total_dwell": 0, "total_views": 0,
                        "any_wishlist": False,
                        "first_event_ts": fe[1], "event_count": int(fe[2] or 0),
                    }
        except Exception as exc:
            log.warning("behavioral_cohorts: event query failed: %s", exc)

    # Step 4: Classify each customer into segments
    segmentable = 0
    by_engagement: dict[str, list] = defaultdict(list)
    by_visit: dict[str, list] = defaultdict(list)
    by_source: dict[str, list] = defaultdict(list)

    for key, cd in customer_data.items():
        vid = cd["visitor_id"]
        beh = behavior_map.get(vid)

        source_bucket = _source_bucket(cd["first_source"])
        by_source[source_bucket].append(cd)

        if beh:
            segmentable += 1
            avg_dwell_per_view = beh["total_dwell"] / max(beh["total_views"], 1)
            bi = _behavioral_index(beh["avg_scroll"], avg_dwell_per_view, beh["total_views"])
            tier = _engagement_tier(bi)
            by_engagement[tier].append(cd)

            pattern = _visit_pattern(
                beh.get("first_event_ts"),
                cd["first_purchase"],
                beh.get("event_count", 1),
            )
            by_visit[pattern].append(cd)
        else:
            by_engagement["UNKNOWN"].append(cd)
            by_visit["UNKNOWN"].append(cd)

    # Step 5: Compute segment metrics
    def _segment_metrics(members: list[dict]) -> dict:
        n = len(members)
        if n == 0:
            return {"customers": 0, "repeat_rate": 0.0, "avg_revenue": 0.0, "avg_orders": 0.0}
        total_rev = sum(m["revenue"] for m in members)
        total_ord = sum(m["orders"] for m in members)
        repeaters = sum(1 for m in members if m["orders"] >= 2)
        return {
            "customers": n,
            "repeat_rate": round(repeaters / n, 4),
            "avg_revenue": round(total_rev / n, 2),
            "avg_orders": round(total_ord / n, 2),
            "total_revenue": round(total_rev, 2),
        }

    engagement_segments = [
        {"segment": tier, **_segment_metrics(members)}
        for tier, members in sorted(by_engagement.items())
        if members
    ]
    visit_segments = [
        {"segment": pat, **_segment_metrics(members)}
        for pat, members in sorted(by_visit.items())
        if members
    ]
    source_segments = [
        {"segment": src, **_segment_metrics(members)}
        for src, members in sorted(by_source.items(), key=lambda x: -_segment_metrics(x[1])["avg_revenue"])
        if members
    ]

    # Step 6: Generate insights
    insights = _generate_insights(engagement_segments, visit_segments, source_segments, total_customers)

    return {
        "window_days": days,
        "generated_at": _now().isoformat() + "Z",
        "data_coverage": {
            "total_customers": total_customers,
            "segmentable_customers": segmentable,
            "coverage_rate": round(segmentable / total_customers, 3) if total_customers > 0 else 0.0,
        },
        "segments": {
            "by_engagement": engagement_segments,
            "by_visit_pattern": visit_segments,
            "by_source": source_segments,
        },
        "insights": insights,
    }


def _generate_insights(engagement: list, visit: list, source: list, total: int) -> list[str]:
    """Generate actionable interpretive insights from segment data."""
    insights = []

    if total == 0:
        return ["No customer data yet. Insights will appear as orders with visitor tracking accumulate."]

    # Engagement insight
    high = next((s for s in engagement if s["segment"] == "HIGH"), None)
    low = next((s for s in engagement if s["segment"] == "LOW"), None)
    if high and low and high["customers"] >= 2 and low["customers"] >= 2:
        if high["avg_revenue"] > 0 and low["avg_revenue"] > 0:
            ratio = round(high["avg_revenue"] / low["avg_revenue"], 1)
            if ratio > 1.2:
                insights.append(
                    f"High-engagement visitors generate {ratio}x more revenue per customer "
                    f"(${high['avg_revenue']:.0f}) than low-engagement visitors (${low['avg_revenue']:.0f})."
                )
        if high["repeat_rate"] > low["repeat_rate"] + 0.05:
            insights.append(
                f"High-engagement buyers have {high['repeat_rate']*100:.0f}% repeat rate vs "
                f"{low['repeat_rate']*100:.0f}% for low-engagement — pre-purchase behavior predicts retention."
            )

    # Visit pattern insight
    repeat = next((s for s in visit if s["segment"] == "REPEAT_VISITOR"), None)
    single = next((s for s in visit if s["segment"] == "SINGLE_VISIT"), None)
    if repeat and single and repeat["customers"] >= 2 and single["customers"] >= 2:
        if repeat["avg_revenue"] > single["avg_revenue"] * 1.2:
            insights.append(
                f"Repeat visitors before purchase spend ${repeat['avg_revenue']:.0f}/customer vs "
                f"${single['avg_revenue']:.0f} for single-visit buyers. "
                "Retargeting browsers who haven't bought yet could capture this value."
            )
        if single["repeat_rate"] < repeat["repeat_rate"] - 0.05:
            insights.append(
                f"Single-visit purchasers have only {single['repeat_rate']*100:.0f}% repeat rate. "
                "Consider post-purchase engagement flows for impulse buyers."
            )

    # Source insight
    if len(source) >= 2:
        best = max(source, key=lambda s: s["avg_revenue"])
        worst = min((s for s in source if s["customers"] >= 2), key=lambda s: s["avg_revenue"], default=None)
        if worst and best["segment"] != worst["segment"] and best["avg_revenue"] > worst["avg_revenue"] * 1.3:
            insights.append(
                f"{best['segment']} traffic produces the highest customer value "
                f"(${best['avg_revenue']:.0f}/customer). "
                f"{worst['segment']} traffic converts but at ${worst['avg_revenue']:.0f}/customer — "
                f"consider if the acquisition cost is justified."
            )

    # Coverage insight
    if not insights:
        if total < 10:
            insights.append(
                f"Only {total} customers in the analysis window. "
                "Behavioral segments become meaningful with 20+ customers."
            )
        else:
            insights.append("Segments are balanced — no strong behavioral signal detected yet.")

    return insights


def _empty_response(days: int) -> dict:
    return {
        "window_days": days,
        "generated_at": _now().isoformat() + "Z",
        "data_coverage": {
            "total_customers": 0,
            "segmentable_customers": 0,
            "coverage_rate": 0.0,
        },
        "segments": {
            "by_engagement": [],
            "by_visit_pattern": [],
            "by_source": [],
        },
        "insights": ["No customer data yet. Behavioral segments will appear as orders with visitor tracking accumulate."],
    }
