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


def compute_churn_score(db: Session, shop_domain: str) -> dict:
    """
    Compute churn risk score for a single merchant.

    Returns:
      {
        shop_domain, churn_risk_score (0-100), risk_level,
        signals: [...], recommended_action, computed_at
      }
    """
    signals = []
    score_components = {}

    # --- 1. Revenue trajectory (30 pts max) ---
    try:
        now = _now()
        recent = now - timedelta(days=14)
        prior = now - timedelta(days=28)

        rev_row = db.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= :recent THEN total_price ELSE 0 END), 0) as rev_recent,
                COALESCE(SUM(CASE WHEN created_at < :recent THEN total_price ELSE 0 END), 0) as rev_prior,
                COUNT(*) FILTER (WHERE created_at >= :recent) as orders_recent,
                COUNT(*) FILTER (WHERE created_at < :recent) as orders_prior
            FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :prior
        """), {"shop": shop_domain, "recent": recent, "prior": prior}).fetchone()

        rev_recent = float(rev_row[0] or 0)
        rev_prior = float(rev_row[1] or 0)
        orders_recent = rev_row[2] or 0
        orders_prior = rev_row[3] or 0

        if rev_prior > 0:
            rev_change = ((rev_recent - rev_prior) / rev_prior) * 100
        elif rev_recent > 0:
            rev_change = 100
        else:
            rev_change = -100  # no revenue at all = high risk

        if rev_change <= -50:
            score_components["revenue"] = 30
            signals.append({"signal": "revenue_collapse", "detail": f"Revenue dropped {rev_change:.0f}% WoW", "weight": 30})
        elif rev_change <= -20:
            score_components["revenue"] = 20
            signals.append({"signal": "revenue_declining", "detail": f"Revenue dropped {rev_change:.0f}% WoW", "weight": 20})
        elif rev_change <= 0:
            score_components["revenue"] = 10
            signals.append({"signal": "revenue_flat", "detail": "Revenue stagnant or slightly down", "weight": 10})
        else:
            score_components["revenue"] = 0

        if orders_recent == 0 and orders_prior == 0:
            score_components["revenue"] = 25
            signals.append({"signal": "zero_orders", "detail": "No orders in 28 days", "weight": 25})
    except Exception:
        score_components["revenue"] = 15  # unknown = moderate risk

    # --- 2. Tracker activity (25 pts max) ---
    try:
        event_row = db.execute(text("""
            SELECT
                COUNT(*) as total_events,
                COUNT(*) FILTER (WHERE to_timestamp(timestamp/1000) >= :week_ago) as events_7d,
                MAX(to_timestamp(timestamp/1000)) as last_event
            FROM events
            WHERE shop_domain = :shop
              AND to_timestamp(timestamp/1000) >= :month_ago
        """), {
            "shop": shop_domain,
            "week_ago": now - timedelta(days=7),
            "month_ago": now - timedelta(days=30),
        }).fetchone()

        events_7d = event_row[1] or 0
        last_event = event_row[2]
        days_silent = (now - last_event).days if last_event else 999

        if days_silent > 14:
            score_components["tracker"] = 25
            signals.append({"signal": "tracker_dead", "detail": f"No tracker events for {days_silent} days", "weight": 25})
        elif days_silent > 7:
            score_components["tracker"] = 15
            signals.append({"signal": "tracker_declining", "detail": f"No events for {days_silent} days", "weight": 15})
        elif events_7d < 10:
            score_components["tracker"] = 10
            signals.append({"signal": "low_traffic", "detail": f"Only {events_7d} events in 7 days", "weight": 10})
        else:
            score_components["tracker"] = 0
    except Exception:
        score_components["tracker"] = 12

    # --- 3. Digest engagement (20 pts max) ---
    try:
        digest_row = db.execute(text("""
            SELECT sent_count, opened_count, clicked_count
            FROM merchant_email_stats
            WHERE shop_domain = :shop
            ORDER BY id DESC LIMIT 1
        """), {"shop": shop_domain}).fetchone()

        if digest_row:
            sent = digest_row[0] or 0
            opened = digest_row[1] or 0
            clicked = digest_row[2] or 0

            if sent >= 3:
                open_rate = opened / sent if sent > 0 else 0
                click_rate = clicked / sent if sent > 0 else 0

                if open_rate < 0.1:
                    score_components["digest"] = 20
                    signals.append({"signal": "digest_ignored", "detail": f"Open rate {open_rate*100:.0f}% (sent {sent})", "weight": 20})
                elif open_rate < 0.3:
                    score_components["digest"] = 10
                    signals.append({"signal": "digest_low_engagement", "detail": f"Open rate {open_rate*100:.0f}%", "weight": 10})
                elif click_rate < 0.05:
                    score_components["digest"] = 5
                    signals.append({"signal": "digest_opens_no_clicks", "detail": "Opens but never clicks", "weight": 5})
                else:
                    score_components["digest"] = 0
            else:
                score_components["digest"] = 5  # too few sends to judge
        else:
            score_components["digest"] = 10  # no stats = concerning
    except Exception:
        score_components["digest"] = 10

    # --- 4. Tenure + billing (15 pts max) ---
    try:
        merchant_row = db.execute(text("""
            SELECT installed_at, billing_active, plan
            FROM merchants
            WHERE shop_domain = :shop
            LIMIT 1
        """), {"shop": shop_domain}).fetchone()

        if merchant_row:
            installed_at = merchant_row[0]
            billing_active = merchant_row[1]
            plan = merchant_row[2] or "lite"

            if installed_at:
                tenure_days = (now - installed_at).days
                if tenure_days < 14:
                    score_components["tenure"] = 15
                    signals.append({"signal": "new_install", "detail": f"Installed {tenure_days} days ago — critical window", "weight": 15})
                elif tenure_days < 30:
                    score_components["tenure"] = 8
                    signals.append({"signal": "early_tenure", "detail": f"Installed {tenure_days} days ago", "weight": 8})
                else:
                    score_components["tenure"] = 0

            if not billing_active:
                score_components["billing"] = 10
                signals.append({"signal": "billing_inactive", "detail": "Billing not active", "weight": 10})
            else:
                score_components["billing"] = 0
        else:
            score_components["tenure"] = 5
            score_components["billing"] = 5
    except Exception:
        score_components["tenure"] = 5
        score_components["billing"] = 0

    # --- 5. Onboarding completeness (10 pts max) ---
    try:
        journey_row = db.execute(text("""
            SELECT current_stage
            FROM merchant_journey_states
            WHERE shop_domain = :shop
            ORDER BY id DESC LIMIT 1
        """), {"shop": shop_domain}).fetchone()

        if journey_row:
            stage = journey_row[0] or ""
            completed_stages = {"active", "activated_lite", "activated_pro", "replied"}
            if stage not in completed_stages:
                score_components["onboarding"] = 10
                signals.append({"signal": "onboarding_incomplete", "detail": f"Stage: {stage}", "weight": 10})
            else:
                score_components["onboarding"] = 0
        else:
            score_components["onboarding"] = 5
    except Exception:
        score_components["onboarding"] = 5

    # --- Aggregate ---
    total_score = min(100, sum(score_components.values()))

    if total_score >= 70:
        risk_level = "critical"
        action = "Immediate outreach needed — merchant is likely to churn within 7 days."
    elif total_score >= 50:
        risk_level = "high"
        action = "Send personalized re-engagement email with specific value proposition."
    elif total_score >= 30:
        risk_level = "moderate"
        action = "Monitor weekly. Consider proactive check-in if score increases."
    else:
        risk_level = "low"
        action = "Merchant is healthy. Continue normal digest cadence."

    signals.sort(key=lambda s: s["weight"], reverse=True)

    return {
        "shop_domain": shop_domain,
        "churn_risk_score": total_score,
        "risk_level": risk_level,
        "signals": signals[:5],
        "score_breakdown": score_components,
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
    # Get all active merchants
    merchants = db.execute(text("""
        SELECT shop_domain FROM merchants
        WHERE install_status = 'installed'
        ORDER BY installed_at DESC
        LIMIT 500
    """)).fetchall()

    results = []
    for row in merchants:
        try:
            score = compute_churn_score(db, row[0])
            if score["churn_risk_score"] > 0:
                results.append(score)
        except Exception as exc:
            log.warning("Churn score failed for %s: %s", row[0], exc)

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
