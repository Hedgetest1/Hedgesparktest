"""
customer_churn.py — Per-customer churn prediction API (δ4).

GET /pro/customer-churn?limit=50
    Top N customers most at risk of going silent in the next 30 days.

Cache-first (the 284-YELLOW class remediation, proven by
scripts/explain_at_scale.py): `score_shop_customers` runs an unbounded
per-shop-history `GROUP BY customer_email` that the planner *intermittently*
resolves with an external-merge **disk sort** at large-merchant scale
(measured: 148ms / Disk 2024kB at 50k orders / 6,250 customers, plan-unstable
run-to-run with table bloat). An EXPLAIN-proven covering index did NOT fix it
(planner ignored it — measured, not assumed). The structural, planner-
independent fix is the established cache-first + stampede-lock pattern
(mirrors recurring_buyers.py): the heavy query runs at most once per shop per
TTL instead of once per request, and `get_lazy_read_db` makes a cache hit hold
zero pooled connections (class-consistent with the 9ec6cdc cache-first sweep —
required to stay GREEN in audit_cachefirst_conn_pin).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_lazy_read_db
from app.core.deps import require_pro_session
from app.services.customer_churn_scorer import score_shop_customers
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger(__name__)
router = APIRouter(prefix="/pro", tags=["customer_churn"])

# 30min — churn ("at risk in next 30 days") is daily-fresh by nature; matches
# the recurring_buyers analytics-cache cadence.
_CACHE_TTL_SECONDS = 30 * 60
_CACHE_KEY_PREFIX = "hs:customer_churn:v1"
_LOCK_KEY_PREFIX = "hs:customer_churn:lock:v1"
_LOCK_TTL_SECONDS = 30
_LOCK_WAIT_BUDGET_SEC = 2.0
# Compute the full top-N once (the heavy query cost is independent of the
# caller's `limit` — it only slices the already-sorted result), then serve
# any caller `limit` ≤ this from the one cached entry. Keying by shop only
# (not by limit) means the expensive query cannot be cache-busted by varying
# `limit`. A limit=50 slice of the cached top-500 equals computing with
# limit=50 ONLY because score_shop_customers' SQL now has a deterministic
# `ORDER BY COUNT(*) DESC, customer_email` before its `LIMIT 5000` cap
# (added 2026-05-16d after an adversarial audit found the prior cap was a
# NONDETERMINISTIC 5000-row sample for >5000-customer merchants — the
# slice-equivalence claim was false without it). If that ORDER BY is ever
# removed, this equivalence breaks for >5000-customer shops — do not.
_MAX_LIMIT = 500


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
            record_silent_return("customer_churn.cache_read_redis_down")
            return None
        cached = rc.get(_cache_key(shop))
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("customer_churn: cache read failed for %s: %s", shop, exc)
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
        log.warning("customer_churn: cache write failed for %s: %s", shop, exc)


def _acquire_lock(shop: str) -> bool:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("customer_churn.lock_acquire_redis_down")
            return True  # no Redis → no stampede protection, proceed
        return bool(rc.set(_lock_key(shop), "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception as exc:
        log.warning("customer_churn: lock acquire failed for %s: %s", shop, exc)
        return True


def _wait_for_cache(shop: str) -> dict | None:
    deadline = time.monotonic() + _LOCK_WAIT_BUDGET_SEC
    while time.monotonic() < deadline:
        cached = _read_cached(shop)
        if cached is not None:
            return cached
        time.sleep(0.1)
    return None


def _shape(full: dict, limit: int) -> dict:
    """Slice the cached full top-N to the caller's `limit` and recompute the
    band summary over the slice — byte-identical to having called
    score_shop_customers(limit=limit) directly (same desc-sorted prefix)."""
    customers = full.get("customers", [])[:limit]
    summary = {
        "critical": sum(1 for c in customers if c["risk_band"] == "critical"),
        "high": sum(1 for c in customers if c["risk_band"] == "high"),
        "medium": sum(1 for c in customers if c["risk_band"] == "medium"),
        "low": sum(1 for c in customers if c["risk_band"] == "low"),
    }
    return {
        "shop_domain": full["shop_domain"],
        "total_customers_scored": len(customers),
        "by_risk_band": summary,
        "customers": customers,
        "currency": full["currency"],
    }


@router.get("/customer-churn")
def list_at_risk_customers(
    limit: int = Query(50, ge=1, le=500),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_lazy_read_db),  # ε1 — cache hit = 0 conns
):
    cached = _read_cached(shop)
    if cached is not None:
        return _shape(cached, limit)

    if not _acquire_lock(shop):
        waited = _wait_for_cache(shop)
        if waited is not None:
            return _shape(waited, limit)
        # lock-timeout fail-open → fall through and compute

    scored = score_shop_customers(db, shop, limit=_MAX_LIMIT)
    # Resolve shop currency so the dashboard renders `avg_order_value_eur`
    # with the merchant's native symbol. Falls back to USD when lookup
    # returns None (brand-new shop with no order history yet).
    currency = get_shop_currency(db, shop) or "USD"
    full = {
        "shop_domain": shop,
        "customers": scored,
        "currency": currency,
    }
    _write_cached(shop, full)
    return _shape(full, limit)
