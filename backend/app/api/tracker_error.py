"""
tracker_error.py — Public error-telemetry endpoint for the storefront tracker.

POST /public/tracker-error
    Accepts runtime error reports from spark-tracker.js / spark-pixel.js /
    spark-nudge.js. No auth (public-shop-facing), rate-limited per shop,
    payload PII-scrubbed server-side.

Why this exists
---------------
Before 2026-04-17 the tracker scripts ran in the merchant's shopper
browsers with ZERO error telemetry. A syntax regression, a browser
quirk, an API change — nothing came back to us. Shoppers saw a broken
tracker, merchants lost event signal, we lost visibility. This endpoint
closes that blind spot. Aggregated counts + spike detection land via
the aggregation worker → ops_alert → self-healing pipeline.

Storage
-------
One `ops_alerts` row per accepted report, `source="tracker_runtime"`,
`severity="info"`. The aggregation worker scans the last 24h and raises
a `tracker_runtime_error_spike` (severity=warning) when a shop crosses
the distinct-error-hash threshold so we don't drown in duplicate noise.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db


def datetime_utcnow_isodate() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Redis keys for per-shop per-day aggregation. 7-day TTL covers the
# 24h detection window plus a buffer for forensics. All keys include
# the date so yesterday's counts auto-expire without tombstone cleanup.
_KEY_TOTAL    = "hs:trkerr:tot:{shop}:{date}"     # count of all reports
_KEY_HASHES   = "hs:trkerr:hash:{shop}:{date}"    # set of distinct error_hashes
_KEY_SAMPLE   = "hs:trkerr:sample:{shop}:{date}:{hash}"  # last seen detail
_TTL_SECONDS  = 7 * 86400


def _persist_to_redis(*, shop: str, error_hash: str, date: str, detail: dict) -> bool:
    """Bump per-(shop, date) counters and remember a sample of the detail
    payload for each distinct error_hash. Returns True on success.

    A Redis outage degrades to False so the endpoint surfaces the
    persistence failure — better to return 500-like to the tracker
    than silently lose observability data."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("tracker_error.persist.no_client")
            return False
        total_key = _KEY_TOTAL.format(shop=shop, date=date)
        hash_key = _KEY_HASHES.format(shop=shop, date=date)
        sample_key = _KEY_SAMPLE.format(shop=shop, date=date, hash=error_hash)
        pipe = rc.pipeline()
        pipe.incr(total_key)
        pipe.expire(total_key, _TTL_SECONDS)
        pipe.sadd(hash_key, error_hash)
        pipe.expire(hash_key, _TTL_SECONDS)
        # Only write a full sample on first observation of the hash
        # today — `setex NX-equivalent via set with nx=True` semantics.
        pipe.set(sample_key, json.dumps(detail, default=str), ex=_TTL_SECONDS, nx=True)
        pipe.execute()
        return True
    except Exception as exc:
        log.warning("tracker_error: redis persist failed shop=%s: %s", shop, exc)
        return False

log = logging.getLogger("tracker_error")

router = APIRouter(tags=["tracker_error"])


# Rate limits (per-shop):
#   - burst: 10 reports per minute
#   - daily: 500 reports per 24h (prevents a pathological merchant from
#     DoS-ing ops_alerts with a tight error loop)
_RATE_BURST_KEY = "hs:trkerr:burst:{shop}"
_RATE_BURST_TTL = 60
_RATE_BURST_MAX = 10

_RATE_DAY_KEY = "hs:trkerr:day:{shop}"
_RATE_DAY_TTL = 86400
_RATE_DAY_MAX = 500


# PII patterns — strip before we persist ANY text that came from the wild.
# We don't need the actual values for debugging; we need the structural
# hash to dedupe and the scrubbed message to recognize patterns.
# Mirrors app/core/llm_pii_guard.py scrubbing conventions so the two
# paths stay behaviorally consistent.
_EMAIL_RE  = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_TOKEN_RE  = re.compile(r"\b(shpat_[A-Za-z0-9]{20,}|shpca_[A-Za-z0-9]{20,}|sk_[a-z]+_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})\b")
_IBAN_RE   = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_CC_RE     = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE_RE  = re.compile(r"\+?\d{1,3}[\s\-.]?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}")
_JWT_RE    = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")


def _scrub(text: str | None) -> str:
    if not text:
        return ""
    s = str(text)[:2000]  # hard cap — we never need more for debugging
    for rx, tag in [
        (_JWT_RE,   "[JWT]"),
        (_TOKEN_RE, "[TOKEN]"),
        (_EMAIL_RE, "[EMAIL]"),
        (_IBAN_RE,  "[IBAN]"),
        (_CC_RE,    "[CC]"),
        (_PHONE_RE, "[PHONE]"),
    ]:
        s = rx.sub(tag, s)
    return s


def _error_hash(scrubbed_msg: str, tracker_src: str) -> str:
    """Stable hash over the scrubbed message + source tracker file so the
    aggregation worker can count distinct errors vs sheer volume."""
    h = hashlib.sha1()
    h.update(scrubbed_msg.encode("utf-8", errors="ignore"))
    h.update(b"\x00")
    h.update(tracker_src.encode("utf-8", errors="ignore"))
    return h.hexdigest()[:16]


def _check_rate_limit(shop: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Fail-open on Redis outage — we'd rather
    accept a few extra reports than lose visibility entirely.

    Under APP_ENV=test the rate limit is disabled so repeated test runs
    against the same Redis instance don't leak state across tests."""
    import os
    if os.environ.get("APP_ENV") == "test":
        return True, "ok"
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("tracker_error.rate_limit.no_client")
            return True, "fail_open"
        # Burst window
        burst_key = _RATE_BURST_KEY.format(shop=shop)
        count = rc.incr(burst_key)
        if count == 1:
            rc.expire(burst_key, _RATE_BURST_TTL)
        if count > _RATE_BURST_MAX:
            return False, "burst_exceeded"
        # Daily ceiling
        day_key = _RATE_DAY_KEY.format(shop=shop)
        day_count = rc.incr(day_key)
        if day_count == 1:
            rc.expire(day_key, _RATE_DAY_TTL)
        if day_count > _RATE_DAY_MAX:
            return False, "daily_exceeded"
        return True, "ok"
    except Exception:
        record_silent_return("tracker_error.rate_limit.exception")
        return True, "fail_open"


class TrackerErrorIn(BaseModel):
    shop: str = Field(..., min_length=3, max_length=255)
    source: str = Field(..., max_length=64)  # "spark-tracker" | "spark-pixel" | "spark-nudge" | …
    message: str = Field("", max_length=2000)
    stack: str = Field("", max_length=4000)
    url: str = Field("", max_length=500)
    tracker_version: int | None = Field(None, ge=0, le=10_000)
    user_agent: str = Field("", max_length=300)


@router.post("/public/tracker-error")
def post_tracker_error(
    payload: TrackerErrorIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Accept a tracker runtime error report. Idempotent/dedup-ed via
    error_hash; always 200 to prevent tracker retry storms."""
    shop = (payload.shop or "").strip().lower()
    if not shop or shop == "undefined":
        return {"ok": False, "reason": "invalid_shop"}

    allowed, reason = _check_rate_limit(shop)
    if not allowed:
        # Soft-drop — return 200 so the tracker doesn't retry.
        return {"ok": False, "reason": reason}

    scrubbed_msg = _scrub(payload.message)
    scrubbed_stack = _scrub(payload.stack)
    url_host_only = ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(payload.url or "")
        url_host_only = parsed.netloc[:120]  # strip query + fragments + path
    except Exception:
        pass  # SILENT-EXCEPT-OK: malformed URL is not worth logging — url_host stays empty

    src = payload.source[:64] or "unknown"
    err_hash = _error_hash(scrubbed_msg, src)
    ua_short = (payload.user_agent or "")[:120]

    # Write the row. Keep payload small — aggregation worker only needs
    # error_hash + source + tracker_version for counting.
    detail: dict[str, Any] = {
        "error_hash": err_hash,
        "tracker_src": src,
        "message": scrubbed_msg[:500],
        "stack_head": scrubbed_stack[:400],
        "url_host": url_host_only,
        "tracker_version": payload.tracker_version,
        "user_agent_head": ua_short,
        "received_at": int(time.time()),
    }

    # Write to Redis counters — the spike detector reads from here.
    # We don't use ops_alerts for per-event rows because its built-in
    # dedup (5-min acute + chronic collapse) intentionally reduces
    # 100 duplicate reports to 1 row. For tracker errors we need the
    # FULL count of distinct error_hashes per (shop, day), which dedup
    # would throw away. Redis counters preserve both total volume and
    # distinct fingerprints cheaply (< 100 bytes / shop / day).
    today = datetime_utcnow_isodate()
    persisted = _persist_to_redis(shop=shop, error_hash=err_hash, date=today, detail=detail)
    if not persisted:
        return {"ok": False, "reason": "persist_failed"}

    # `db` is declared as a dependency but not used on this happy path —
    # referenced here to silence type-check complaints about the unused
    # parameter. Kept as a Depends so a future DB-write path stays simple.
    _ = db
    return {"ok": True, "hash": err_hash}
