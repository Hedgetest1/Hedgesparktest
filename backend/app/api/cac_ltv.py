"""
cac_ltv.py — CAC:LTV ratio endpoint.

Aggregates customer lifetime value (from ltv_engine) against acquisition
cost (from shop_cost_defaults.ad_spend_manual_monthly or auto-tracked
monthly spend) and produces the single most important ratio for DTC:

    ratio = LTV / CAC

    ratio > 3 → healthy (industry standard)
    ratio 1-3 → ok (grow with caution)
    ratio < 1 → unprofitable — losing money on every acquisition

Endpoint: GET /pro/cac-ltv

Deterministic. Reuses ltv_engine + shop_cost_defaults. No LLM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["cac_ltv"])




class CacLtvResponse(BaseModel):
    shop_domain: str
    window_days: int
    customers_acquired: int
    total_ad_spend_eur: float
    cac_eur: float
    avg_ltv_eur: float
    predicted_12m_ltv_eur: float
    ratio: float
    status: str  # 'healthy' | 'ok' | 'unprofitable' | 'no_data'
    headline: str
    ad_spend_source: str
    # Shop's native currency — all `_eur` fields above are native.
    currency: str = "USD"
    generated_at: str


def _get_ad_spend(db: Session, shop: str, window_days: int) -> tuple[float, str]:
    """Return (total_ad_spend_eur_in_window, source) for the shop.

    Priority: shop_cost_defaults.ad_spend_manual_monthly (merchant entry)
    → proportional to window → source='manual'.
    Future: Meta/Google OAuth spend → source='auto'.
    """
    try:
        row = db.execute(
            sql_text(
                "SELECT ad_spend_manual_monthly FROM shop_cost_defaults WHERE shop_domain = :s LIMIT 1"
            ),
            {"s": shop},
        ).fetchone()
        if row and row[0] is not None:
            monthly = float(row[0])
            # Proportional allocation (30 days nominal month)
            return round(monthly * (window_days / 30.0), 2), "manual"
    except Exception as exc:
        log.warning("cac_ltv: ad spend lookup failed: %s", exc)
    return 0.0, "unconfigured"


def _count_new_customers(db: Session, shop: str, window_days: int) -> int:
    """Count distinct customers whose FIRST order landed in the window."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)
    try:
        # Subquery: customer → first_order_at. Outer: count those first_order_at in window.
        row = db.execute(
            sql_text(
                """
                WITH first_orders AS (
                    SELECT customer_email, MIN(created_at) AS first_at
                    FROM shop_orders
                    WHERE shop_domain = :s
                      AND customer_email IS NOT NULL
                      AND customer_email <> ''
                    GROUP BY customer_email
                )
                SELECT COUNT(*) FROM first_orders
                WHERE first_at >= :cutoff
                """
            ),
            {"s": shop, "cutoff": cutoff},
        ).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception as exc:
        log.warning("cac_ltv: new customer count failed: %s", exc)
        return 0


def _get_ltv_metrics(db: Session, shop: str) -> tuple[float, float]:
    """Return (observed_avg_ltv, predicted_12m_ltv).

    observed_avg_ltv = total revenue per customer over whole history
    predicted_12m_ltv = avg 12-month projected LTV (from ltv_engine)
    """
    try:
        currency = get_shop_currency(db, shop)
        row = db.execute(
            sql_text(
                """
                SELECT
                    COUNT(DISTINCT customer_email) AS n,
                    COALESCE(SUM(CAST(total_price AS FLOAT)), 0) AS rev
                FROM shop_orders
                WHERE shop_domain = :s
                  AND customer_email IS NOT NULL
                  AND customer_email <> ''
                  AND (:currency IS NULL OR currency = :currency)
                """
            ),
            {"s": shop, "currency": currency},
        ).fetchone()
        n = int(row[0] or 0) if row else 0
        rev = float(row[1] or 0) if row else 0.0
        observed = (rev / n) if n > 0 else 0.0
    except Exception:
        observed = 0.0

    try:
        from app.services.ltv_engine import get_predicted_ltv
        pred = get_predicted_ltv(db, shop, limit=200)
        customers = pred.get("customers", [])
        if customers:
            avg_predicted = sum(float(c.get("predicted_12m_ltv") or 0) for c in customers) / len(customers)
        else:
            avg_predicted = observed  # fallback: use observed
    except Exception:
        avg_predicted = observed

    return observed, avg_predicted


@router.get("/cac-ltv", response_model=CacLtvResponse)
def get_cac_ltv(
    window_days: int = 30,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1 — analytics read path
):
    window_days = max(7, min(window_days, 365))
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    customers_acquired = _count_new_customers(db, shop, window_days)
    ad_spend, ad_source = _get_ad_spend(db, shop, window_days)
    observed_ltv, predicted_ltv = _get_ltv_metrics(db, shop)
    currency = get_shop_currency(db, shop)

    cac = (ad_spend / customers_acquired) if customers_acquired > 0 else 0.0

    if cac <= 0 or predicted_ltv <= 0:
        ratio = 0.0
        status = "no_data"
    else:
        ratio = predicted_ltv / cac
        if ratio >= 3.0:
            status = "healthy"
        elif ratio >= 1.0:
            status = "ok"
        else:
            status = "unprofitable"

    # Headline
    if status == "no_data":
        if ad_source == "unconfigured":
            headline = "Add your monthly ad spend in Settings → Costs to unlock your CAC:LTV ratio."
        else:
            headline = "Not enough data yet — we need customers and spend to compute CAC:LTV."
    elif status == "healthy":
        headline = f"CAC:LTV ratio of {ratio:.1f}× is healthy — keep acquiring aggressively."
    elif status == "ok":
        headline = f"CAC:LTV ratio of {ratio:.1f}× is ok but tight — target 3× to scale safely."
    else:
        headline = f"Warning: CAC:LTV ratio is {ratio:.1f}× — you're losing money on each new customer."

    return CacLtvResponse(
        shop_domain=shop,
        window_days=window_days,
        customers_acquired=customers_acquired,
        total_ad_spend_eur=ad_spend,
        cac_eur=round(cac, 2),
        avg_ltv_eur=round(observed_ltv, 2),
        predicted_12m_ltv_eur=round(predicted_ltv, 2),
        ratio=round(ratio, 2),
        status=status,
        headline=headline,
        ad_spend_source=ad_source,
        currency=currency or "USD",
        generated_at=now.isoformat(),
    )
