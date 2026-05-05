"""
rum.py — POST /rum/metric endpoint for real-user web-vitals ingestion.

Contract
--------
Public endpoint (NO auth): the dashboard emits vitals on every visit,
including pre-auth screens like /install and /pricing. Payload is
strict; rate-limiting is per-IP via Redis (30 req/min).

Payload:
    {
        "route": str,        # path the vital was measured on (e.g. /app)
        "metric": str,       # one of ttfb|fcp|lcp|cls|inp
        "value": float,      # ms for time metrics, unitless 0-1+ for cls
    }

Returns 202 on accept, 400 on invalid payload, 429 on rate-limit.
Never raises — ingestion must not cascade errors back to the browser.

Design notes
------------
The endpoint is deliberately thin. All aggregation + regression logic
lives in app/services/rum_monitor.py so the ingestion hot path does
one Redis pipeline write per sample. At 10k merchants × 5 metrics ×
5 routes × 1 visit/day we expect ~250k writes/day peak — well within
Redis throughput.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.rum_monitor import ALLOWED_METRICS, ingest_sample

log = logging.getLogger("rum")

router = APIRouter(prefix="/rum", tags=["rum"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

_MAX_ROUTE = 128


class RumMetricPayload(BaseModel):
    route: str = Field(..., min_length=1, max_length=_MAX_ROUTE)
    metric: str = Field(..., min_length=1, max_length=16)
    value: float = Field(..., ge=0.0, le=60_000.0)

    @field_validator("metric")
    @classmethod
    def _check_metric(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_METRICS:
            raise ValueError(f"metric must be one of {ALLOWED_METRICS}")
        return v

    @field_validator("route")
    @classmethod
    def _clean_route(cls, v: str) -> str:
        # Strip control chars; upstream normalization (query/hash stripping,
        # leading slash) happens in rum_monitor._safe_route.
        return re.sub(r"[\x00-\x1f]", "", v).strip()[:_MAX_ROUTE]


# ---------------------------------------------------------------------------
# Per-IP rate limit — same shape as frontend_errors.
# ---------------------------------------------------------------------------

_RATE_LIMIT_PER_MIN = 120  # 5 metrics × ~1 route × 1 emit/visit, headroom ×20


def _client_ip(request: Request) -> str:
    from app.core.client_ip import extract_client_ip
    return extract_client_ip(request)


def _rate_limit_check(ip: str) -> bool:
    """Return True if the caller is under the limit, False when blocked.
    Fail-open when Redis is unreachable — RUM matters less than uptime."""
    try:
        from app.core.redis_client import _client
        r = _client()
        if r is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("rum.rate_limit")
            return True
        key = f"hs:rum_rl:{ip}"
        n = r.incr(key)
        if n == 1:
            r.expire(key, 60)
        return int(n) <= _RATE_LIMIT_PER_MIN
    except Exception as exc:
        log.warning("rum: rate limit check failed: %s", exc)
        return True


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/metric", status_code=status.HTTP_202_ACCEPTED)
def report_rum_metric(
    payload: RumMetricPayload,
    request: Request,
    _db: Session = Depends(get_db),  # kept for symmetry; ingest uses redis only
) -> dict:
    """Store one web-vitals sample. Fire-and-forget."""
    ip = _client_ip(request)
    if not _rate_limit_check(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rum metric reports rate-limited (120/min per ip)",
        )

    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        rc = None

    stored = ingest_sample(rc, payload.route, payload.metric, payload.value)
    return {"accepted": stored}
