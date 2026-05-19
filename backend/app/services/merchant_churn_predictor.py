"""
merchant_churn_predictor.py — Predict which merchants will churn.

Meta-intelligence: we use the same behavioral analysis we offer
merchants... on our OWN merchants. Engagement decay, revenue
trajectory, digest interaction, dashboard silence — all signals.

No ML model needed. Rule-based scoring with empirical thresholds.
The beauty: we can PROVE our own churn prevention works because
we have holdout-grade measurement infrastructure.

Inputs:
  - merchant.installed_at, uninstalled_at, billing_active
  - merchant_email_stats (digest engagement)
  - shop_orders (revenue trajectory)
  - events (tracker activity = merchant's store is live)
  - merchant_journey_state (onboarding progress)

Output: 0-100 churn risk score + actionable signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("merchant_churn")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# compute_churn_score — stage helpers
# Refactor 2026-05-13 (A3 close): 215-LOC god function → composer + 7
# pure stage helpers (5 signal scorers + risk classifier + signal merger).
# Contract preserved byte-identical. Silent exception fallbacks now
# observed via record_silent_return — unknown-state defaults remain
# the documented behavior (moderate risk on query failure) but a
# spike in failures surfaces in metrics.
# ---------------------------------------------------------------------------


_REVENUE_SQL = text("""
    SELECT
        COALESCE(SUM(CASE WHEN created_at >= :recent THEN total_price ELSE 0 END), 0) as rev_recent,
        COALESCE(SUM(CASE WHEN created_at < :recent THEN total_price ELSE 0 END), 0) as rev_prior,
        COUNT(*) FILTER (WHERE created_at >= :recent) as orders_recent,
        COUNT(*) FILTER (WHERE created_at < :recent) as orders_prior
    FROM shop_orders
    WHERE shop_domain = :shop AND created_at >= :prior
""")


_TRACKER_SQL = text("""
    SELECT
        COUNT(*) as total_events,
        COUNT(*) FILTER (WHERE to_timestamp(timestamp/1000) >= :week_ago) as events_7d,
        MAX(to_timestamp(timestamp/1000)) as last_event
    FROM events
    WHERE shop_domain = :shop
      AND to_timestamp(timestamp/1000) >= :month_ago
""")


_DIGEST_SQL = text("""
    SELECT sent_count, opened_count, clicked_count
    FROM merchant_email_stats
    WHERE shop_domain = :shop
    ORDER BY id DESC LIMIT 1
""")


_MERCHANT_SQL = text("""
    SELECT installed_at, billing_active, plan
    FROM merchants
    WHERE shop_domain = :shop
    LIMIT 1
""")


_JOURNEY_SQL = text("""
    SELECT current_stage
    FROM merchant_journey_states
    WHERE shop_domain = :shop
    ORDER BY id DESC LIMIT 1
""")


def _record_unknown(component: str) -> None:
    """Observe a 'query failed → unknown state default' fallback so a
    spike in failures becomes visible instead of hiding behind moderate
    default scores."""
    from app.core.silent_fallback import record_silent_return
    record_silent_return(f"merchant_churn.{component}_unknown")


def _classify_revenue_change(rev_recent: float, rev_prior: float) -> float:
    """Compute WoW % change with sentinel values for two edge cases:
    rev_prior=0 + rev_recent>0 → +100 (clean growth from 0);
    both 0 → -100 (no revenue at all = high risk anchor)."""
    if rev_prior > 0:
        return ((rev_recent - rev_prior) / rev_prior) * 100
    if rev_recent > 0:
        return 100
    return -100


def _score_revenue(db: Session, shop_domain: str, now: datetime) -> tuple[dict, list]:
    """Revenue trajectory scorer (30 pts max)."""
    try:
        recent = now - timedelta(days=14)
        prior = now - timedelta(days=28)
        row = db.execute(_REVENUE_SQL, {
            "shop": shop_domain, "recent": recent, "prior": prior,
        }).fetchone()
        rev_recent = float(row[0] or 0)
        rev_prior = float(row[1] or 0)
        orders_recent = row[2] or 0
        orders_prior = row[3] or 0
    except Exception:
        _record_unknown("revenue")
        return {"revenue": 15}, []

    rev_change = _classify_revenue_change(rev_recent, rev_prior)
    signals: list[dict] = []
    if rev_change <= -50:
        components = {"revenue": 30}
        signals.append({"signal": "revenue_collapse",
                        "detail": f"Revenue dropped {rev_change:.0f}% WoW", "weight": 30})
    elif rev_change <= -20:
        components = {"revenue": 20}
        signals.append({"signal": "revenue_declining",
                        "detail": f"Revenue dropped {rev_change:.0f}% WoW", "weight": 20})
    elif rev_change <= 0:
        components = {"revenue": 10}
        signals.append({"signal": "revenue_flat",
                        "detail": "Revenue stagnant or slightly down", "weight": 10})
    else:
        components = {"revenue": 0}

    # zero_orders override: dominates any prior revenue classification
    # because absence of orders for the full 28d window is the strongest
    # single signal we have (it implies the store isn't transacting at all).
    if orders_recent == 0 and orders_prior == 0:
        components["revenue"] = 25
        signals.append({"signal": "zero_orders",
                        "detail": "No orders in 28 days", "weight": 25})
    return components, signals


def _score_tracker(db: Session, shop_domain: str, now: datetime) -> tuple[dict, list]:
    """Storefront tracker activity scorer (25 pts max)."""
    try:
        row = db.execute(_TRACKER_SQL, {
            "shop": shop_domain,
            "week_ago": now - timedelta(days=7),
            "month_ago": now - timedelta(days=30),
        }).fetchone()
        events_7d = row[1] or 0
        last_event = row[2]
        days_silent = (now - last_event).days if last_event else 999
    except Exception:
        _record_unknown("tracker")
        return {"tracker": 12}, []

    signals: list[dict] = []
    if days_silent > 14:
        components = {"tracker": 25}
        signals.append({"signal": "tracker_dead",
                        "detail": f"No tracker events for {days_silent} days", "weight": 25})
    elif days_silent > 7:
        components = {"tracker": 15}
        signals.append({"signal": "tracker_declining",
                        "detail": f"No events for {days_silent} days", "weight": 15})
    elif events_7d < 10:
        components = {"tracker": 10}
        signals.append({"signal": "low_traffic",
                        "detail": f"Only {events_7d} events in 7 days", "weight": 10})
    else:
        components = {"tracker": 0}
    return components, signals


def _score_digest(db: Session, shop_domain: str) -> tuple[dict, list]:
    """Digest email engagement scorer (20 pts max)."""
    try:
        row = db.execute(_DIGEST_SQL, {"shop": shop_domain}).fetchone()
    except Exception:
        _record_unknown("digest")
        return {"digest": 10}, []

    if row is None:
        return {"digest": 10}, []  # no stats = concerning

    sent = row[0] or 0
    opened = row[1] or 0
    clicked = row[2] or 0
    if sent < 3:
        return {"digest": 5}, []  # too few sends to judge

    open_rate = opened / sent if sent > 0 else 0
    click_rate = clicked / sent if sent > 0 else 0
    signals: list[dict] = []
    if open_rate < 0.1:
        components = {"digest": 20}
        signals.append({"signal": "digest_ignored",
                        "detail": f"Open rate {open_rate*100:.0f}% (sent {sent})", "weight": 20})
    elif open_rate < 0.3:
        components = {"digest": 10}
        signals.append({"signal": "digest_low_engagement",
                        "detail": f"Open rate {open_rate*100:.0f}%", "weight": 10})
    elif click_rate < 0.05:
        components = {"digest": 5}
        signals.append({"signal": "digest_opens_no_clicks",
                        "detail": "Opens but never clicks", "weight": 5})
    else:
        components = {"digest": 0}
    return components, signals


def _score_tenure_billing(
    db: Session, shop_domain: str, now: datetime,
) -> tuple[dict, list]:
    """Tenure + billing scorer (15 + 10 pts max). Two components,
    surfaced as separate score_breakdown keys."""
    try:
        row = db.execute(_MERCHANT_SQL, {"shop": shop_domain}).fetchone()
    except Exception:
        _record_unknown("tenure_billing")
        return {"tenure": 5, "billing": 0}, []

    if row is None:
        return {"tenure": 5, "billing": 5}, []

    installed_at = row[0]
    billing_active = row[1]
    components: dict = {}
    signals: list[dict] = []

    if installed_at:
        tenure_days = (now - installed_at).days
        if tenure_days < 14:
            components["tenure"] = 15
            signals.append({"signal": "new_install",
                            "detail": f"Installed {tenure_days} days ago — critical window",
                            "weight": 15})
        elif tenure_days < 30:
            components["tenure"] = 8
            signals.append({"signal": "early_tenure",
                            "detail": f"Installed {tenure_days} days ago", "weight": 8})
        else:
            components["tenure"] = 0

    if not billing_active:
        components["billing"] = 10
        signals.append({"signal": "billing_inactive",
                        "detail": "Billing not active", "weight": 10})
    else:
        components["billing"] = 0
    return components, signals


def _score_onboarding(db: Session, shop_domain: str) -> tuple[dict, list]:
    """Onboarding completeness scorer (10 pts max)."""
    try:
        row = db.execute(_JOURNEY_SQL, {"shop": shop_domain}).fetchone()
    except Exception:
        _record_unknown("onboarding")
        return {"onboarding": 5}, []

    if row is None:
        return {"onboarding": 5}, []

    stage = row[0] or ""
    completed_stages = {"active", "activated_lite", "activated_pro", "replied"}
    if stage in completed_stages:
        return {"onboarding": 0}, []
    return (
        {"onboarding": 10},
        [{"signal": "onboarding_incomplete",
          "detail": f"Stage: {stage}", "weight": 10}],
    )


def _classify_risk_level(total_score: int) -> tuple[str, str]:
    """Score → (risk_level, recommended_action) tuple."""
    if total_score >= 70:
        return (
            "critical",
            "Immediate outreach needed — merchant is likely to churn within 7 days.",
        )
    if total_score >= 50:
        return (
            "high",
            "Send personalized re-engagement email with specific value proposition.",
        )
    if total_score >= 30:
        return (
            "moderate",
            "Monitor weekly. Consider proactive check-in if score increases.",
        )
    return (
        "low",
        "Merchant is healthy. Continue normal digest cadence.",
    )


def compute_churn_score(db: Session, shop_domain: str) -> dict:
    """
    Compute churn risk score for a single merchant.

    Returns:
      {
        shop_domain, churn_risk_score (0-100), risk_level,
        signals: [...], recommended_action, computed_at
      }

    Refactored 2026-05-13 (A3 close): 215-LOC god function → 25-LOC
    composer + 7 pure helpers (5 signal scorers + revenue change
    classifier + risk-level classifier).
    """
    now = _now()
    all_components: dict[str, int] = {}
    all_signals: list[dict] = []

    for scorer_components, scorer_signals in (
        _score_revenue(db, shop_domain, now),
        _score_tracker(db, shop_domain, now),
        _score_digest(db, shop_domain),
        _score_tenure_billing(db, shop_domain, now),
        _score_onboarding(db, shop_domain),
    ):
        all_components.update(scorer_components)
        all_signals.extend(scorer_signals)

    total_score = min(100, sum(all_components.values()))
    risk_level, action = _classify_risk_level(total_score)
    all_signals.sort(key=lambda s: s["weight"], reverse=True)

    return {
        "shop_domain": shop_domain,
        "churn_risk_score": total_score,
        "risk_level": risk_level,
        "signals": all_signals[:5],
        "score_breakdown": all_components,
        "recommended_action": action,
        "computed_at": now.isoformat(),
    }


def compute_churn_report(db: Session) -> dict:
    """
    Compute churn risk for ALL active merchants. Used by monthly audit
    and operator dashboard.

    Returns:
      {
        total_merchants, at_risk_count, critical_count,
        merchants: [...], summary, computed_at
      }
    """
    # Get all active merchants. Born-bug fix 2026-05-17: this filtered
    # `install_status='installed'` while the canonical lifecycle value
    # is 'active' (53 call sites use 'active'; NO merchant is ever
    # 'installed' — verified live + the comment above says "active").
    # Effect: compute_churn_report returned ZERO merchants in prod, so
    # the batched churn map was always empty and EVERY Brain Vero
    # decision ran with churn_level='unknown' (churn-gated rules
    # silently never fired). Surfaced by the 2026-05-17 capillary audit.
    merchants = db.execute(text("""
        SELECT shop_domain FROM merchants
        WHERE install_status = 'active'
        ORDER BY installed_at DESC
        LIMIT 500
    """)).fetchall()

    from app.core.database import rollback_quiet
    results = []
    for row in merchants:
        try:
            score = compute_churn_score(db, row[0])
            if score["churn_risk_score"] > 0:
                results.append(score)
        except Exception as exc:
            log.warning("Churn score failed for %s: %s", row[0], exc)
            # write_no_rollback class close 2026-05-19: read-only
            # scoring loop, but a conn-death / PendingRollbackError
            # mid-loop (the sentry #239 trigger class) would poison the
            # shared session for every remaining merchant + the caller
            # (merchant_brain.py:1339 → whole Brain Vero cycle). Un-poison.
            rollback_quiet(db)

    results.sort(key=lambda r: r["churn_risk_score"], reverse=True)

    critical = [r for r in results if r["risk_level"] == "critical"]
    high = [r for r in results if r["risk_level"] == "high"]
    moderate = [r for r in results if r["risk_level"] == "moderate"]

    return {
        "total_merchants": len(merchants),
        "at_risk_count": len(critical) + len(high),
        "critical_count": len(critical),
        "high_count": len(high),
        "moderate_count": len(moderate),
        "merchants": results[:20],
        "summary": (
            f"{len(critical)} critical, {len(high)} high risk, "
            f"{len(moderate)} moderate out of {len(merchants)} merchants."
        ),
        "computed_at": _now().isoformat(),
    }
