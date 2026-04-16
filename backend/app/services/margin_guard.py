"""
margin_guard.py — COGS-aware discount refusal.

The P&L engine computes real profit margin per shop. This service uses
that to PREVENT any autonomous discount action that would push margin
below a safe floor. Closes the single remaining gap in P&L from the
audit: "no validation preventing under-margin discounts".

Public API
----------
    check_discount_safe(db, shop, discount_pct) -> CheckResult
        Returns (allowed, reason, context).
        Integrated into trust_contract.can_execute() and action_agent's
        high-risk execution path.

    get_margin_snapshot(db, shop) -> dict
        Lightweight P&L summary for guardrail use — does NOT trigger the
        auto-sync side effect of get_pnl_report() so it's cheap.

Design notes
------------
- Fail-safe: if COGS data is missing (precision='rough'), we still enforce
  a conservative floor (default 20% gross margin). The merchant can tighten
  via shop_cost_defaults.min_profit_margin_pct later.
- Zero LLM.
- Cached 5 min in Redis per shop to avoid hammering shop_orders on every
  action evaluation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("margin_guard")

_CACHE_PREFIX = "hs:margin_snapshot"
_CACHE_TTL_S = 300  # 5 min

# The absolute minimum gross margin HedgeSpark will allow an autonomous
# action to drop prices to. Below this, the merchant is losing money.
# Override per-shop via shop_cost_defaults.min_profit_margin_pct (future col).
_DEFAULT_MIN_MARGIN_PCT = 20.0  # 20% gross margin floor


@dataclass
class MarginCheckResult:
    allowed: bool
    reason: str
    current_margin_pct: float
    projected_margin_pct: float
    min_required_pct: float
    precision: str  # 'rough' | 'refined' | 'exact'
    total_revenue_30d: float
    total_cogs_30d: float


def get_margin_snapshot(db: Session, shop_domain: str) -> dict:
    """Return a lightweight margin snapshot. Cached 5 min in Redis."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(f"{_CACHE_PREFIX}:{shop_domain}")
            if raw:
                return json.loads(raw)
    except Exception as exc:
        log.warning("margin_guard: get_margin_snapshot failed: %s", exc)
        rc = None

    snapshot = _compute_margin_snapshot(db, shop_domain)

    if rc is not None:
        try:
            rc.setex(
                f"{_CACHE_PREFIX}:{shop_domain}",
                _CACHE_TTL_S,
                json.dumps(snapshot, default=str),
            )
        except Exception as exc:
            log.warning("margin_guard: get_margin_snapshot failed: %s", exc)

    return snapshot


def _compute_margin_snapshot(db: Session, shop_domain: str) -> dict:
    """Lightweight computation — 30d revenue + COGS coverage, no waterfall.

    Returns:
        {
            "shop_domain": str,
            "window_days": 30,
            "revenue_eur": float,
            "cogs_eur": float,
            "gross_margin_eur": float,
            "gross_margin_pct": float,
            "precision": 'rough' | 'refined' | 'exact',
            "min_required_margin_pct": float,
            "computed_at": str,
        }
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=30)

    # Revenue (shop_orders)
    currency = get_shop_currency(db, shop_domain)
    try:
        revenue = float(
            db.execute(
                sql_text(
                    "SELECT COALESCE(SUM(total_price), 0) FROM shop_orders "
                    "WHERE shop_domain = :s AND created_at >= :c "
                    "AND (:currency IS NULL OR currency = :currency)"
                ),
                {"s": shop_domain, "c": cutoff, "currency": currency},
            ).scalar()
            or 0
        )
    except Exception as exc:
        log.warning("margin_guard: _compute_margin_snapshot failed: %s", exc)
        revenue = 0.0

    # Shop cost defaults
    cogs_pct = 0.40  # module default 40%
    min_margin = _DEFAULT_MIN_MARGIN_PCT
    precision = "rough"
    try:
        row = db.execute(
            sql_text(
                "SELECT default_cogs_pct FROM shop_cost_defaults "
                "WHERE shop_domain = :s LIMIT 1"
            ),
            {"s": shop_domain},
        ).fetchone()
        if row and row[0] is not None:
            cogs_pct = float(row[0])
            precision = "refined"
    except Exception as exc:
        log.warning("margin_guard: _compute_margin_snapshot failed: %s", exc)

    # Per-product COGS coverage — rough check: does the shop have any
    # product_costs rows? If yes, at least "refined"; if covering 80%+ of
    # recent skus, "exact". Avoids expensive JSONB parse on every call.
    try:
        cost_count = int(
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM product_costs "
                    "WHERE shop_domain = :s AND cogs_per_unit IS NOT NULL"
                ),
                {"s": shop_domain},
            ).scalar()
            or 0
        )
        if cost_count > 0 and precision == "rough":
            precision = "refined"
        if cost_count >= 20:  # heuristic: shops with 20+ covered skus → "exact"
            precision = "exact"
    except Exception as exc:
        log.warning("margin_guard: _compute_margin_snapshot failed: %s", exc)

    cogs_eur = revenue * cogs_pct
    gross_margin_eur = revenue - cogs_eur
    gross_margin_pct = (gross_margin_eur / revenue * 100) if revenue > 0 else 0.0

    return {
        "shop_domain": shop_domain,
        "window_days": 30,
        "revenue_eur": round(revenue, 2),
        "cogs_eur": round(cogs_eur, 2),
        "gross_margin_eur": round(gross_margin_eur, 2),
        "gross_margin_pct": round(gross_margin_pct, 2),
        "cogs_pct_used": round(cogs_pct * 100, 2),
        "precision": precision,
        "min_required_margin_pct": round(min_margin, 2),
        "computed_at": now.isoformat(),
    }


def check_discount_safe(
    db: Session,
    shop_domain: str,
    discount_pct: float,
) -> MarginCheckResult:
    """Check if a proposed discount would keep margin above the safety floor.

    Args:
        discount_pct: NEGATIVE for a price cut (e.g. -10 means 10% off).
                      POSITIVE or zero is always safe.
    Returns:
        MarginCheckResult with allowed=False if the discount would push
        gross margin below the shop's minimum threshold.
    """
    snapshot = get_margin_snapshot(db, shop_domain)

    current_margin = snapshot["gross_margin_pct"]
    min_required = snapshot["min_required_margin_pct"]
    precision = snapshot["precision"]
    revenue = snapshot["revenue_eur"]
    cogs = snapshot["cogs_eur"]

    # Positive discount (markup) or zero is always safe
    if discount_pct >= 0:
        return MarginCheckResult(
            allowed=True,
            reason="non_negative_discount",
            current_margin_pct=current_margin,
            projected_margin_pct=current_margin,
            min_required_pct=min_required,
            precision=precision,
            total_revenue_30d=revenue,
            total_cogs_30d=cogs,
        )

    # No revenue data → defer to contract bounds (we can't enforce margin
    # on a shop with zero orders in the last 30 days; that's not a safety
    # call, it's an impossibility). The contract's own discount_floor_pct
    # is still enforced upstream in trust_contract.can_execute().
    if revenue <= 0:
        return MarginCheckResult(
            allowed=True,
            reason="no_revenue_data_defer_to_contract",
            current_margin_pct=0.0,
            projected_margin_pct=0.0,
            min_required_pct=min_required,
            precision=precision,
            total_revenue_30d=0.0,
            total_cogs_30d=0.0,
        )

    # Project margin post-discount.
    # A -X% discount reduces revenue by X%, COGS stays fixed per unit.
    # New revenue per unit ≈ revenue * (1 + discount_pct/100)   (discount_pct is negative)
    # New margin pct = (new_rev - cogs) / new_rev * 100
    new_revenue = revenue * (1.0 + discount_pct / 100.0)
    if new_revenue <= 0:
        return MarginCheckResult(
            allowed=False,
            reason=f"discount_too_aggressive:{discount_pct}%",
            current_margin_pct=current_margin,
            projected_margin_pct=-100.0,
            min_required_pct=min_required,
            precision=precision,
            total_revenue_30d=revenue,
            total_cogs_30d=cogs,
        )

    projected_margin_pct = ((new_revenue - cogs) / new_revenue) * 100.0

    if projected_margin_pct < min_required:
        return MarginCheckResult(
            allowed=False,
            reason=(
                f"margin_floor_breach:{projected_margin_pct:.1f}%<{min_required:.1f}%"
            ),
            current_margin_pct=current_margin,
            projected_margin_pct=round(projected_margin_pct, 2),
            min_required_pct=min_required,
            precision=precision,
            total_revenue_30d=revenue,
            total_cogs_30d=cogs,
        )

    return MarginCheckResult(
        allowed=True,
        reason="margin_safe",
        current_margin_pct=current_margin,
        projected_margin_pct=round(projected_margin_pct, 2),
        min_required_pct=min_required,
        precision=precision,
        total_revenue_30d=revenue,
        total_cogs_30d=cogs,
    )
