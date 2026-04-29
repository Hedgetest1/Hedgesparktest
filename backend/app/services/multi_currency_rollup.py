"""
multi_currency_rollup.py — honest revenue aggregation across shops.

When a merchant runs N Shopify stores in different currencies (EU/EUR,
US/USD, UK/GBP) we cannot collapse the rollup into a single number
without an FX layer. HedgeSpark does not ship an FX layer (no external
dependency, no theater). Instead we surface per-currency totals.

Two consumers:
  * Multi-store consolidation dashboard (merchant_groups)
  * Agency white-label console (agency)

Both previously summed `total_price` across shops as if denominated
identically — the result was a fake number labeled `revenue_eur` that
mixed currencies. This utility replaces that with truth.

Output shape:

    {
        "by_currency": {
            "EUR": {"revenue": 12345.0, "orders": 200, "shops": 2},
            "USD": {"revenue": 8000.0,  "orders": 100, "shops": 1},
        },
        "primary_currency": "EUR",  # most-revenue currency, for headline
        "is_homogeneous": False,    # True iff all shops share one currency
        "total_orders": 300,        # safe to sum (count is currency-free)
        "shop_count": 3,
    }

When `is_homogeneous=True` callers may render a single rollup card.
When False they MUST render per-currency cards — never a fake unified
number.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ShopRow:
    """One shop's contribution to a rollup, in its own native currency."""
    shop_domain: str
    currency: str
    revenue: float
    orders: int


def aggregate_by_currency(rows: Iterable[ShopRow]) -> dict:
    """
    Group per-shop rows by currency. Returns the truth-shaped rollup.

    Currency is normalized to upper-case 3-letter ISO; empty/None
    falls back to "UNKNOWN" so callers can decide how to surface it
    (typically: warn the merchant and exclude from rollup).
    """
    by_currency: dict[str, dict] = {}
    total_orders = 0
    shop_count = 0
    for row in rows:
        ccy = (row.currency or "").strip().upper() or "UNKNOWN"
        bucket = by_currency.setdefault(
            ccy, {"revenue": 0.0, "orders": 0, "shops": 0}
        )
        bucket["revenue"] += float(row.revenue or 0)
        bucket["orders"] += int(row.orders or 0)
        bucket["shops"] += 1
        total_orders += int(row.orders or 0)
        shop_count += 1

    # Round to 2 dp at the boundary, never inside the loop (precision)
    for ccy, bucket in by_currency.items():
        bucket["revenue"] = round(bucket["revenue"], 2)

    # Primary currency = highest revenue bucket; ties broken by orders.
    if by_currency:
        primary_currency = max(
            by_currency.keys(),
            key=lambda c: (by_currency[c]["revenue"], by_currency[c]["orders"]),
        )
    else:
        primary_currency = None

    is_homogeneous = len(by_currency) <= 1

    return {
        "by_currency": by_currency,
        "primary_currency": primary_currency,
        "is_homogeneous": is_homogeneous,
        "total_orders": total_orders,
        "shop_count": shop_count,
    }


def headline_for(rollup: dict) -> dict | None:
    """
    Compact one-line summary for tile/header rendering.

    When the rollup is homogeneous: returns a single {revenue, orders,
    aov, currency} dict. When mixed: returns the largest-currency
    bucket with `mixed=True` so the UI can flag it.
    """
    primary = rollup.get("primary_currency")
    if not primary:
        return None
    bucket = rollup["by_currency"][primary]
    aov = round(bucket["revenue"] / bucket["orders"], 2) if bucket["orders"] else 0.0
    return {
        "currency": primary,
        "revenue": bucket["revenue"],
        "orders": bucket["orders"],
        "aov": aov,
        "shops": bucket["shops"],
        "mixed": not rollup["is_homogeneous"],
    }
