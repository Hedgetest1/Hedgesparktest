"""recurring_buyers.py — GET /pro/recurring-buyers/summary endpoint.

Pro-gated read of the cadence-based recurring buyer analytics
(app/services/recurring_buyer_analytics.py). Read-only — no merchant
state mutation. Cached 30min in Redis (the underlying analytics scan
the full lookback window of shop_orders, ~180d; recomputing per
request at 10k merchants would burn DB).

Endpoint shape:
  {
    "shop_domain": "...",
    "currency": "USD",
    "lookback_days": 180,
    "has_data": true,
    "recurring_count": 12,
    "recurring_revenue_30d": 1234.56,
    "mrr_estimate": 987.65,
    "at_risk_count": 2,
    "churned_30d": 1,
    "buyers": [
      {
        "email_masked": "j***@gmail.com",
        "cadence_kind": "monthly",
        "cadence_days": 30.1,
        "orders_count": 5,
        "lifetime_revenue": 234.50,
        "currency": "USD",
        "last_order_at": "2026-05-01T...",
        "next_expected_at": "2026-05-31T...",
        "is_at_risk": false
      },
      ...
    ],
    "note": "..."  // present when has_data=false
  }

Raw customer_email is NEVER serialized — every buyer dict carries
`email_masked` instead. The aggregate counts respect the same k>=10
floor as the underlying service.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)
router = APIRouter(tags=["recurring_buyers"])


_CACHE_TTL_SECONDS = 30 * 60  # 30min — analytics costly to compute
_CACHE_KEY_PREFIX = "hs:recurring_buyers:v1"
_LOCK_KEY_PREFIX = "hs:recurring_buyers:lock:v1"
_LOCK_TTL_SECONDS = 30
_LOCK_WAIT_BUDGET_SEC = 2.0


class RecurringBuyerItem(BaseModel):
    email_masked: str
    cadence_kind: str
    cadence_days: float
    orders_count: int
    lifetime_revenue: float
    currency: str
    last_order_at: str
    next_expected_at: str
    is_at_risk: bool


class RecurringBuyersResponse(BaseModel):
    shop_domain: str
    currency: str
    lookback_days: int
    has_data: bool
    recurring_count: int
    recurring_revenue_30d: float
    mrr_estimate: float
    at_risk_count: int
    churned_30d: int
    buyers: list[RecurringBuyerItem] = Field(default_factory=list)
    note: str | None = None


def _cache_key(shop: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"


def _lock_key(shop: str) -> str:
    return f"{_LOCK_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"


def _read_cached(shop: str) -> dict | None:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("recurring_buyers.cache_read_redis_down")
            return None
        cached = rc.get(_cache_key(shop))
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("recurring_buyers: cache read failed for %s: %s", shop, exc)
    return None


def _write_cached(shop: str, payload: dict) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(
                _cache_key(shop), _CACHE_TTL_SECONDS,
                json.dumps(payload, default=str),
            )
    except Exception as exc:
        log.warning("recurring_buyers: cache write failed for %s: %s", shop, exc)


def _acquire_lock(shop: str) -> bool:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("recurring_buyers.lock_acquire_redis_down")
            return True  # no Redis → no stampede protection, proceed
        return bool(rc.set(_lock_key(shop), "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception as exc:
        log.warning("recurring_buyers: lock acquire failed for %s: %s", shop, exc)
        return True


def _wait_for_cache(shop: str) -> dict | None:
    deadline = time.monotonic() + _LOCK_WAIT_BUDGET_SEC
    while time.monotonic() < deadline:
        cached = _read_cached(shop)
        if cached is not None:
            return cached
        time.sleep(0.1)
    return None


@router.get(
    "/pro/recurring-buyers/summary",
    response_model=RecurringBuyersResponse,
    response_model_exclude_none=False,
)
def get_summary(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Return cadence-based recurring buyer analytics for the shop."""
    cached = _read_cached(shop)
    if cached is not None:
        return RecurringBuyersResponse(**cached)

    if not _acquire_lock(shop):
        # Another request is computing — wait briefly for its cache write
        waited = _wait_for_cache(shop)
        if waited is not None:
            return RecurringBuyersResponse(**waited)
        # Fall through and compute (lock-timeout fail-open)

    from app.services.recurring_buyer_analytics import (
        compute_recurring_analytics, mask_email,
    )
    report = compute_recurring_analytics(db, shop)

    payload = {
        "shop_domain": report.shop_domain,
        "currency": report.currency,
        "lookback_days": report.lookback_days,
        "has_data": report.has_data,
        "recurring_count": report.recurring_count,
        "recurring_revenue_30d": report.recurring_revenue_30d,
        "mrr_estimate": report.mrr_estimate,
        "at_risk_count": report.at_risk_count,
        "churned_30d": report.churned_30d,
        "buyers": [
            {
                "email_masked": mask_email(b.customer_email),
                "cadence_kind": b.cadence_kind,
                "cadence_days": b.cadence_days,
                "orders_count": b.orders_count,
                "lifetime_revenue": b.lifetime_revenue,
                "currency": b.currency,
                "last_order_at": b.last_order_at.isoformat(),
                "next_expected_at": b.next_expected_at.isoformat(),
                "is_at_risk": b.is_at_risk,
            }
            for b in report.buyers
        ],
        "note": report.note,
    }
    _write_cached(shop, payload)
    return RecurringBuyersResponse(**payload)
