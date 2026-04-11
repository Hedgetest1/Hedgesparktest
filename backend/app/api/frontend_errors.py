"""
frontend_errors.py — Frontend error intake for the self-healing pipeline.

Closes a long-standing blind spot: before this module, any React exception,
failed fetch, or rendering crash in the Next.js dashboard was **invisible** to
the autonomous repair loop. Sentry-triage only parses backend stacktraces, so
frontend regressions lived until a merchant complained.

This endpoint is the bridge: the dashboard calls POST /ops/frontend-errors
whenever its global error boundary, window.onerror, or unhandledrejection
handler fires. Each report is normalized, fingerprinted, and written to
ops_alerts with alert_type='frontend_error'. The existing bugfix triage
pipeline then picks it up (see bugfix_pipeline.run_bug_triage Rule 5) and
the standard triage → propose → apply loop runs — no separate codepath.

Contract
--------
Public endpoint (NO auth): errors happen in pre-auth flows (e.g. install,
billing setup) and we must accept them even when the session cookie is
missing or invalid. Payload validation is strict; rate-limiting is per-IP
via Redis.

Payload:
    {
        "component": str,         # React component/route that reported it (<64)
        "error_type": str,        # e.g. "TypeError", "FetchError" (<64)
        "message": str,           # error.message (<512)
        "stack": str | None,      # first ~2KB only — no full stacks leaked
        "url": str | None,        # window.location.href for context (<256)
        "user_agent": str | None, # <256
        "shop_domain": str | None,# if the reporter knows it (<256)
        "severity": str | None,   # "critical" | "warning" | "info"; default warning
        "extra": dict | None,     # optional structured context (<1KB json)
    }

Returns: 202 Accepted on success (fire-and-forget semantics), 429 if
rate-limited, 400 on payload errors. Never raises — frontend reporting
must never cascade into a second error.

Fingerprinting
--------------
source_ref is `fe:{component}:{hash8}` where hash8 is md5(error_type+message)
truncated to 8 chars. This collapses repeated reports of the same error
into one triage candidate, while different error classes or different
components create distinct candidates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.alerting import write_alert

log = logging.getLogger("frontend_errors")

router = APIRouter(prefix="/ops", tags=["ops"])


# ---------------------------------------------------------------------------
# Payload — strict limits prevent log bloat and denial-of-disk attacks
# ---------------------------------------------------------------------------

_MAX_MESSAGE = 512
_MAX_STACK = 2048
_MAX_URL = 256
_MAX_UA = 256
_MAX_COMPONENT = 64
_MAX_ERROR_TYPE = 64
_MAX_SHOP = 256
_MAX_EXTRA_BYTES = 1024
_ALLOWED_SEVERITIES = {"critical", "warning", "info"}
# Strip anything that looks like a bearer token / secret from captured strings
# before persistence. Not a security boundary — defense in depth.
_SECRET_RE = re.compile(
    r"(bearer\s+[\w.\-]{8,}|api[_-]?key[=:]\s*[\w.\-]{8,}|sk_live_[\w]{8,}|sk_test_[\w]{8,})",
    re.IGNORECASE,
)


def _sanitize(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    clean = _SECRET_RE.sub("[REDACTED]", value)
    return clean[:limit]


class FrontendErrorPayload(BaseModel):
    component: str = Field(..., min_length=1, max_length=_MAX_COMPONENT)
    error_type: str = Field(..., min_length=1, max_length=_MAX_ERROR_TYPE)
    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE)
    stack: str | None = Field(None, max_length=_MAX_STACK)
    url: str | None = Field(None, max_length=_MAX_URL)
    user_agent: str | None = Field(None, max_length=_MAX_UA)
    shop_domain: str | None = Field(None, max_length=_MAX_SHOP)
    severity: str | None = Field("warning")
    extra: dict[str, Any] | None = None

    @field_validator("severity")
    @classmethod
    def _check_severity(cls, v: str | None) -> str:
        if v is None or v not in _ALLOWED_SEVERITIES:
            return "warning"
        return v

    @field_validator("component", "error_type")
    @classmethod
    def _check_identifier(cls, v: str) -> str:
        # Components are arbitrary strings from the frontend but should never
        # carry newlines or control chars — keeps log lines clean.
        return re.sub(r"[\x00-\x1f]", "", v).strip()[:_MAX_COMPONENT]


# ---------------------------------------------------------------------------
# Rate limiting — per-IP token bucket backed by Redis.
# ---------------------------------------------------------------------------
# A misbehaving client could spam this endpoint in an infinite loop
# (error → handler → POST → rejected → error). Cap per-IP to 30 reports/min.
# Redis key `hs:fe_errors:{ip}` is a simple counter with 60s expiry.

_RATE_LIMIT_PER_MIN = 30


def _client_ip(request: Request) -> str:
    # Honor X-Forwarded-For when behind Traefik; fall back to request.client.host.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host[:64]
    return "unknown"


def _rate_limit_check(ip: str) -> bool:
    """Return True if the caller is under the limit, False if blocked."""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r is None:
            return True  # fail-open if redis is down — alerting matters more
        key = f"hs:fe_errors:{ip}"
        n = r.incr(key)
        if n == 1:
            r.expire(key, 60)
        return int(n) <= _RATE_LIMIT_PER_MIN
    except Exception:
        return True  # fail-open on any redis hiccup


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/frontend-errors",
    status_code=status.HTTP_202_ACCEPTED,
)
def report_frontend_error(
    payload: FrontendErrorPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """
    Ingest a frontend error report and forward it to the ops_alert pipeline.

    Fire-and-forget: the frontend does not wait for processing. We always
    return 202 unless rate-limited. Any DB or downstream error is logged
    but not raised to the caller.
    """
    ip = _client_ip(request)
    if not _rate_limit_check(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="frontend error reports rate-limited (30/min per ip)",
        )

    component = _sanitize(payload.component, _MAX_COMPONENT) or "unknown"
    error_type = _sanitize(payload.error_type, _MAX_ERROR_TYPE) or "UnknownError"
    message = _sanitize(payload.message, _MAX_MESSAGE) or ""

    # Fingerprint: collapse repeated reports of the same error into one
    # stable source_ref so triage dedup works across sessions/users.
    fingerprint_raw = f"{error_type}::{message}"
    fingerprint = hashlib.md5(fingerprint_raw.encode("utf-8")).hexdigest()[:8]
    source = f"fe:{component}:{fingerprint}"

    extra_json: str | None = None
    if payload.extra:
        try:
            raw = json.dumps(payload.extra, default=str)
            if len(raw) <= _MAX_EXTRA_BYTES:
                extra_json = raw
        except Exception:
            extra_json = None

    detail = {
        "error_type": error_type,
        "message": message,
        "stack": _sanitize(payload.stack, _MAX_STACK),
        "url": _sanitize(payload.url, _MAX_URL),
        "user_agent": _sanitize(payload.user_agent, _MAX_UA),
        "component": component,
        "fingerprint": fingerprint,
        "reporter_ip": ip,
        "extra": extra_json,
    }

    summary = f"[{component}] {error_type}: {message[:180]}"

    try:
        write_alert(
            db,
            severity=payload.severity or "warning",
            source=source,
            alert_type="frontend_error",
            summary=summary,
            shop_domain=_sanitize(payload.shop_domain, _MAX_SHOP),
            detail=detail,
        )
        db.commit()
    except Exception as exc:
        log.warning(
            "frontend_errors: write_alert failed component=%s err=%s: %s",
            component, error_type, exc,
        )
        # Never let a logging error cascade to the frontend.
        try:
            db.rollback()
        except Exception:
            pass

    return {"accepted": True, "source": source}
