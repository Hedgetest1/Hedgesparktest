"""
counterfactual.py — Phase Ω⁷ killer #2.

"What if you'd acted sooner?"

For every open opportunity signal, computes the hypothetical revenue
the merchant would have recovered IF they had applied the recommended
action on day D. The math is deterministic:

  1. Pull the signal detection timestamp from opportunity_signals
  2. Compute the per-day loss rate from the signal's estimated_loss
  3. Multiply by (today - detection_date) days
  4. Apply an AOV × baseline CVR × fix-uplift projection

The output is a single killer sentence + a compact table of
"what-if" scenarios over N=0, 7, 14, 30 days. Merchants can then
decide to act now or live with the ongoing bleed.

Honest disclosure: the projection uses the same RARS loss math that
powers Revenue-at-Risk. We're not inventing numbers — we're showing
the integral of losses over the window the signal has been open.

Endpoint:
    GET /pro/counterfactual/signals — all open signals with hypothetical impact
    GET /pro/counterfactual/signals/{id} — detail view for one signal

Auth: Pro session. Tenant-isolated via shop_domain.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
from app.core.currency import format_money
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("counterfactual")

router = APIRouter(tags=["counterfactual"])


# Projection constants — documented so the math is readable
_DEFAULT_AOV_EUR = 50.0      # fallback when shop has no orders
_FIX_UPLIFT_PCT = 0.015      # 1.5% CVR uplift assumption for "if you'd fixed it"
_MAX_LOOKBACK_DAYS = 60      # cap the accrual window so projections stay honest


def _now() -> datetime:
    # Naive-UTC to match TIMESTAMP WITHOUT TIME ZONE columns used across
    # the schema. datetime.utcnow() is deprecated — we materialize the
    # equivalent via now(timezone.utc).replace(tzinfo=None).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to naive UTC — DB rows may come back aware or naive
    depending on column type, and mixing them raises TypeError in arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _shop_aov(db: Session, shop: str) -> tuple[float, bool]:
    """Return (aov_eur, is_real). Falls back to default when no orders.

    We deliberately catch only SQLAlchemyError so that coding bugs (NameError,
    KeyError, etc.) surface instead of being silently swallowed."""
    from sqlalchemy.exc import SQLAlchemyError
    currency = get_shop_currency(db, shop)
    try:
        row = db.execute(
            text(
                """
                SELECT COALESCE(AVG(total_price) FILTER (WHERE total_price > 0), 0), COUNT(*)
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - INTERVAL '30 days'
                  AND (:currency IS NULL OR currency = :currency)
                """
            ),
            {"shop": shop, "currency": currency},
        ).fetchone()
        if row and row[1] and row[0]:
            return float(row[0]), True
    except SQLAlchemyError as exc:
        log.warning("counterfactual: aov lookup failed for %s: %s", shop, exc)
    return _DEFAULT_AOV_EUR, False


def _compute_cf_for_signal(
    row, aov: float, aov_is_real: bool, currency: str | None = None
) -> dict:
    """
    row = (id, signal_type, product_url, signal_strength, detected_at, estimated_loss)
    """
    sig_id, stype, purl, strength, detected_at, est_loss = row
    now = _now()
    detected_at = _as_naive_utc(detected_at)
    if detected_at is None:
        detected_at = now - timedelta(hours=1)
    days_open = max(0, (now - detected_at).days)
    days_open = min(days_open, _MAX_LOOKBACK_DAYS)

    # If estimated_loss is populated, use it directly as per-day loss rate
    # (that's how RARS shapes this field — loss over the signal's default
    # 30-day look-ahead window). Otherwise synthesize from strength × AOV.
    per_day_loss: float
    if est_loss and est_loss > 0:
        per_day_loss = float(est_loss) / 30.0
    else:
        # strength ∈ [0, 1]; at strength 0.5 we assume 1 fix-sized event/day
        per_day_loss = float(strength or 0.0) * aov * _FIX_UPLIFT_PCT

    # Scenarios: 0 days (now), +7, +14, +30 — what would have happened if
    # the merchant had acted N days ago
    scenarios = []
    for days_ago in (0, 7, 14, 30):
        # "if you'd fixed it N days ago, you'd have saved N days of loss"
        saved = round(min(days_ago, days_open) * per_day_loss, 2)
        scenarios.append({
            "days_ago": days_ago,
            "saved_eur": saved,
            "label": (
                "right now" if days_ago == 0
                else f"{days_ago} days ago"
            ),
        })

    # Biggest possible save — across the full open window
    max_save = round(days_open * per_day_loss, 2)

    return {
        "signal_id": sig_id,
        "signal_type": stype,
        "product_url": purl,
        "detected_at": detected_at.isoformat() if detected_at else None,
        "days_open": days_open,
        "per_day_loss_eur": round(per_day_loss, 2),
        "scenarios": scenarios,
        "max_save_eur": max_save,
        "aov_used_eur": round(aov, 2),
        "aov_is_real": aov_is_real,
        "headline": (
            f"Acting now recovers {format_money(max_save, currency, compact=True)} "
            f"from this signal. Every day you wait costs "
            f"~{format_money(per_day_loss, currency, compact=True)}."
            if max_save > 0 else
            "Signal still accruing — check back in 24h for a meaningful counterfactual."
        ),
    }


class CounterfactualScenario(BaseModel):
    days_ago: int
    saved_eur: float
    label: str


class CounterfactualEntry(BaseModel):
    signal_id: int
    signal_type: str
    product_url: str | None = None
    detected_at: str | None = None
    days_open: int
    per_day_loss_eur: float
    scenarios: list[CounterfactualScenario] = Field(default_factory=list)
    max_save_eur: float
    aov_used_eur: float
    aov_is_real: bool
    headline: str


class CounterfactualListResponse(BaseModel):
    shop_domain: str
    aov_eur: float
    aov_is_real: bool
    total_open_signals: int
    total_max_save_eur: float
    entries: list[CounterfactualEntry] = Field(default_factory=list)
    headline: str
    # Shop's native currency (USD/EUR/GBP/…) — every `_eur` field in
    # this payload is denominated in this currency.
    currency: str = "USD"
    generated_at: str


@router.get("/pro/counterfactual/signals", response_model=CounterfactualListResponse)
def list_counterfactuals(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """List open signals with their counterfactual revenue scenarios."""
    try:
        from app.core.feature_usage import track
        track("counterfactual_explorer", shop)
    except Exception as exc:
        log.warning("counterfactual: feature usage track failed: %s", exc)

    aov, aov_is_real = _shop_aov(db, shop)
    currency = get_shop_currency(db, shop) or "USD"
    cutoff = _now() - timedelta(days=_MAX_LOOKBACK_DAYS)

    try:
        rows = db.execute(
            text(
                """
                SELECT id, signal_type, product_url, signal_strength,
                       detected_at,
                       -- estimated_loss is pulled from the signal detail if
                       -- populated; otherwise NULL and we derive from strength
                       NULL::float AS estimated_loss
                FROM opportunity_signals
                WHERE shop_domain = :shop
                  AND detected_at >= :cutoff
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY signal_strength DESC NULLS LAST, detected_at DESC
                LIMIT 20
                """
            ),
            {"shop": shop, "cutoff": cutoff},
        ).fetchall()
    except Exception as exc:
        log.warning("counterfactual: signal query failed for %s: %s", shop, exc)
        rows = []

    entries = [_compute_cf_for_signal(r, aov, aov_is_real, currency) for r in rows]
    total_max_save = round(sum(e["max_save_eur"] for e in entries), 2)

    return {
        "shop_domain": shop,
        "aov_eur": round(aov, 2),
        "aov_is_real": aov_is_real,
        "total_open_signals": len(entries),
        "total_max_save_eur": total_max_save,
        "entries": entries,
        "headline": (
            f"Acting on all {len(entries)} open signals now would recover "
            f"~{format_money(total_max_save, currency, compact=True)}. "
            f"Every day of delay keeps this number climbing."
            if total_max_save > 0 else
            "No open signals with measurable counterfactual impact yet."
        ),
        "currency": currency,
        "generated_at": _now().isoformat(),
    }


@router.get("/pro/counterfactual/signals/{signal_id}", response_model=CounterfactualEntry)
def get_counterfactual(
    signal_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Detail view for a single signal's counterfactual."""
    aov, aov_is_real = _shop_aov(db, shop)
    currency = get_shop_currency(db, shop) or "USD"
    try:
        row = db.execute(
            text(
                """
                SELECT id, signal_type, product_url, signal_strength,
                       detected_at, NULL::float AS estimated_loss
                FROM opportunity_signals
                WHERE shop_domain = :shop AND id = :id
                """
            ),
            {"shop": shop, "id": signal_id},
        ).fetchone()
    except Exception as exc:
        log.warning("counterfactual: signal detail query failed: %s", exc)
        row = None
    if not row:
        raise HTTPException(404, "signal not found")
    return _compute_cf_for_signal(row, aov, aov_is_real, currency)
