"""
email_deliverability.py — Resend DNS verification gate + runtime sender guard.

Context
-------
On 2026-04-12 the Resend domain verification for `hedgesparkhq.com` flipped
to `failed` (DKIM/SPF detached DNS-side after a Hostinger zone edit). Every
transactional/digest email routed through `HedgeSpark <dev@hedgesparkhq.com>`
has been silently suppressed by Resend since then — the API call succeeds
at the network layer but the recipient never sees the email. Merchants on
the beta list wondering why no morning brief arrived is a trust leak the
self-healing pipeline cannot repair on its own (DNS is an external dependency
that requires founder intervention at the registrar).

This module catches the *class* of bug, not just the current incident:

1. `is_domain_verified()` caches the Resend API domain status in Redis for
   10 min and returns True/False.
2. `send_email()` in `app/core/email.py` consults this gate before attempting
   delivery through any `@hedgesparkhq.com` sender — if the domain is
   `failed`, the send is short-circuited (logged, counted, not attempted)
   instead of producing a misleading Resend rejection.
3. The agent worker hourly task refreshes the cache and detects the
   `failed → verified` flip (or the reverse) and fires a Telegram alert.
4. A preflight audit warns the operator when a commit lands while DNS is
   failed so the state doesn't fall off the radar during unrelated work.

Fail-open policy
----------------
When the Resend API is unreachable (network timeout, 5xx, or missing
`RESEND_API_KEY`) we return `verified=True`. The preventer is designed to
catch KNOWN misconfig (`status=failed`), not to police transient API
reachability — we never want to suppress email because we briefly could
not check.

Public interface
----------------
    get_domain_status(force_refresh=False) -> dict
    is_domain_verified() -> bool
    uses_org_domain(from_address: str) -> bool
    invalidate_cache() -> None

Redis keys
----------
    hs:email:domain_status:v1    TTL 600   cached API response
    hs:email:last_verified:v1    no TTL    sticky flip-detection state
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

_REDIS_KEY_STATUS = "hs:email:domain_status:v1"
_REDIS_KEY_LAST_VERIFIED = "hs:email:last_verified:v1"
_CACHE_TTL_S = 600
# The sticky "last verified state" is rewritten every hour by the agent
# worker task, so a 30-day TTL leaves ~720 re-write chances before expiry.
# If the worker is down for >30d we lose flip context and the next run
# treats it as a first observation (silent) — acceptable trade-off to
# keep this key aligned with the CLAUDE.md §12 "every key has a TTL"
# invariant enforced by test_every_redis_set_has_ttl.
_LAST_VERIFIED_TTL_S = 2_592_000  # 30 days

# Resend domain id for hedgesparkhq.com — static, set when the domain was
# first registered with Resend (2026-03-25). Never rotates.
_RESEND_DOMAIN_ID = "b65abad8-43f3-4dfe-aaa7-29b62a701495"
_ORG_DOMAIN = "hedgesparkhq.com"
_API_TIMEOUT_S = 4.0


def _fetch_domain_status() -> Optional[dict]:
    """Hit Resend API once. Returns the response dict or None on any error."""
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        return None
    url = f"https://api.resend.com/domains/{_RESEND_DOMAIN_ID}"
    try:
        import httpx

        with httpx.Client(timeout=_API_TIMEOUT_S) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code != 200:
                log.warning(
                    "email_deliverability: Resend API non-200 status=%d",
                    resp.status_code,
                )
                return None
            return resp.json()
    except Exception as exc:
        log.warning("email_deliverability: Resend fetch failed: %s", exc)
        return None


def get_domain_status(force_refresh: bool = False) -> dict:
    """
    Return current deliverability state for `@hedgesparkhq.com`.

    Shape:
        {
          "verified": bool,          # True if status == 'verified' or API unreachable
          "status": str,             # verified | failed | pending | unknown
          "reason": str,             # human-readable, empty when verified
          "fetched_at": float,       # unix ts of the API response (or cache hit)
          "from_cache": bool,        # whether this came from Redis
        }

    Cache hit returns the stored payload. Miss/force_refresh fetches fresh.
    API failure fails OPEN (verified=True, status='unknown').
    """
    if not force_refresh:
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                cached = rc.get(_REDIS_KEY_STATUS)
                if cached:
                    payload = json.loads(cached)
                    payload["from_cache"] = True
                    return payload
        except Exception as exc:
            log.warning("email_deliverability: cache read failed: %s", exc)

    data = _fetch_domain_status()
    now = time.time()
    if data is None:
        # Fail-open: unknown state = treat as verified so we never suppress
        # email because the Resend API had a transient hiccup.
        return {
            "verified": True,
            "status": "unknown",
            "reason": "api_unreachable",
            "fetched_at": now,
            "from_cache": False,
        }

    status = str(data.get("status", "unknown"))
    verified = status == "verified"
    payload = {
        "verified": verified,
        "status": status,
        "reason": "" if verified else f"resend_status={status}",
        "fetched_at": now,
        "from_cache": False,
    }

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            # Strip the from_cache field before persisting (it's per-request).
            persistable = {k: v for k, v in payload.items() if k != "from_cache"}
            rc.set(_REDIS_KEY_STATUS, json.dumps(persistable), ex=_CACHE_TTL_S)
    except Exception as exc:
        log.warning("email_deliverability: cache write failed: %s", exc)

    return payload


def is_domain_verified() -> bool:
    return bool(get_domain_status().get("verified", True))


def uses_org_domain(from_address: str | None) -> bool:
    """True when from_address targets @hedgesparkhq.com (the org domain)."""
    if not from_address:
        return False
    return _ORG_DOMAIN in from_address.lower()


def invalidate_cache() -> None:
    """Drop the Redis cache so the next read fetches fresh. Best-effort."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(_REDIS_KEY_STATUS)
    except Exception as exc:
        log.warning("email_deliverability: cache delete failed: %s", exc)


def read_last_verified_state() -> Optional[bool]:
    """Return the last recorded verified state (sticky), or None if unknown."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(_REDIS_KEY_LAST_VERIFIED)
            if raw is None:
                return None
            return raw.decode() == "1" if isinstance(raw, bytes) else raw == "1"
    except Exception as exc:
        log.warning("email_deliverability: last-state read failed: %s", exc)
    return None


def write_last_verified_state(verified: bool) -> None:
    """Persist the latest verified state so the hourly task can detect flips."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.set(
                _REDIS_KEY_LAST_VERIFIED,
                "1" if verified else "0",
                ex=_LAST_VERIFIED_TTL_S,
            )
    except Exception as exc:
        log.warning("email_deliverability: last-state write failed: %s", exc)
