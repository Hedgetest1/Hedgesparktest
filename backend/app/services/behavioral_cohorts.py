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

# ---------------------------------------------------------------------------
# get_behavioral_cohort_analysis — stage helpers
# Refactor 2026-05-13 (A3 close): 215-LOC god function → composer + 11
# pure stage helpers. Contract preserved byte-identical. SQL hoisted
# to module constants. _segment_metrics promoted to module-level so
# it's unit-testable. 2 hardcoded `$` strings in _generate_insights
# converted to format_money(currency) — currency-drift fix discovered
# during the refactor sibling sweep.
# ---------------------------------------------------------------------------


_CUSTOMERS_SQL = text("""
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
""")


_BEHAVIOR_SQL = text("""
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
""")


_FIRST_EVENTS_SQL = text("""
    SELECT visitor_id, MIN(timestamp) AS first_ts, COUNT(*) AS event_count
    FROM events
    WHERE shop_domain = :shop
      AND visitor_id = ANY(:vids)
    GROUP BY visitor_id
""")


def _fetch_customer_orders(
    db: Session, shop_domain: str, cutoff: datetime,
) -> list | None:
    """Returns customer rows or None on query failure. Caller turns
    None into _empty_response(days) — preserves prior behavior."""
    try:
        return db.execute(_CUSTOMERS_SQL, {
            "shop": shop_domain, "cutoff": cutoff,
        }).fetchall()
    except Exception as exc:
        log.error("behavioral_cohorts: customer query failed: %s", exc)
        return None


def _fetch_behavior_rows(
    db: Session, shop_domain: str, visitor_ids: list[str],
) -> list:
    """Pre-purchase behavioral data per visitor. Returns [] on failure."""
    if not visitor_ids:
        return []
    try:
        return db.execute(_BEHAVIOR_SQL, {
            "shop": shop_domain, "vids": visitor_ids,
        }).fetchall()
    except Exception as exc:
        log.warning("behavioral_cohorts: behavior query failed: %s", exc)
        return []


def _fetch_first_events(
    db: Session, shop_domain: str, visitor_ids: list[str],
) -> list:
    """First event timestamp + event count per visitor. Returns [] on failure."""
    if not visitor_ids:
        return []
    try:
        return db.execute(_FIRST_EVENTS_SQL, {
            "shop": shop_domain, "vids": visitor_ids,
        }).fetchall()
    except Exception as exc:
        log.warning("behavioral_cohorts: event query failed: %s", exc)
        return []


def _resolve_currency_cohorts(db: Session, shop_domain: str) -> str:
    """USD fallback on any lookup failure — observed via record_silent_return."""
    from app.core.silent_fallback import record_silent_return
    try:
        from app.services.revenue_metrics import get_shop_currency
        return get_shop_currency(db, shop_domain) or "USD"
    except Exception as exc:
        record_silent_return("behavioral_cohorts.resolve_currency")
        log.warning("behavioral_cohorts: currency lookup failed: %s", exc)
        # data-truth-allowed: except-block last-resort fallback; failure surfaces via record_silent_return metric
        return "USD"


def _build_customer_aggregates(customer_rows: list) -> dict[str, dict]:
    """Customer rows → {visitor_id: {visitor_id, first_source,
    first_purchase, orders, revenue}}. First-seen wins for the
    first_source / first_purchase fields (rows are pre-sorted by
    visitor_id, confirmed_at in the SQL)."""
    out: dict[str, dict] = {}
    for row in customer_rows:
        vid = row[0]
        if vid not in out:
            out[vid] = {
                "visitor_id": vid,
                "first_source": row[1],
                "first_purchase": row[2],
                "orders": 0,
                "revenue": 0.0,
            }
        out[vid]["orders"] += 1
        out[vid]["revenue"] += float(row[3] or 0)
    return out


def _build_behavior_map(
    behavior_rows: list, first_event_rows: list,
) -> dict[str, dict]:
    """Merge per-visitor behavioral rows + first-event rows into a
    single map. A visitor with first-events but no behavior gets zero-
    filled behavior defaults."""
    out: dict[str, dict] = {}
    for br in behavior_rows:
        out[br[0]] = {
            "avg_scroll": float(br[1] or 0),
            "total_dwell": float(br[2] or 0),
            "total_views": int(br[3] or 0),
            "any_wishlist": bool(br[4]),
        }
    for fe in first_event_rows:
        if fe[0] in out:
            out[fe[0]]["first_event_ts"] = fe[1]
            out[fe[0]]["event_count"] = int(fe[2] or 0)
        else:
            out[fe[0]] = {
                "avg_scroll": 0, "total_dwell": 0, "total_views": 0,
                "any_wishlist": False,
                "first_event_ts": fe[1], "event_count": int(fe[2] or 0),
            }
    return out


def _segment_metrics(members: list[dict]) -> dict:
    """Per-segment aggregates: count, repeat rate, avg + total revenue,
    avg orders. Promoted from inline closure → module-level helper so
    it's independently testable."""
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


def _classify_into_segments(
    customer_data: dict[str, dict], behavior_map: dict[str, dict],
) -> tuple[dict, dict, dict, int]:
    """Sort each customer into 3 segment dicts (by engagement tier,
    visit pattern, source bucket). Returns (by_engagement, by_visit,
    by_source, segmentable_count) where segmentable_count is the
    number of customers with behavior data."""
    by_engagement: dict[str, list] = defaultdict(list)
    by_visit: dict[str, list] = defaultdict(list)
    by_source: dict[str, list] = defaultdict(list)
    segmentable = 0

    for cd in customer_data.values():
        vid = cd["visitor_id"]
        beh = behavior_map.get(vid)

        by_source[_source_bucket(cd["first_source"])].append(cd)

        if beh:
            segmentable += 1
            avg_dwell_per_view = beh["total_dwell"] / max(beh["total_views"], 1)
            bi = _behavioral_index(beh["avg_scroll"], avg_dwell_per_view, beh["total_views"])
            by_engagement[_engagement_tier(bi)].append(cd)
            by_visit[_visit_pattern(
                beh.get("first_event_ts"),
                cd["first_purchase"],
                beh.get("event_count", 1),
            )].append(cd)
        else:
            by_engagement["UNKNOWN"].append(cd)
            by_visit["UNKNOWN"].append(cd)
    return by_engagement, by_visit, by_source, segmentable


def _build_engagement_segments(by_engagement: dict) -> list[dict]:
    """Engagement segments sorted alphabetically (HIGH/LOW/MEDIUM/UNKNOWN)."""
    return [
        {"segment": tier, **_segment_metrics(members)}
        for tier, members in sorted(by_engagement.items())
        if members
    ]


def _build_visit_segments(by_visit: dict) -> list[dict]:
    """Visit-pattern segments sorted alphabetically."""
    return [
        {"segment": pat, **_segment_metrics(members)}
        for pat, members in sorted(by_visit.items())
        if members
    ]


def _build_source_segments(by_source: dict) -> list[dict]:
    """Source segments sorted by avg_revenue descending."""
    return [
        {"segment": src, **_segment_metrics(members)}
        for src, members in sorted(
            by_source.items(),
            key=lambda x: -_segment_metrics(x[1])["avg_revenue"],
        )
        if members
    ]


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

    Refactored 2026-05-13 (A3 close): 215-LOC god function → 35-LOC
    composer + 11 pure stage helpers.
    """
    days = max(7, min(days, 180))
    cutoff = _now() - timedelta(days=days)

    customer_rows = _fetch_customer_orders(db, shop_domain, cutoff)
    if customer_rows is None or not customer_rows:
        return _empty_response(days)

    customer_data = _build_customer_aggregates(customer_rows)
    total_customers = len(customer_data)

    visitor_ids = list({cd["visitor_id"] for cd in customer_data.values()})
    behavior_map = _build_behavior_map(
        _fetch_behavior_rows(db, shop_domain, visitor_ids),
        _fetch_first_events(db, shop_domain, visitor_ids),
    )

    by_engagement, by_visit, by_source, segmentable = _classify_into_segments(
        customer_data, behavior_map,
    )
    engagement_segments = _build_engagement_segments(by_engagement)
    visit_segments = _build_visit_segments(by_visit)
    source_segments = _build_source_segments(by_source)

    currency = _resolve_currency_cohorts(db, shop_domain)
    insights = _generate_insights(
        engagement_segments, visit_segments, source_segments,
        total_customers, currency=currency,
    )

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


def _generate_insights(
    engagement: list, visit: list, source: list, total: int,
    currency: str = "USD",
) -> list[str]:
    """Generate actionable interpretive insights from segment data.

    Currency drift fix 2026-05-13: 2 of 3 insight branches previously
    hardcoded `$` in revenue strings; all 3 now route through
    format_money(currency) so a GBP/EUR merchant sees the right symbol.
    """
    from app.core.currency import format_money
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
                    f"({format_money(high['avg_revenue'], currency)}) than low-engagement visitors "
                    f"({format_money(low['avg_revenue'], currency)})."
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
                f"Repeat visitors before purchase spend "
                f"{format_money(repeat['avg_revenue'], currency)}/customer vs "
                f"{format_money(single['avg_revenue'], currency)} for single-visit buyers. "
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
                f"({format_money(best['avg_revenue'], currency)}/customer). "
                f"{worst['segment']} traffic converts but at "
                f"{format_money(worst['avg_revenue'], currency)}/customer — "
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
