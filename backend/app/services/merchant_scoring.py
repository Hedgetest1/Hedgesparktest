"""
merchant_scoring.py — Revenue-weighted merchant prioritization.

Scores each merchant 0–100 based on traffic, revenue, opportunity, and engagement.
Score determines email frequency, LLM allocation, and re-engagement intensity.

Tiers:
    HIGH   (70–100): Priority email, daily checks, LLM nudge composition
    MEDIUM (30–69):  Weekly digest, standard follow-up, rule-based nudges
    LOW    (0–29):   Monthly digest only, minimal LLM, re-engagement only

All data sourced from existing tables — no new infrastructure needed.

Public interface:
    score_merchant(db, shop_domain) -> MerchantScore
    score_all_merchants(db) -> list[MerchantScore]
    get_tier(score: int) -> str
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("merchant_scoring")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class MerchantScore:
    shop_domain: str
    total_score: int          # 0–100
    tier: str                 # HIGH / MEDIUM / LOW
    traffic_score: int        # 0–100
    revenue_score: int        # 0–100
    opportunity_score: int    # 0–100
    engagement_score: int     # 0–100
    revenue_at_risk: float    # estimated monthly revenue leakage
    monthly_revenue: float    # current monthly revenue


def get_tier(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def score_merchant(db: Session, shop_domain: str) -> MerchantScore:
    """Score a single merchant. Returns MerchantScore with 0–100 composite score."""

    traffic = _score_traffic(db, shop_domain)
    revenue = _score_revenue(db, shop_domain)
    opportunity = _score_opportunity(db, shop_domain)
    engagement = _score_engagement(db, shop_domain)

    total = int(
        traffic * 0.30
        + revenue * 0.30
        + opportunity * 0.25
        + engagement * 0.15
    )
    total = min(100, max(0, total))

    # Compute revenue-at-risk for framing
    risk_row = db.execute(text("""
        SELECT COALESCE(SUM(os.signal_strength * 100), 0)
        FROM opportunity_signals os
        WHERE os.shop_domain = :shop AND os.expires_at > now()
    """), {"shop": shop_domain}).scalar() or 0.0

    monthly_rev = db.execute(text("""
        SELECT COALESCE(SUM(total_price), 0)
        FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :cutoff
    """), {"shop": shop_domain, "cutoff": _now() - timedelta(days=30)}).scalar() or 0.0

    return MerchantScore(
        shop_domain=shop_domain,
        total_score=total,
        tier=get_tier(total),
        traffic_score=traffic,
        revenue_score=revenue,
        opportunity_score=opportunity,
        engagement_score=engagement,
        revenue_at_risk=round(risk_row, 2),
        monthly_revenue=round(monthly_rev, 2),
    )


def score_all_merchants(db: Session, limit: int = 200) -> list[MerchantScore]:
    """Score all active merchants, sorted by score descending."""
    shops = db.execute(text("""
        SELECT shop_domain FROM merchants
        WHERE install_status = 'active' AND is_synthetic = false
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    scores = []
    for row in shops:
        try:
            s = score_merchant(db, row[0])
            scores.append(s)
        except Exception as exc:
            log.warning("merchant_scoring: error scoring %s: %s", row[0], exc)

    scores.sort(key=lambda s: s.total_score, reverse=True)
    return scores


# ---------------------------------------------------------------------------
# Sub-scores (each 0–100)
# ---------------------------------------------------------------------------

def _score_traffic(db: Session, shop: str) -> int:
    """Score based on visitor volume. More visitors = more conversion potential."""
    row = db.execute(text("""
        SELECT COALESCE(SUM(unique_visitors_7d), 0)
        FROM product_metrics WHERE shop_domain = :shop
    """), {"shop": shop}).scalar() or 0

    # Logarithmic scale: 10 visitors = 20, 100 = 50, 1000 = 80, 10000 = 100
    if row <= 0:
        return 0
    import math
    return min(100, int(math.log10(max(1, row)) * 25))


def _score_revenue(db: Session, shop: str) -> int:
    """Score based on actual order volume. Revenue proves the merchant is real."""
    row = db.execute(text("""
        SELECT COUNT(*), COALESCE(SUM(total_price), 0)
        FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :cutoff
    """), {"shop": shop, "cutoff": _now() - timedelta(days=30)}).first()

    order_count = row[0] if row else 0
    revenue = row[1] if row else 0

    if order_count == 0:
        return 0

    # Orders: 1–5 = 20, 10–50 = 50, 100+ = 80
    import math
    order_score = min(50, int(math.log10(max(1, order_count)) * 25))

    # Revenue: $100 = 20, $1000 = 40, $10000 = 50
    rev_score = min(50, int(math.log10(max(1, revenue)) * 12.5))

    return min(100, order_score + rev_score)


def _score_opportunity(db: Session, shop: str) -> int:
    """Score based on fixable problems. More signals = more revenue to recover."""
    row = db.execute(text("""
        SELECT COUNT(*), COALESCE(AVG(signal_strength), 0)
        FROM opportunity_signals
        WHERE shop_domain = :shop AND expires_at > now()
    """), {"shop": shop}).first()

    signal_count = row[0] if row else 0
    avg_strength = row[1] if row else 0

    if signal_count == 0:
        return 0

    # More signals = more opportunity: 1 = 30, 3 = 50, 5+ = 70, 10+ = 90
    count_score = min(60, signal_count * 12)
    strength_score = min(40, int(avg_strength * 40))

    return min(100, count_score + strength_score)


def _score_engagement(db: Session, shop: str) -> int:
    """Score based on email + dashboard engagement. Engaged merchants convert to Pro."""
    row = db.execute(text("""
        SELECT COALESCE(SUM(sent_count), 0),
               COALESCE(SUM(opened_count), 0),
               COALESCE(SUM(clicked_count), 0)
        FROM merchant_email_stats
        WHERE shop_domain = :shop
    """), {"shop": shop}).first()

    sent = row[0] if row else 0
    opened = row[1] if row else 0
    clicked = row[2] if row else 0

    if sent == 0:
        return 50  # No data yet — neutral score, don't penalize new merchants

    open_rate = opened / sent
    click_rate = clicked / sent if sent else 0

    # Open rate: 0% = 0, 30% = 40, 50%+ = 60
    open_score = min(60, int(open_rate * 120))

    # Click rate: any clicks = bonus 20–40
    click_score = min(40, int(click_rate * 200))

    return min(100, open_score + click_score)
