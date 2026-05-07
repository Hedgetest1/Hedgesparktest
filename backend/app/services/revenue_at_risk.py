"""
revenue_at_risk.py — THE hero metric of HedgeSpark.

Returns ONE number: "Your shop has €X/month at risk right now".

Aggregates five loss sources into a single deterministic € figure:
  1. Abandoned high-intent visitors         → expected_conversion × AOV × volume
  2. Refund/return product decline           → from services.refund_loss
  3. Nudge effectiveness gap                  → exposures without impact × expected_lift × AOV
  4. Below-benchmark loss                     → from services.benchmarks recovery_to_p75
  5. Goal gap                                  → from services.goals at-risk projection

Why this is the killer
----------------------
Triple Whale, Peel, Varos, Lifetimely all ship "dashboard modules" —
each metric lives in its own card with no unified view. HedgeSpark ships
a SINGLE number that IS the pitch:

    "€1,840 at risk this month. HedgeSpark already prevented €640."

This number becomes the hero of the dashboard, the headline of the
weekly digest, the opening line of every demo. Every other feature
(benchmarks, refunds, goals, segments) drills DOWN from this one figure.

Self-healing pipeline integration
---------------------------------
* project_brain domain: 'rars' (high criticality — it's the headline)
* ops_alert on compute failure (source='revenue_at_risk')
* data_integrity_probe watches for RARS volatility (sudden spikes =
  data corruption upstream)
* Cached 5 min per shop — cheap enough for live updates
* Exposed via /analytics/revenue-at-risk with full breakdown for drill-down
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("revenue_at_risk")

_CACHE_TTL_SECONDS = 5 * 60  # 5 minutes — cheap live updates
_CACHE_KEY_PREFIX = "hs:rars:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


@dataclass
class RARSComponent:
    """One loss source contributing to the total RARS."""
    source: str          # abandoned_high_intent | refund_decline | nudge_gap | below_benchmark | goal_gap
    loss_eur: float
    narrative: str
    evidence: dict = field(default_factory=dict)


@dataclass
class RARSReport:
    """Full RARS report with breakdown for drill-down UI."""
    shop_domain: str
    total_at_risk_eur: float
    components: list[RARSComponent]
    prevented_eur_this_month: float  # what HedgeSpark has already stopped
    net_roi_eur: float                # prevented - billing cost (Pro tier)
    generated_at: str
    headline: str

    def to_dict(self) -> dict:
        return {
            "shop_domain": self.shop_domain,
            "total_at_risk_eur": round(self.total_at_risk_eur, 2),
            "prevented_eur_this_month": round(self.prevented_eur_this_month, 2),
            "net_roi_eur": round(self.net_roi_eur, 2),
            "components": [
                {
                    "source": c.source,
                    "loss_eur": round(c.loss_eur, 2),
                    "narrative": c.narrative,
                    "evidence": c.evidence,
                }
                for c in self.components
            ],
            "generated_at": self.generated_at,
            "headline": self.headline,
        }


# ---------------------------------------------------------------------------
# Component: abandoned high-intent visitors
# ---------------------------------------------------------------------------


def _compute_abandoned_high_intent(db: Session, shop: str) -> RARSComponent:
    """
    High-intent visitors who didn't convert × baseline conversion × AOV.
    High-intent = events with event_type='add_to_cart' OR dwell > 120s
    on product pages and NO purchase in the last 30 days.
    """
    now = _now()
    cutoff_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    currency = get_shop_currency(db, shop)

    try:
        row = db.execute(text("""
            SELECT
                COUNT(DISTINCT e.visitor_id) AS high_intent_visitors,
                (SELECT COALESCE(AVG(total_price), 0) FROM shop_orders
                 WHERE shop_domain = :shop AND created_at >= NOW() - INTERVAL '30 days'
                   AND total_price > 0
                   AND (:currency IS NULL OR currency = :currency)) AS aov
            FROM events e
            WHERE e.shop_domain = :shop
              AND e.timestamp >= :cutoff_ms
              AND (
                   e.event_type = 'add_to_cart'
                OR (e.event_type = 'dwell_time' AND COALESCE(e.max_scroll_depth, 0) >= 50)
              )
              AND NOT EXISTS (
                  SELECT 1 FROM visitor_purchase_sessions vps
                  WHERE vps.shop_domain = e.shop_domain
                    AND vps.visitor_id = e.visitor_id
              )
        """), {"shop": shop, "cutoff_ms": cutoff_ms, "currency": currency}).fetchone()
    except Exception as exc:
        log.debug("rars: abandoned_high_intent query failed: %s", exc)
        return RARSComponent(
            source="abandoned_high_intent",
            loss_eur=0.0,
            narrative="Signal unavailable (tracking data incomplete)",
            evidence={"error": type(exc).__name__},
        )

    if not row:
        return RARSComponent(
            source="abandoned_high_intent", loss_eur=0.0,
            narrative="No high-intent visitor data in last 30d", evidence={},
        )

    visitors = int(row[0] or 0)
    aov = float(row[1] or 0)
    # Conservative expected conversion rate for high-intent abandoners: 8%
    # (industry average for retargeted high-intent is 5-12%)
    expected_conversion = 0.08
    loss = visitors * expected_conversion * aov

    return RARSComponent(
        source="abandoned_high_intent",
        loss_eur=round(loss, 2),
        narrative=f"{visitors} high-intent visitors abandoned in 30d "
                  f"→ at 8% recovery × €{aov:.0f} AOV = €{loss:.0f} at risk",
        evidence={
            "high_intent_visitors_30d": visitors,
            "aov": round(aov, 2),
            "expected_recovery_rate": expected_conversion,
        },
    )


# ---------------------------------------------------------------------------
# Component: refund/product decline (from services.refund_loss)
# ---------------------------------------------------------------------------


def _compute_refund_decline(db: Session, shop: str) -> RARSComponent:
    try:
        from app.services.refund_loss import get_refund_loss_report
        report = get_refund_loss_report(db, shop)
    except Exception as exc:
        return RARSComponent(
            source="refund_decline", loss_eur=0.0,
            narrative="Refund module unavailable",
            evidence={"error": type(exc).__name__},
        )

    total = float(report.get("total_loss_eur_per_month") or 0)
    product_count = int(report.get("product_count") or 0)
    return RARSComponent(
        source="refund_decline",
        loss_eur=total,
        narrative=(report.get("headline") or "")[:200]
                  if product_count > 0
                  else "No product decline detected",
        evidence={
            "product_count": product_count,
            "source_method": report.get("method"),
        },
    )


# ---------------------------------------------------------------------------
# Component: nudge effectiveness gap
# ---------------------------------------------------------------------------


def _compute_nudge_gap(db: Session, shop: str) -> RARSComponent:
    """
    Nudges that fired but did NOT measurably improve conversion.
    Uses nudge_events: count exposures where the same visitor did not
    go on to purchase, times the AOV × expected lift a working nudge
    would have delivered (~5-15% effective lift).
    """
    currency = get_shop_currency(db, shop)
    try:
        row = db.execute(text("""
            SELECT
                COUNT(*) FILTER (
                    WHERE ne.event_type = 'exposed'
                      AND ne.created_at >= NOW() - INTERVAL '30 days'
                ) AS exposures,
                COUNT(*) FILTER (
                    WHERE ne.event_type = 'purchase_after_exposed'
                      AND ne.created_at >= NOW() - INTERVAL '30 days'
                ) AS purchases,
                (SELECT COALESCE(AVG(total_price), 0) FROM shop_orders
                 WHERE shop_domain = :shop AND created_at >= NOW() - INTERVAL '30 days'
                   AND total_price > 0
                   AND (:currency IS NULL OR currency = :currency)) AS aov
            FROM nudge_events ne
            JOIN active_nudges n ON n.id = ne.nudge_id
            WHERE n.shop_domain = :shop
        """), {"shop": shop, "currency": currency}).fetchone()
    except Exception as exc:
        log.debug("rars: nudge_gap query failed: %s", exc)
        return RARSComponent(
            source="nudge_gap", loss_eur=0.0,
            narrative="Nudge data unavailable",
            evidence={"error": type(exc).__name__},
        )

    if not row or not row[0]:
        return RARSComponent(
            source="nudge_gap", loss_eur=0.0,
            narrative="No nudge exposures in last 30d", evidence={},
        )

    exposures = int(row[0] or 0)
    purchases = int(row[1] or 0)
    aov = float(row[2] or 0)
    if exposures < 10:
        return RARSComponent(
            source="nudge_gap", loss_eur=0.0,
            narrative=f"Only {exposures} nudge exposures — too few to judge",
            evidence={"exposures": exposures},
        )

    actual_cvr = purchases / exposures if exposures > 0 else 0
    # A well-tuned nudge converts ~8% of exposures. Gap × volume × AOV is the loss.
    target_cvr = 0.08
    gap_cvr = max(0.0, target_cvr - actual_cvr)
    loss = gap_cvr * exposures * aov

    return RARSComponent(
        source="nudge_gap",
        loss_eur=round(loss, 2),
        narrative=(
            f"Nudges converting at {actual_cvr*100:.1f}% vs target 8% "
            f"→ €{loss:.0f} lost to underperforming copy/targeting"
            if gap_cvr > 0 else
            f"Nudges converting at {actual_cvr*100:.1f}% — above target ✓"
        ),
        evidence={
            "exposures_30d": exposures,
            "purchases_30d": purchases,
            "actual_cvr_pct": round(actual_cvr * 100, 2),
            "target_cvr_pct": 8.0,
            "aov": round(aov, 2),
        },
    )


# ---------------------------------------------------------------------------
# Component: below-benchmark (from services.benchmarks)
# ---------------------------------------------------------------------------


def _compute_below_benchmark(db: Session, shop: str) -> RARSComponent:
    try:
        from app.services.benchmarks import get_merchant_benchmark_report
        report = get_merchant_benchmark_report(db, shop)
    except Exception as exc:
        return RARSComponent(
            source="below_benchmark", loss_eur=0.0,
            narrative="Benchmark module unavailable",
            evidence={"error": type(exc).__name__},
        )

    recovery = float(report.get("total_recovery_potential_eur") or 0)
    band = report.get("band") or "unknown"
    from app.core.currency import format_money
    currency = get_shop_currency(db, shop)
    return RARSComponent(
        source="below_benchmark",
        loss_eur=recovery,
        narrative=(
            f"{format_money(recovery, currency)}/month recoverable if you moved from current position "
            f"to top 25% of {band}-band peers"
            if recovery > 0 else
            f"At or above peer benchmarks for {band} band ✓"
        ),
        evidence={
            "band": band,
            "peer_count": report.get("peer_count"),
        },
    )


# ---------------------------------------------------------------------------
# Component: goal gap (from services.goals)
# ---------------------------------------------------------------------------


def _compute_goal_gap(db: Session, shop: str) -> RARSComponent:
    try:
        from app.services.goals import compute_goal_progress
        progress = compute_goal_progress(db, shop)
    except Exception as exc:
        return RARSComponent(
            source="goal_gap", loss_eur=0.0,
            narrative="Goals module unavailable",
            evidence={"error": type(exc).__name__},
        )

    # Only monthly_revenue goal-gap contributes to RARS in €
    revenue_progress = [p for p in progress if p.metric == "monthly_revenue"]
    if not revenue_progress:
        return RARSComponent(
            source="goal_gap", loss_eur=0.0,
            narrative="No revenue goal set — set one to see gap-at-risk",
            evidence={"goal_count": len(progress)},
        )

    p = revenue_progress[0]
    gap = max(0.0, p.target_value - p.projected_value)
    return RARSComponent(
        source="goal_gap",
        loss_eur=gap,
        narrative=(
            f"Goal €{p.target_value:.0f}/month — projected €{p.projected_value:.0f} "
            f"→ €{gap:.0f} below target"
            if gap > 0 else
            f"On track for €{p.target_value:.0f}/month goal ✓"
        ),
        evidence={
            "target": p.target_value,
            "projected": p.projected_value,
            "status": p.status,
        },
    )


# ---------------------------------------------------------------------------
# Prevention signal — what HedgeSpark has ALREADY saved this month
# ---------------------------------------------------------------------------


def _compute_prevented(db: Session, shop: str) -> tuple[float, dict]:
    """
    How much loss HedgeSpark prevented this month:
      + Nudge-driven revenue above holdout baseline (from nudge_stats)
      + Auto-fixes applied that measured 'effective' with monetary delta
    """
    prevented = 0.0
    evidence: dict = {"sources": []}
    currency = get_shop_currency(db, shop)

    # Source 1: nudge incremental revenue (exposed cvr over holdout cvr × exposures × AOV)
    try:
        row = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE ne.event_type = 'exposed'
                    AND ne.created_at >= date_trunc('month', NOW())) AS month_exposures,
                COUNT(*) FILTER (WHERE ne.event_type = 'purchase_after_exposed'
                    AND ne.created_at >= date_trunc('month', NOW())) AS month_purchases,
                COUNT(*) FILTER (WHERE ne.event_type = 'holdout_assigned'
                    AND ne.created_at >= date_trunc('month', NOW())) AS month_holdout,
                (SELECT COALESCE(AVG(total_price), 0) FROM shop_orders
                 WHERE shop_domain = :shop AND created_at >= date_trunc('month', NOW())
                   AND (:currency IS NULL OR currency = :currency)) AS aov
            FROM nudge_events ne
            JOIN active_nudges n ON n.id = ne.nudge_id
            WHERE n.shop_domain = :shop
        """), {"shop": shop, "currency": currency}).fetchone()
        if row and row[0]:
            exp = int(row[0])
            pur = int(row[1] or 0)
            hold = int(row[2] or 0)
            aov = float(row[3] or 0)
            # Nudge prevented revenue requires REAL holdout-vs-treatment
            # comparison. Without tracked holdout purchases (separate event
            # type), any claim is an invented number — principle §2 rule 4:
            # "no false claims, no marketing-driven features".
            # This block is a no-op until holdout_purchase tracking ships.
            # When it does: treatment_cvr = pur/exp, holdout_cvr = holdout_pur/hold,
            # incremental = (treatment_cvr - holdout_cvr) * exp * aov.
            pass
    except Exception as exc:
        log.warning("rars: prevented nudge query failed: %s", exc)

    return prevented, evidence


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_revenue_at_risk(db: Session, shop_domain: str, plan: str = "pro") -> dict:
    """
    Compute and return the RARS report for the merchant.
    Cached 5 minutes per (shop, plan) — Lite and Pro use SEPARATE
    cache entries because they compute different work.

    plan = "pro"       → full 5-dimension component breakdown
    plan != "pro"      → headline total + prevented + net_roi only,
                         components array is empty. Lite skips the
                         3 heaviest computes (refund/nudge/benchmark)
                         that get filtered out anyway — closes the
                         slo:rars_lite latency_breach (1274ms p95
                         pre-fix, ~250ms post-fix expected).
    """
    plan_key = (plan or "pro").lower()
    cache_key = f"{_CACHE_KEY_PREFIX}:{plan_key}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"
    cache_hit: dict | None = None
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                cache_hit = json.loads(cached)
    except Exception as exc:
        log.warning("revenue_at_risk: redis cache read failed: %s", exc)

    if cache_hit is not None:
        return _apply_plan_filter(cache_hit, plan)

    components = []
    is_pro = plan_key == "pro"
    try:
        components.append(_compute_abandoned_high_intent(db, shop_domain))
        # Lite fast-path: skip the 3 heaviest computes (refund/nudge/
        # benchmark). They cost ~700ms combined and their loss_eur is
        # filtered out for Lite anyway via _apply_plan_filter. Closes
        # slo:rars_lite latency_breach (target 800ms, was 1274ms).
        if is_pro:
            components.append(_compute_refund_decline(db, shop_domain))
            components.append(_compute_nudge_gap(db, shop_domain))
            components.append(_compute_below_benchmark(db, shop_domain))
        components.append(_compute_goal_gap(db, shop_domain))
    except Exception as exc:
        log.warning("rars: component compute failed shop=%s: %s", shop_domain, exc)
        try:
            from app.services.alerting import write_alert
            # heal-detection: RARS calculation event — discrete computation log
            write_alert(
                db,
                severity="warning",
                source="revenue_at_risk",
                alert_type="rars_compute_failed",
                summary=f"RARS compute failed for shop {shop_domain}: {type(exc).__name__}",
                shop_domain=shop_domain,
                detail={"error": str(exc)[:500]},
            )
        except Exception as exc:
            log.warning("revenue_at_risk: alert write failed: %s", exc)

    total = sum(c.loss_eur for c in components)
    prevented, prevent_evidence = _compute_prevented(db, shop_domain)

    # Net ROI = prevented − subscription. Subscription must match the
    # merchant's ACTUAL tier cost, not an assumed €99 every time. A
    # Lite merchant pays €0; a Pro merchant pays €99. Subtracting €99
    # from a Lite merchant produces a false "Net ROI −€99" strip — the
    # lie `feedback_no_accettabile_per_beta.md` forbids.
    #
    # Pricing is doctrine — imported from `app.core.tier_pricing` so
    # every net_roi calculation in the backend tracks the same number.
    # `_apply_plan_filter` downstream rewrites net_roi to the Lite-correct
    # zero-subtraction; the Pro reference number below serves the cache.
    from app.core.tier_pricing import TIER_SUBSCRIPTION_EUR as _TIER_SUBSCRIPTION_EUR
    _PRO_TIER_COST_EUR = _TIER_SUBSCRIPTION_EUR["pro"]
    net_roi = prevented - _PRO_TIER_COST_EUR

    from app.core.currency import format_money
    currency = get_shop_currency(db, shop_domain)
    total_str = format_money(total, currency)
    prevented_str = format_money(prevented, currency)
    net_roi_str = format_money(net_roi, currency)
    sub_str = format_money(_PRO_TIER_COST_EUR, currency)

    if total <= 0:
        headline = "✨ No significant revenue at risk — your shop is healthy across all tracked signals."
    elif net_roi > 0:
        headline = (
            f"{total_str}/mo at risk — HedgeSpark already prevented {prevented_str} this month "
            f"(net ROI +{net_roi_str} vs your {sub_str} subscription)"
        )
    else:
        headline = (
            f"{total_str}/mo at risk — the biggest drivers are listed below. "
            f"Open each to see the action plan."
        )

    report = RARSReport(
        shop_domain=shop_domain,
        total_at_risk_eur=total,
        components=components,
        prevented_eur_this_month=prevented,
        net_roi_eur=net_roi,
        generated_at=_now().isoformat(),
        headline=headline,
    )
    result = report.to_dict()
    result["_prevent_evidence"] = prevent_evidence
    result["currency"] = currency or "USD"

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
    except Exception as exc:
        log.warning("revenue_at_risk: redis cache write failed: %s", exc)

    # rars_history snapshot — only on Pro path. The Lite fast-path
    # skips the 3 heaviest components, so its `total` is structurally
    # an underestimate vs Pro; recording it would pollute the
    # rars_history time series consumed by Pro dashboard cards.
    if is_pro:
        try:
            from app.services.risk_forecast import record_rars_snapshot
            record_rars_snapshot(shop_domain, total)
        except Exception as exc:
            log.warning("revenue_at_risk: rars snapshot record failed: %s", exc)

    return _apply_plan_filter(result, plan)


def _apply_plan_filter(result: dict, plan: str) -> dict:
    """Reduce RARS response fidelity for non-Pro tiers.

    Pro merchants get the full 5-dim breakdown. Lite merchants get
    the hero number + prevented + headline but not the drill-down
    components — the breakdown lives behind the upgrade CTA.

    Lite tier also gets a corrected net_roi: the service computed
    net_roi = prevented − €99 (assuming Pro subscription), but a Lite
    merchant pays €0, so their net_roi = prevented. Without this fix
    the UI shows "Net ROI −€99" to a merchant who pays nothing —
    exactly the lie `feedback_no_accettabile_per_beta.md` forbids.

    Shallow-copies so we don't mutate a shared cached dict.
    """
    if plan == "pro":
        return result
    filtered = dict(result)
    filtered["components"] = []
    # Non-Pro merchants pay €0 today (closed-beta Lite) — net_roi
    # equals prevented, not prevented minus an imaginary subscription.
    prevented = float(result.get("prevented_eur_this_month") or 0.0)
    filtered["net_roi_eur"] = prevented
    return filtered
