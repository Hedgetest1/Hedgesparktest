"""
repair_claim.py — Distributed repair claim/lease to prevent concurrent repairs.

Uses Redis SET NX with TTL for distributed locking.
Falls back to in-process dict when Redis is unavailable.

Public interface:
    try_claim_repair(shop_domain, area) -> bool
    release_repair_claim(shop_domain, area) -> None
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("repair_claim")

_CLAIM_TTL_SECONDS = 300  # 5 minutes — no single repair takes longer

# In-process fallback when Redis is unavailable
_fallback_claims: dict[str, float] = {}


def _claim_key(shop_domain: str, area: str) -> str:
    return f"hs:repair_claim:{shop_domain}:{area}"


def try_claim_repair(shop_domain: str, area: str) -> bool:
    """
    Attempt to acquire a repair claim for (shop, area).
    Returns True if claim acquired, False if already held.
    Safe when Redis is down — uses in-process fallback.
    """
    key = _claim_key(shop_domain, area)

    # Try Redis first
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            acquired = rc.set(key, "1", nx=True, ex=_CLAIM_TTL_SECONDS)
            if acquired:
                log.info("repair_claim: acquired %s (redis)", key)
                return True
            log.info("repair_claim: denied %s (redis — already held)", key)
            return False
    except Exception as exc:
        log.warning("repair_claim: redis error for %s, using fallback: %s", key, exc)

    # Fallback: in-process monotonic clock
    now = time.monotonic()
    expiry = _fallback_claims.get(key)
    if expiry is not None and (now - expiry) < _CLAIM_TTL_SECONDS:
        log.info("repair_claim: denied %s (fallback — already held)", key)
        return False
    _fallback_claims[key] = now
    log.info("repair_claim: acquired %s (fallback)", key)
    return True


def release_repair_claim(shop_domain: str, area: str) -> None:
    """Release a repair claim early (after repair completes)."""
    key = _claim_key(shop_domain, area)

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(key)
    except Exception as exc:
        log.warning("repair_claim: release failed for %s: %s", key, exc)

    _fallback_claims.pop(key, None)


def _clear_all_claims() -> None:
    """For testing only."""
    _fallback_claims.clear()
