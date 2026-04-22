"""
roi_report.py — ROI report generator for the dashboard.

Generates the "Net ROI this month" data structure that powers the Pro
dashboard card:

  "HedgeSpark cost you €99 this month.
   Detected €2,431 of revenue at risk.
   Prevented €840 via auto-fixes and holdout-measured nudges.
   Net ROI: +€741 this month."

The math that makes churn impossible. No competitor ships this because
nobody else has the RARS + holdout infrastructure to quantify prevention.

Delivery
--------
Consumed ON-SCREEN via /pro/roi-report (JSON for dashboard card). The
merchant sees the Net ROI in the weekly digest (Monday) and anytime
they open the dashboard — no separate monthly email channel. The
monthly-email path was intentionally removed 2026-04-22: redundant
with weekly_digest + always-on dashboard, violated the "one channel
per ritmo" principle by adding a 3rd cadence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("roi_report")

# Pro tier cost for SMB band — imported from the shared doctrine
# module `app.core.tier_pricing` so every net_roi / subscription
# calculation in the codebase tracks the same number. A pricing
# change happens in one place and all consumers update. Audited by
# `audit_tier_cost_literals.py` preflight — any inline literal
# under a cost/roi/subscription variable is blocked at commit.
from app.core.tier_pricing import TIER_SUBSCRIPTION_EUR as _TIER_SUBSCRIPTION_EUR
_PRO_TIER_COST_EUR = _TIER_SUBSCRIPTION_EUR["pro"]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _month_key(now: datetime | None = None) -> str:
    now = now or _now()
    return f"{now.year:04d}-{now.month:02d}"


@dataclass
class ROIReport:
    shop_domain: str
    month: str  # YYYY-MM
    cost_eur: float
    at_risk_detected_eur: float
    prevented_eur: float
    net_roi_eur: float
    components: list[dict]
    headline: str
    generated_at: str
    # Shop's native currency for money rendering (USD/EUR/GBP/…).
    # `_eur`-suffixed fields above are actually in this currency —
    # the suffix is a historical misnomer.
    currency: str = "USD"

    def to_dict(self) -> dict:
        return {
            "shop_domain": self.shop_domain,
            "month": self.month,
            "cost_eur": round(self.cost_eur, 2),
            "at_risk_detected_eur": round(self.at_risk_detected_eur, 2),
            "prevented_eur": round(self.prevented_eur, 2),
            "net_roi_eur": round(self.net_roi_eur, 2),
            "components": self.components,
            "headline": self.headline,
            "currency": self.currency,
            "generated_at": self.generated_at,
        }


def generate_roi_report(db: Session, shop_domain: str) -> ROIReport:
    """
    Build the ROI report data structure for the current month.
    Consumed by `/pro/roi-report` for the dashboard card and by
    weekly_digest for the Monday inbox banner.
    """
    from app.services.revenue_at_risk import get_revenue_at_risk
    rars = get_revenue_at_risk(db, shop_domain)

    at_risk = float(rars.get("total_at_risk_eur") or 0)
    prevented = float(rars.get("prevented_eur_this_month") or 0)
    components = rars.get("components") or []
    net_roi = prevented - _PRO_TIER_COST_EUR

    if net_roi > 0:
        headline = (
            f"🟢 HedgeSpark paid for itself +€{net_roi:.0f} this month."
        )
    elif prevented > 0:
        headline = (
            f"HedgeSpark detected €{at_risk:.0f} at risk and prevented €{prevented:.0f}."
        )
    else:
        headline = (
            f"HedgeSpark surfaced €{at_risk:.0f} at risk this month — "
            "review the detected sources below."
        )

    try:
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"

    now = _now()
    return ROIReport(
        shop_domain=shop_domain,
        month=_month_key(now),
        cost_eur=_PRO_TIER_COST_EUR,
        at_risk_detected_eur=at_risk,
        prevented_eur=prevented,
        net_roi_eur=net_roi,
        components=components,
        headline=headline,
        generated_at=now.isoformat(),
        currency=currency,
    )
