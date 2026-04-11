"""
Thin Redis cache layer for HedgeSpark.

Public interface
----------------
    cache_get(key)                       -> Any | None
    cache_set(key, value, ttl_seconds)   -> None
    cache_delete(key)                    -> None

Fallback contract
-----------------
Every function is safe to call when Redis is unavailable.  Any
Redis error — connection refused, timeout, server restart — is caught,
logged at WARNING level, and the caller receives the safe fallback
value (None for reads, no-op for writes).  The rest of the system
must never require Redis to be present: it is a performance layer,
not a data store.

Initialisation
--------------
A single ConnectionPool is created at import time from REDIS_URL.
If REDIS_URL is absent the module runs in no-op mode (all reads
return None, all writes are silently skipped).

If Redis is unreachable at import time this is not fatal — the pool
is created but no connection is attempted until the first call.  A
warning is logged on the first failed operation, not at startup.

Key namespace
-------------
    hs:signals:{shop_domain}   TTL 300 s     list[dict] opportunity signals
    hs:brief:{shop_domain}     TTL 86400 s   AI daily brief text (Phase 2)

Both keys are written and read exclusively through this module.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key helpers — all cache consumers import these constants rather than
# constructing key strings inline.
# ---------------------------------------------------------------------------

KEY_SIGNALS = "hs:signals:{shop}"    # format with shop_domain
KEY_BRIEF = "hs:brief:{shop}"        # format with shop_domain

TTL_SIGNALS = 300       # 5 minutes
TTL_BRIEF = 86_400      # 24 hours
KEY_AI_COMPOSE = "hs:ai_compose:{hash}"    # format with payload hash
TTL_AI_COMPOSE = 86400  # 24 hours — same product + same signals = same copy (saves LLM calls)
KEY_DASHBOARD  = "hs:dash:{shop}"          # format with shop_domain
TTL_DASHBOARD  = 60     # 60 seconds — dashboard data refreshes on aggregation cycle

# ---------------------------------------------------------------------------
# Connection pool — created once at module import.
# _pool is None when REDIS_URL is absent (no-op mode).
# ---------------------------------------------------------------------------

_REDIS_URL: str | None = os.getenv("REDIS_URL")

_pool: redis.ConnectionPool | None = None

if _REDIS_URL:
    _pool = redis.ConnectionPool.from_url(
        _REDIS_URL,
        # Short timeouts prevent a slow or hung Redis from blocking
        # the API request path.
        socket_connect_timeout=1,
        socket_timeout=1,
        # Decode byte responses to str automatically.
        decode_responses=True,
        # Keep the pool small — this is a cache layer, not a primary store.
        max_connections=10,
    )
else:
    logger.warning(
        "redis_client: REDIS_URL is not set — running in no-op mode. "
        "All cache reads will miss; signal detection will fall back to "
        "the PostgreSQL fresh-signal read path on every request."
    )


def _client() -> redis.Redis | None:
    """
    Return a Redis client bound to the shared pool, or None in no-op mode.
    Does NOT raise — returns None if the pool was never created.
    """
    if _pool is None:
        return None
    return redis.Redis(connection_pool=_pool)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_get(key: str) -> Any | None:
    """
    Return the deserialised value stored at *key*, or None on any error.

    Returns None when:
      - Redis is unavailable or unreachable
      - The key does not exist (cache miss)
      - The stored value cannot be deserialised
      - REDIS_URL is not configured (no-op mode)
    """
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            try:
                from app.core.metrics import track_cache_miss
                track_cache_miss()
            except Exception:
                pass
            return None
        try:
            from app.core.metrics import track_cache_hit
            track_cache_hit()
        except Exception:
            pass
        return json.loads(raw)
    except redis.RedisError as exc:
        logger.warning("redis_client.cache_get(%r) failed: %s", key, exc)
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "redis_client.cache_get(%r) — deserialisation error: %s", key, exc
        )
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    """
    Serialise *value* as JSON and store it at *key* with the given TTL.

    Silently no-ops when:
      - Redis is unavailable or unreachable
      - *value* cannot be serialised to JSON
      - REDIS_URL is not configured (no-op mode)
    """
    client = _client()
    if client is None:
        return
    try:
        client.setex(key, ttl_seconds, json.dumps(value))
    except redis.RedisError as exc:
        logger.warning("redis_client.cache_set(%r) failed: %s", key, exc)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "redis_client.cache_set(%r) — serialisation error: %s", key, exc
        )


def cache_delete(key: str) -> None:
    """
    Delete *key* from the cache.

    Silently no-ops when Redis is unavailable or not configured.
    """
    client = _client()
    if client is None:
        return
    try:
        client.delete(key)
    except redis.RedisError as exc:
        logger.warning("redis_client.cache_delete(%r) failed: %s", key, exc)
