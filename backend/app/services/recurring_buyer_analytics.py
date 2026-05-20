"""recurring_buyer_analytics.py — detect cadence-based recurring buyers.

Pro mid-band feature shipping the parity gap against Glew Pro / Putler
Plus subscription analytics. Honest framing: we DO NOT integrate with
Shopify's Subscriptions Admin API (most SMB Shopify merchants don't
use it). Instead we detect REGULAR-CADENCE buyers heuristically from
shop_orders.customer_email + created_at. Surfaced as "Recurring
buyers" (not "Subscriptions") so the merchant knows the data source.

Detection algorithm:
  1. Pull shop_orders in the lookback window with customer_email NOT NULL
  2. Group by customer_email; require >= 3 orders to call a pattern
  3. Sort by created_at; compute gap distribution between consecutive orders
  4. Classify cadence as one of {weekly, biweekly, monthly, quarterly}
     based on mean gap, requiring std/mean < 0.45 (regularity floor)
  5. Compute lifetime_revenue + last_order_at + next_expected_at per
     recurring buyer
  6. At-risk = next_expected_at overdue by > 7 days (relative to cadence)

Metrics returned:
  - recurring_count: # buyers detected with regular cadence
  - recurring_revenue_30d: sum of recurring buyer orders in last 30d
  - mrr_estimate: monthly recurring revenue extrapolated from cadence
  - at_risk_count: # whose next-expected is overdue > cadence_days/4
  - churned_30d: recurring active 60d ago but no order in last 30d

GDPR: customer_email is PII. The API endpoint exposes ONLY aggregate
counts + per-buyer dicts with email_masked (e.g. "j***@gmail.com"),
never raw email. Caller (api/) is responsible for masking; this
service returns the raw email for the FastAPI layer to mask before
serialization.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


log = logging.getLogger(__name__)


# Minimum orders to call a customer a "recurring buyer"
MIN_ORDERS_FOR_CADENCE = 3

# Regularity threshold: std/mean of inter-order gaps must be < this.
# 0.45 admits some natural jitter while excluding random patterns.
REGULARITY_CV_THRESHOLD = 0.45

# Cadence buckets — (kind_label, min_days, max_days, expected_days)
_CADENCE_BUCKETS = [
    ("weekly",     5,  9,   7),
    ("biweekly",  12, 16,  14),
    ("monthly",   25, 40,  30),
    ("quarterly", 80, 100, 91),
]

# At-risk threshold: a recurring buyer is at-risk when their next-expected
# order is overdue by more than cadence_days * 0.25 days. Conservative:
# a monthly buyer becomes at-risk after ~37.5 days; weekly after ~8.75.
AT_RISK_OVERDUE_FRACTION = 0.25

# Churn threshold for trailing comparisons. A recurring buyer who was
# active in the 60d-30d window but not in the last 30d → churned.
CHURN_LOOKBACK_DAYS = 60
CHURN_COMPARE_DAYS = 30

# Default lookback for the analysis window. 180d is long enough to see
# 6 monthly cycles but short enough that classic-customer changes
# (last-year shoppers) don't pollute current cadence.
DEFAULT_LOOKBACK_DAYS = 180


@dataclass
class RecurringBuyer:
    customer_email: str
    cadence_kind: str  # "weekly" / "biweekly" / "monthly" / "quarterly"
    cadence_days: float  # observed mean gap
    orders_count: int
    lifetime_revenue: float
    currency: str
    first_order_at: datetime
    last_order_at: datetime
    next_expected_at: datetime
    is_at_risk: bool


@dataclass
class RecurringAnalyticsReport:
    shop_domain: str
    currency: str
    lookback_days: int
    recurring_count: int
    recurring_revenue_30d: float
    mrr_estimate: float
    at_risk_count: int
    churned_30d: int
    buyers: list[RecurringBuyer]
    has_data: bool
    note: str | None = None


def compute_recurring_analytics(
    db: Session,
    shop_domain: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> RecurringAnalyticsReport:
    """Main entry point. Returns the full report shape for the API layer.

    Empty/insufficient-data cases:
      - has_data=False + note when shop has < 10 distinct customer
        emails in window (statistical floor)
      - recurring_count=0 when no email-group reaches the regularity gate
    """
    horizon = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=lookback_days,
    )

    rows = db.execute(text("""
        SELECT customer_email, total_price, currency, created_at
        FROM shop_orders
        WHERE shop_domain = :shop
          AND customer_email IS NOT NULL
          AND created_at >= :horizon
        ORDER BY customer_email, created_at
    """), {"shop": shop_domain, "horizon": horizon}).fetchall()

    if not rows:
        return RecurringAnalyticsReport(
            shop_domain=shop_domain,
            # data-truth-allowed: empty-state return; no orders means no currency can be derived from data; consumer renders explicit "no_data" branch
            currency="USD",
            lookback_days=lookback_days,
            recurring_count=0,
            recurring_revenue_30d=0.0,
            mrr_estimate=0.0,
            at_risk_count=0,
            churned_30d=0,
            buyers=[],
            has_data=False,
            note=f"No orders with customer_email in last {lookback_days} days.",
        )

    # Group by customer_email — rows are already sorted, accumulate
    by_email: dict[str, list[tuple]] = {}
    currency_counter: dict[str, int] = {}
    for r in rows:
        email = r.customer_email.lower().strip()
        if not email:
            continue
        by_email.setdefault(email, []).append((
            float(r.total_price), r.currency or "USD", r.created_at,
        ))
        currency_counter[r.currency or "USD"] = currency_counter.get(
            r.currency or "USD", 0,
        ) + 1

    # Shop currency = mode of order currencies in window
    # data-truth-allowed: mode-of-orders fallback when currency_counter is empty (means rows had no currency, impossible by SQL contract — defensive only)
    shop_currency = (
        max(currency_counter.items(), key=lambda kv: kv[1])[0]
        if currency_counter else "USD"
    )

    # Statistical floor: need ≥ 10 distinct customers to claim anything
    if len(by_email) < 10:
        return RecurringAnalyticsReport(
            shop_domain=shop_domain,
            currency=shop_currency,
            lookback_days=lookback_days,
            recurring_count=0,
            recurring_revenue_30d=0.0,
            mrr_estimate=0.0,
            at_risk_count=0,
            churned_30d=0,
            buyers=[],
            has_data=False,
            note=(
                f"Only {len(by_email)} distinct buyers in {lookback_days}d — "
                "need at least 10 for cadence detection."
            ),
        )

    buyers: list[RecurringBuyer] = []
    for email, orders in by_email.items():
        buyer = _classify_buyer(email, orders, shop_currency)
        if buyer is not None:
            buyers.append(buyer)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_30d = now - timedelta(days=30)
    cutoff_60d = now - timedelta(days=60)

    recurring_revenue_30d = 0.0
    at_risk_count = 0
    for b in buyers:
        if b.last_order_at >= cutoff_30d:
            recurring_revenue_30d += b.lifetime_revenue * (
                30.0 / max((b.last_order_at - b.first_order_at).days, 1)
            )
        if b.is_at_risk:
            at_risk_count += 1

    # MRR estimate: per buyer, lifetime_revenue / months_observed.
    # months_observed = max(1, days_in_window / 30).
    mrr_estimate = 0.0
    for b in buyers:
        days_observed = max((b.last_order_at - b.first_order_at).days, 1)
        months_observed = max(days_observed / 30.0, 1.0)
        mrr_estimate += b.lifetime_revenue / months_observed

    # Churned: was active in 60d-30d window, no order in last 30d
    churned = 0
    for b in buyers:
        recently_inactive = b.last_order_at < cutoff_30d
        was_active_before = b.last_order_at >= cutoff_60d
        if recently_inactive and was_active_before:
            churned += 1

    return RecurringAnalyticsReport(
        shop_domain=shop_domain,
        currency=shop_currency,
        lookback_days=lookback_days,
        recurring_count=len(buyers),
        recurring_revenue_30d=round(recurring_revenue_30d, 2),
        mrr_estimate=round(mrr_estimate, 2),
        at_risk_count=at_risk_count,
        churned_30d=churned,
        buyers=buyers,
        has_data=True,
    )


def _classify_buyer(
    email: str,
    orders: list[tuple],
    shop_currency: str,
) -> RecurringBuyer | None:
    """Return RecurringBuyer when this email's orders meet the cadence
    regularity gate. None when the pattern is too irregular OR < 3 orders."""
    if len(orders) < MIN_ORDERS_FOR_CADENCE:
        return None

    # Mixed-currency customer (rare, e.g. shop with multi-currency rollout)
    # — skip from cadence detection because lifetime_revenue would mix units.
    currencies = {c for _, c, _ in orders}
    if len(currencies) > 1:
        return None
    order_currency = currencies.pop()

    timestamps = [created_at for _, _, created_at in orders]
    gaps_days = [
        (timestamps[i] - timestamps[i - 1]).total_seconds() / 86400.0
        for i in range(1, len(timestamps))
    ]
    if not gaps_days:
        return None

    mean_gap = sum(gaps_days) / len(gaps_days)
    if mean_gap == 0:
        return None
    std_gap = math.sqrt(
        sum((g - mean_gap) ** 2 for g in gaps_days) / len(gaps_days)
    )
    cv = std_gap / mean_gap  # coefficient of variation
    if cv > REGULARITY_CV_THRESHOLD:
        return None  # too irregular

    cadence_kind = _classify_cadence(mean_gap)
    if cadence_kind is None:
        return None  # gap doesn't match any standard bucket

    lifetime_revenue = sum(price for price, _, _ in orders)
    last_order_at = timestamps[-1]
    next_expected_at = last_order_at + timedelta(days=mean_gap)

    # At-risk: now is past next_expected_at + cadence * AT_RISK_OVERDUE_FRACTION
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    overdue_threshold = next_expected_at + timedelta(
        days=mean_gap * AT_RISK_OVERDUE_FRACTION,
    )
    is_at_risk = now > overdue_threshold

    return RecurringBuyer(
        customer_email=email,
        cadence_kind=cadence_kind,
        cadence_days=round(mean_gap, 1),
        orders_count=len(orders),
        lifetime_revenue=round(lifetime_revenue, 2),
        currency=order_currency,
        first_order_at=timestamps[0],
        last_order_at=last_order_at,
        next_expected_at=next_expected_at,
        is_at_risk=is_at_risk,
    )


def _classify_cadence(mean_gap_days: float) -> str | None:
    """Return cadence label when mean_gap fits a standard bucket, else None."""
    for kind, lo, hi, _expected in _CADENCE_BUCKETS:
        if lo <= mean_gap_days <= hi:
            return kind
    return None


def mask_email(email: str) -> str:
    """Mask email for safe surfacing in API responses.
    'jdoe@gmail.com' → 'j***@gmail.com'. Never log the raw email."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"
