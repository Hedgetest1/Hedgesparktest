"""
weekly_digest.py — Assemble a weekly revenue digest for a merchant.

Public interface:
    assemble_digest(db, shop_domain) -> dict | None

Returns None if the merchant has no orders in the last 14 days
(nothing useful to send).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.opportunity_signal import OpportunitySignal
from app.services.signal_text import humanize_signal, humanize_action, humanize_headline
from app.services.revenue_loss import calculate_expected_loss
from app.services.revenue_metrics import get_shop_aov, get_shop_currency
from app.services.action_proof import get_proof_summary
from app.services.proof_engine import get_digest_proof

log = logging.getLogger(__name__)

_MIN_ATC_FOR_INSIGHT = 5


def _humanize_product_url(product_url: str) -> str:
    """'/products/premium-leather-wallet' → 'Premium Leather Wallet'"""
    slug = product_url.rsplit("/", 1)[-1] if "/" in product_url else product_url
    return slug.replace("-", " ").replace("_", " ").title()


def assemble_digest(db: Session, shop_domain: str, merchant_plan: str = "lite") -> dict | None:
    """
    Build the weekly revenue digest payload for one merchant.

    Includes:
      - 7d revenue, order count, AOV
      - prior 7d for week-over-week delta
      - unique visitors + conversion rate
      - top 3 products by revenue
      - behavioral insight: highest-intent product with no purchases
      - date range for the reporting period
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    period_end = now
    period_start = now - timedelta(days=7)

    # --- 7d and prior-7d revenue ---
    this_week = _revenue_window(db, shop_domain, 0, 7)
    last_week = _revenue_window(db, shop_domain, 7, 14)

    if this_week["order_count"] == 0 and last_week["order_count"] == 0:
        return None  # nothing to report

    # Week-over-week deltas
    rev_delta = None
    if last_week["revenue"] > 0:
        rev_delta = round(
            (this_week["revenue"] - last_week["revenue"]) / last_week["revenue"] * 100, 1
        )

    # --- Visitors + conversion ---
    visitors = _unique_visitors(db, shop_domain, 7)
    conversion_rate = None
    if visitors > 0 and this_week["order_count"] > 0:
        conversion_rate = round(this_week["order_count"] / visitors * 100, 2)

    # --- Top products ---
    top_products = _top_products(db, shop_domain, 7)

    # --- Currency ---
    currency = _dominant_currency(db, shop_domain) or "USD"

    # --- Revenue at risk (from the signal pipeline) ---
    shop_cvr = _shop_conversion_rate(db, shop_domain, 7)
    risk = _aggregate_revenue_at_risk(
        db, shop_domain, currency, shop_cvr,
        weekly_revenue=this_week["revenue"],
        unique_visitors=visitors,
    )

    # --- Positive signal ---
    whats_working = _top_performing_product(db, shop_domain, 7, currency)

    # --- Proof of impact (closed-loop feedback) ---
    proof = get_proof_summary(db, shop_domain, days=30)

    # --- Unified proof engine (revenue-centric, trust-calibrated) ---
    proof_report = get_digest_proof(db, shop_domain)

    # --- Actionable recommendation ---
    # Primary: derive from the signal pipeline (same source as /revenue-radar)
    # Fallback: independent rule-based logic when no signals exist
    if risk["opportunities"]:
        top = risk["opportunities"][0]
        recommendation = {
            "headline": top["headline"],
            "body": f"{top['problem']} {top['action']}",
        }
        insight = {
            "type": top.get("signal_type", "opportunity"),
            "message": top["problem"],
        }
    else:
        insight = _high_intent_no_purchase(db, shop_domain, 7)
        recommendation = _build_recommendation(
            this_week=this_week,
            last_week=last_week,
            rev_delta=rev_delta,
            visitors=visitors,
            conversion_rate=conversion_rate,
            insight=insight,
            currency=currency,
        )

    # --- Data confidence ---
    # Below 30 visitors, conversion rate is statistically unreliable.
    # We still show it but flag it as "estimated (early data)".
    data_confidence = "solid" if visitors >= 30 else "early" if visitors >= 5 else "minimal"

    # --- A3: enrich with killer features (RARS hero, peer benchmarks,
    # product decline, goal progress, risk forecast). Each section is
    # individually wrapped in try/except so the digest still ships even
    # if one signal source is empty or fails.
    rars_hero = _safe_get_rars(db, shop_domain)
    rars_forecast = _safe_get_risk_forecast(shop_domain)
    peer_benchmarks = _safe_get_benchmarks(db, shop_domain)
    product_decline = _safe_get_refund_loss(db, shop_domain)
    goal_progress = _safe_get_goal_progress(db, shop_domain)

    # Killer feature sections (R-series, 2026-04-12)
    revenue_autopsy = _safe_get_revenue_autopsy(db, shop_domain)
    abandoned_intent = _safe_get_abandoned_intent(db, shop_domain)
    price_sensitivity = _safe_get_price_sensitivity(db, shop_domain)
    causal_lift = _safe_get_causal_lift(db, shop_domain)

    return {
        "shop_domain": shop_domain,
        "generated_at": now.isoformat() + "Z",
        "period_start": period_start.strftime("%b %d"),
        "period_end": period_end.strftime("%b %d, %Y"),
        "currency": currency,
        "this_week": this_week,
        "last_week": last_week,
        "revenue_delta_pct": rev_delta,
        "unique_visitors": visitors,
        "conversion_rate": conversion_rate,
        "data_confidence": data_confidence,
        "top_products": top_products,
        "insight": insight,
        "recommendation": recommendation,
        "revenue_at_risk": risk,
        "whats_working": whats_working,
        "proof": proof,
        "proof_report": proof_report,
        "merchant_plan": merchant_plan,
        "sip_insights": _get_sip_insights(db, shop_domain),
        # Killer feature sections (A3, 2026-04-11)
        "rars_hero": rars_hero,
        "rars_forecast": rars_forecast,
        "peer_benchmarks": peer_benchmarks,
        "product_decline": product_decline,
        "goal_progress": goal_progress,
        # R-series features (2026-04-12)
        "revenue_autopsy": revenue_autopsy,
        "abandoned_intent": abandoned_intent,
        "price_sensitivity": price_sensitivity,
        "causal_lift": causal_lift,
    }


# ---------------------------------------------------------------------------
# Killer feature wrappers — each is fail-soft and returns None on failure
# so the digest never breaks because one signal source is empty.
# ---------------------------------------------------------------------------


def _safe_get_rars(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.revenue_at_risk import get_revenue_at_risk
        report = get_revenue_at_risk(db, shop_domain)
        if not isinstance(report, dict):
            return None
        # Strip internal debug field if present
        report.pop("_prevent_evidence", None)
        if (report.get("total_at_risk_eur") or 0) <= 0 and not report.get("components"):
            return None
        return report
    except Exception:
        return None


def _safe_get_risk_forecast(shop_domain: str) -> dict | None:
    try:
        from app.services.risk_forecast import get_risk_forecast
        result = get_risk_forecast(shop_domain)
        if not isinstance(result, dict):
            return None
        if result.get("status") != "ok":
            return None  # insufficient history → don't render forecast
        return result
    except Exception:
        return None


def _safe_get_benchmarks(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.benchmarks import get_merchant_benchmark_report
        report = get_merchant_benchmark_report(db, shop_domain)
        if not isinstance(report, dict):
            return None
        if int(report.get("peer_count") or 0) < 10:
            return None  # k-anonymity floor
        return report
    except Exception:
        return None


def _safe_get_refund_loss(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.refund_loss import get_refund_loss_report
        report = get_refund_loss_report(db, shop_domain)
        if not isinstance(report, dict):
            return None
        if int(report.get("product_count") or 0) == 0:
            return None
        return report
    except Exception:
        return None


def _safe_get_goal_progress(db: Session, shop_domain: str) -> list[dict] | None:
    try:
        from app.services.goals import compute_goal_progress
        progress = compute_goal_progress(db, shop_domain)
        if not progress:
            return None
        return [
            {
                "metric": p.metric,
                "target_value": p.target_value,
                "current_value": p.current_value,
                "projected_value": p.projected_value,
                "status": p.status,
                "progress_pct": (
                    round(p.current_value / p.target_value * 100, 1)
                    if p.target_value else 0
                ),
            }
            for p in progress
        ]
    except Exception:
        return None


def _safe_get_revenue_autopsy(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.revenue_autopsy import compute_product_autopsy
        report = compute_product_autopsy(db, shop_domain)
        if not report or not report.get("products"):
            return None
        # Only include if there are declining products
        declining = [p for p in report["products"] if p["direction"] == "declining"]
        if not declining:
            return None
        return {
            "headline": report["headline"],
            "declining_count": report["summary"]["declining_count"],
            "total_loss_per_week": report["summary"]["total_loss_per_week"],
            "top_decline_cause": report["summary"]["top_decline_cause"],
            "top_product": declining[0]["product_name"] if declining else None,
        }
    except Exception:
        return None


def _safe_get_abandoned_intent(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.abandoned_intent import compute_abandoned_intent
        report = compute_abandoned_intent(db, shop_domain)
        if not report or not report.get("products"):
            return None
        top = report["products"][0]
        return {
            "headline": report["headline"],
            "top_product": top["product_name"],
            "abandon_rate": top["abandon_rate_pct"],
            "leak_point": top["leak_label"],
            "buyer_avg_products": report["session_insights"].get("buyer_avg_products_viewed", 0),
            "nonbuyer_avg_products": report["session_insights"].get("nonbuyer_avg_products_viewed", 0),
        }
    except Exception:
        return None


def _safe_get_price_sensitivity(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.price_sensitivity import compute_price_sensitivity
        report = compute_price_sensitivity(db, shop_domain)
        if not report or not report.get("bands"):
            return None
        barrier_products = report.get("products", [])
        if not barrier_products:
            return None
        return {
            "headline": report["headline"],
            "barrier_count": len(barrier_products),
            "top_barrier": barrier_products[0]["product_name"] if barrier_products else None,
        }
    except Exception:
        return None


def _safe_get_causal_lift(db: Session, shop_domain: str) -> dict | None:
    try:
        from app.services.causal_intervention_engine import measure_nudge_lift
        report = measure_nudge_lift(db, shop_domain)
        if not report or report.get("nudges_measured", 0) == 0:
            return None
        if report.get("confidence", 0) < 80:
            return None  # only show statistically significant results
        return {
            "lift_pct": report["total_lift_pct"],
            "attributed_revenue": report["attributed_revenue_eur"],
            "confidence": report["confidence"],
            "nudges_measured": report["nudges_measured"],
            "detail": report["detail"],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Revenue at risk — reads directly from the opportunity signal pipeline
# (same source that powers /opportunities and /revenue-radar/top)
# ---------------------------------------------------------------------------

_MAX_CVR_FOR_RISK = 0.10  # Cap CVR at 10% — no legitimate store sustains higher
_MIN_VISITORS_FOR_RISK = 10  # Below this, CVR is noise — suppress risk section


def _shop_conversion_rate(db: Session, shop: str, days: int) -> float:
    """
    Compute the shop-level conversion rate from real data.

    Guards:
      - Floored at 0.5% to avoid zero-division
      - Capped at _MAX_CVR_FOR_RISK (10%) to prevent inflated risk numbers
        from low-data stores (e.g., 2 orders / 5 visitors = 40% is test noise)
      - Returns 0.02 (2%) when data is insufficient
    """
    try:
        row = db.execute(
            text("""
                SELECT
                    (SELECT COUNT(*)::float FROM shop_orders
                     WHERE shop_domain = :shop
                       AND created_at >= NOW() - make_interval(days => :days)) AS orders,
                    (SELECT COUNT(DISTINCT visitor_id)::float FROM events
                     WHERE shop_domain = :shop
                       AND timestamp > (EXTRACT(EPOCH FROM NOW()) * 1000
                                        - CAST(:days AS bigint) * 86400000)::bigint) AS visitors
            """),
            {"shop": shop, "days": days},
        ).fetchone()
        if row and row[1] and row[1] > 0 and row[0] and row[0] > 0:
            raw_cvr = float(row[0]) / float(row[1])
            return max(min(raw_cvr, _MAX_CVR_FOR_RISK), 0.005)
    except Exception as exc:
        log.warning("weekly_digest: shop_cvr query failed: %s", exc)
    return 0.02  # conservative 2% fallback


def _top_performing_product(db: Session, shop: str, days: int, currency: str) -> dict | None:
    """
    Identify the top-performing product by real conversion efficiency.
    Returns the product with the most orders relative to views.
    """
    try:
        row = db.execute(
            text("""
                WITH product_orders AS (
                    SELECT item->>'title' AS title,
                           COUNT(DISTINCT so.shopify_order_id) AS orders,
                           SUM((item->>'price')::numeric * (item->>'quantity')::int) AS revenue
                    FROM shop_orders so,
                         jsonb_array_elements(so.line_items) AS item
                    WHERE so.shop_domain = :shop
                      AND so.created_at >= NOW() - make_interval(days => :days)
                      AND item->>'title' IS NOT NULL
                      AND item->>'price' IS NOT NULL
                      AND item->>'quantity' IS NOT NULL
                    GROUP BY item->>'title'
                    HAVING COUNT(DISTINCT so.shopify_order_id) >= 2
                    ORDER BY orders DESC, revenue DESC
                    LIMIT 1
                )
                SELECT title, orders, revenue FROM product_orders
            """),
            {"shop": shop, "days": days},
        ).fetchone()
        if row and row[0]:
            return {
                "product_name": str(row[0]),
                "orders": int(row[1]),
                "revenue": round(float(row[2] or 0), 2),
                "message": (
                    f'"{row[0]}" is your top performer this week with '
                    f'{row[1]} orders ({currency} {float(row[2] or 0):,.2f} revenue).'
                ),
            }
    except Exception as exc:
        log.warning("weekly_digest: top_performing_product query failed: %s", exc)
    return None


def _aggregate_revenue_at_risk(
    db: Session, shop: str, currency: str, shop_cvr: float,
    weekly_revenue: float = 0.0, unique_visitors: int = 0,
) -> dict:
    """
    Read active opportunity signals, enrich with expected_loss, return
    a digest-ready summary.

    Uses the same signal pipeline as the dashboard:
      opportunity_signals → signal_text.humanize_signal/action → revenue_loss

    Conversion probability: uses the shop-level conversion rate from real
    order/visitor data (same philosophy as action_candidates_engine Tier 2).
    This ensures the "Revenue at Risk" numbers in the digest are consistent
    with the dashboard's scoring.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    empty = {
        "total_at_risk": 0, "affected_products": 0,
        "opportunities": [], "top_recoverable": 0,
    }

    # Suppress risk section when visitor data is too thin to be meaningful.
    # Below _MIN_VISITORS_FOR_RISK, CVR is noise and risk numbers mislead.
    if unique_visitors < _MIN_VISITORS_FOR_RISK:
        return empty

    try:
        signals = (
            db.query(OpportunitySignal)
            .filter(
                OpportunitySignal.shop_domain == shop,
                OpportunitySignal.expires_at >= now,
            )
            .order_by(OpportunitySignal.signal_strength.desc())
            .limit(20)
            .all()
        )
    except Exception as exc:
        log.warning("weekly_digest: failed to read signals shop=%s: %s", shop, exc)
        return empty

    if not signals:
        return empty

    aov = get_shop_aov(db, shop)

    # Fetch views_24h per product for expected_loss calculation
    product_urls = list({s.product_url for s in signals})
    views_map: dict[str, int] = {}
    try:
        placeholders = ", ".join(f":p{i}" for i in range(len(product_urls)))
        params = {f"p{i}": url for i, url in enumerate(product_urls)}
        params["shop"] = shop
        rows = db.execute(
            text(f"""
                SELECT product_url, COUNT(*) AS views
                FROM events
                WHERE shop_domain = :shop
                  AND product_url IN ({placeholders})
                  AND event_type IN ('page_view', 'product_view')
                  AND timestamp > (EXTRACT(EPOCH FROM NOW()) * 1000 - 86400000)::bigint
                GROUP BY product_url
            """),
            params,
        ).fetchall()
        views_map = {r[0]: int(r[1]) for r in rows}
    except Exception as exc:
        log.warning("weekly_digest: product views_map query failed: %s", exc)

    enriched = []
    total_loss = 0.0
    seen_products = set()

    for sig in signals:
        product_name = _humanize_product_url(sig.product_url)
        views_24h = views_map.get(sig.product_url, 0)

        # Use the shop-level conversion rate (from real data) as the
        # conversion probability for expected_loss calculation.
        # This matches the dashboard's Tier 2 calibration philosophy.
        loss_data = calculate_expected_loss(
            product_metrics_row={"views_24h": views_24h},
            conversion_probability=shop_cvr,
            aov=aov,
        )

        enriched.append({
            "product_name": product_name,
            "product_url": sig.product_url,
            "signal_type": sig.signal_type,
            "headline": humanize_headline(sig.signal_type),
            "problem": humanize_signal(sig.signal_type, product_name),
            "action": humanize_action(sig.signal_type),
            "expected_loss": loss_data["expected_loss"],
            "loss_band": loss_data["loss_band"],
            "urgency": loss_data["urgency_score"],
        })
        total_loss += loss_data["expected_loss"]
        seen_products.add(sig.product_url)

    # Sort by urgency descending (same prioritization as dashboard)
    enriched.sort(key=lambda x: x["urgency"], reverse=True)
    top = enriched[:3]

    # Cap total_at_risk to weekly revenue — "at risk" cannot exceed what was earned.
    # This prevents absurd numbers on low-data stores where CVR × AOV × views
    # can exceed actual revenue due to small-sample artifacts.
    if weekly_revenue > 0:
        total_loss = min(total_loss, weekly_revenue)

    # "Impact if fixed" = expected_loss of the top signal, also capped
    top_recoverable = min(top[0]["expected_loss"], total_loss) if top else 0

    return {
        "total_at_risk": round(total_loss, 2),
        "affected_products": len(seen_products),
        "opportunities": top,
        "top_recoverable": round(top_recoverable, 2),
    }


def _revenue_window(db: Session, shop: str, offset_days: int, end_days: int) -> dict:
    """Revenue stats for shop_orders created between (now - end_days) and (now - offset_days)."""
    currency = get_shop_currency(db, shop)
    try:
        row = db.execute(
            text("""
                SELECT COUNT(*)::int                        AS cnt,
                       COALESCE(SUM(total_price), 0) AS rev,
                       COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 0) AS aov
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :end_d)
                  AND created_at <  NOW() - make_interval(days => :off_d)
                  AND (:currency IS NULL OR currency = :currency)
            """),
            {"shop": shop, "end_d": end_days, "off_d": offset_days, "currency": currency},
        ).fetchone()
        return {
            "order_count": int(row[0] or 0),
            "revenue": round(float(row[1] or 0), 2),
            "aov": round(float(row[2] or 0), 2),
        }
    except Exception as exc:
        log.warning("weekly_digest: _revenue_window shop=%s: %s", shop, exc)
        return {"order_count": 0, "revenue": 0, "aov": 0}


def _unique_visitors(db: Session, shop: str, days: int) -> int:
    """Count distinct visitor_ids with any event in the last N days."""
    try:
        row = db.execute(
            text("""
                SELECT COUNT(DISTINCT visitor_id)::int
                FROM events
                WHERE shop_domain = :shop
                  AND timestamp > (EXTRACT(EPOCH FROM NOW()) * 1000
                                   - CAST(:days AS bigint) * 86400000)::bigint
            """),
            {"shop": shop, "days": days},
        ).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _top_products(db: Session, shop: str, days: int) -> list[dict]:
    """Top 3 products by revenue from line_items JSONB."""
    try:
        rows = db.execute(
            text("""
                SELECT item->>'title'                                        AS title,
                       SUM((item->>'price')::numeric * (item->>'quantity')::int) AS rev,
                       SUM((item->>'quantity')::int)                         AS units
                FROM shop_orders,
                     jsonb_array_elements(line_items) AS item
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND item->>'title' IS NOT NULL
                  AND item->>'price' IS NOT NULL
                  AND item->>'quantity' IS NOT NULL
                GROUP BY item->>'title'
                ORDER BY rev DESC
                LIMIT 3
            """),
            {"shop": shop, "days": days},
        ).fetchall()
        return [
            {"title": r[0], "revenue": round(float(r[1] or 0), 2), "units": int(r[2] or 0)}
            for r in rows
        ]
    except Exception as exc:
        log.warning(
            "weekly_digest._top_products: shop=%s failed (%s): %s",
            shop, type(exc).__name__, str(exc)[:200],
        )
        return []


def _dominant_currency(db: Session, shop: str) -> str | None:
    try:
        row = db.execute(
            text("""
                SELECT MODE() WITHIN GROUP (ORDER BY currency)
                FROM shop_orders WHERE shop_domain = :shop AND currency IS NOT NULL
            """),
            {"shop": shop},
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception as exc:
        log.warning(
            "weekly_digest._dominant_currency: shop=%s failed (%s): %s",
            shop, type(exc).__name__, str(exc)[:200],
        )
        return None


def _high_intent_no_purchase(db: Session, shop: str, days: int) -> dict | None:
    """
    Find the product with the most add_to_cart events but zero purchases.
    Threshold: at least _MIN_ATC_FOR_INSIGHT add-to-carts to be meaningful.
    """
    try:
        row = db.execute(
            text("""
                WITH cutoff AS (
                    SELECT (EXTRACT(EPOCH FROM NOW()) * 1000
                            - CAST(:days AS bigint) * 86400000)::bigint AS ts
                ),
                atc AS (
                    SELECT product_url,
                           COUNT(DISTINCT visitor_id) AS atc_visitors
                    FROM events, cutoff
                    WHERE shop_domain = :shop
                      AND event_type  = 'add_to_cart'
                      AND product_url IS NOT NULL
                      AND timestamp   > cutoff.ts
                    GROUP BY product_url
                ),
                purchased_products AS (
                    SELECT DISTINCT pu.product_url
                    FROM visitor_purchase_sessions vps
                    JOIN events pu
                      ON pu.visitor_id  = vps.visitor_id
                     AND pu.shop_domain = vps.shop_domain
                     AND pu.product_url IS NOT NULL
                     AND pu.event_type  = 'product_view'
                    WHERE vps.shop_domain = :shop
                      AND vps.confirmed_at >= NOW() - make_interval(days => :days)
                )
                SELECT a.product_url, a.atc_visitors
                FROM atc a
                LEFT JOIN purchased_products pp ON pp.product_url = a.product_url
                WHERE pp.product_url IS NULL
                ORDER BY a.atc_visitors DESC
                LIMIT 1
            """),
            {"shop": shop, "days": days},
        ).fetchone()
        if row and row[1] and row[1] >= _MIN_ATC_FOR_INSIGHT:
            product_name = _humanize_product_url(row[0])
            return {
                "type": "high_intent_no_purchase",
                "product_url": row[0],
                "product_name": product_name,
                "atc_visitors": int(row[1]),
                "message": (
                    f"{row[1]} visitors added \"{product_name}\" to cart "
                    f"this week but didn't complete checkout."
                ),
            }
    except Exception as exc:
        log.warning("weekly_digest: insight query failed shop=%s: %s", shop, exc)
    return None


# ---------------------------------------------------------------------------
# Actionable recommendation — rule-based, deterministic
# ---------------------------------------------------------------------------

def _build_recommendation(
    *,
    this_week: dict,
    last_week: dict,
    rev_delta: float | None,
    visitors: int,
    conversion_rate: float | None,
    insight: dict | None,
    currency: str,
) -> dict:
    """
    Classify the merchant's situation and return one concrete recommendation.

    Priority order (first match wins):
    1. Has a high-intent-no-purchase product → address the checkout blocker
    2. Revenue dropped significantly WoW → diagnose the drop
    3. High traffic, low conversion → optimize product pages
    4. Low traffic, decent conversion → invest in traffic
    5. Orders but zero visitors tracked → fix tracking setup
    6. First week with orders → celebrate + set up for next week
    7. Fallback → review dashboard
    """
    orders = this_week["order_count"]
    rev = this_week["revenue"]
    cvr = conversion_rate

    # 1. Cart abandonment opportunity
    if insight and insight.get("type") == "high_intent_no_purchase":
        name = insight["product_name"]
        count = insight["atc_visitors"]
        return {
            "headline": "Recover abandoned carts",
            "body": (
                f'"{name}" had {count} add-to-carts but no purchases this week. '
                f"Consider adding a limited-time offer, reviewing the checkout experience, "
                f"or sending a cart recovery email for this product."
            ),
        }

    # 2. Revenue dropped >20% WoW
    if rev_delta is not None and rev_delta <= -20:
        return {
            "headline": "Revenue dipped — here's where to look",
            "body": (
                f"Revenue fell {abs(rev_delta)}% compared to last week. "
                f"Check whether a traffic source dropped off, a popular product went "
                f"out of stock, or a recent site change affected the checkout flow. "
                f"Open your dashboard to see which products and sources shifted."
            ),
        }

    # 3. High traffic (50+), low conversion (<2%)
    if visitors >= 50 and cvr is not None and cvr < 2.0 and orders > 0:
        return {
            "headline": "Visitors are coming — let's convert more",
            "body": (
                f"You had {visitors:,} visitors but only {orders} orders ({cvr}% conversion). "
                f"Focus on your top-viewed product pages: are images high quality? "
                f"Is pricing clear? Adding social proof (reviews, badges) or a "
                f"time-limited incentive can meaningfully lift conversion."
            ),
        }

    # 4. Low traffic (<20), but decent conversion (>3%)
    if visitors > 0 and visitors < 20 and cvr is not None and cvr >= 3.0:
        return {
            "headline": "Your conversion is strong — send more traffic",
            "body": (
                f"Your {cvr}% conversion rate is solid, but only {visitors} visitors "
                f"came this week. Even a small traffic increase could drive meaningful "
                f"revenue. Consider a social media post, email campaign, or a small "
                f"paid ad test to bring more visitors to your best-converting pages."
            ),
        }

    # 5. Orders exist but no visitors tracked (tracking gap)
    if orders > 0 and visitors == 0:
        return {
            "headline": "We noticed a tracking gap",
            "body": (
                f"You had {orders} orders this week ({currency} {rev:,.2f} revenue) "
                f"but we didn't track any visitor sessions. This usually means the "
                f"HedgeSpark tracker script isn't loading on your storefront. "
                f"Check Settings → Customer events in your Shopify admin, or run a "
                f"repair from your HedgeSpark dashboard."
            ),
        }

    # 6. First week with orders (last_week had 0)
    if orders > 0 and last_week["order_count"] == 0:
        return {
            "headline": "Great start — keep the momentum",
            "body": (
                f"Your first tracked orders are in: {orders} orders for "
                f"{currency} {rev:,.2f}. As more data flows in over the next "
                f"few weeks, HedgeSpark will identify your highest-converting "
                f"products, flag checkout drop-offs, and surface revenue opportunities."
            ),
        }

    # 7. Revenue grew >20% WoW
    if rev_delta is not None and rev_delta >= 20:
        return {
            "headline": "Strong growth — double down on what's working",
            "body": (
                f"Revenue is up {rev_delta}% from last week. Check your dashboard "
                f"to see which traffic sources and products drove the increase, "
                f"and consider amplifying those channels while they're hot."
            ),
        }

    # 8. Fallback — always returns something
    return {
        "headline": "Your week at a glance",
        "body": (
            f"You had {orders} orders totaling {currency} {rev:,.2f} this week. "
            f"Open your HedgeSpark dashboard to explore per-product performance, "
            f"traffic source quality, and visitor behavior patterns."
        ),
    }


def _get_sip_insights(db: Session, shop_domain: str) -> list[dict]:
    """Load SIP-powered intelligence insights for the weekly digest (non-fatal)."""
    try:
        from app.services.intelligence_report import generate_merchant_intelligence
        return generate_merchant_intelligence(db, shop_domain)
    except Exception as exc:
        log.warning(
            "weekly_digest._get_sip_insights: shop=%s failed (%s): %s",
            shop_domain, type(exc).__name__, str(exc)[:200],
        )
        return []
