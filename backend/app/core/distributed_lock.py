"""
distributed_lock.py — Redis-based distributed locking for worker safety.

Provides two primitives:

1. worker_lock(name, ttl) — context manager ensuring only one instance of a
   worker runs at a time across all processes/pods.  Uses Redis SET NX with
   automatic TTL expiry as a dead-man switch.

2. advisory_lock(key, ttl) — lower-level lock for arbitrary resources
   (e.g., per-shop operations).

Fallback: when Redis is unavailable, locks are NOT acquired and the caller
proceeds (fail-open for workers, since PM2 singleton is the primary guard).
A WARNING is logged so operators know distributed safety is degraded.

Why not Postgres advisory locks?
- Advisory locks are connection-scoped — they release when the connection
  closes, which is correct for request-scoped work but wrong for workers
  that hold locks across multiple DB sessions within a single cycle.
- Redis TTL acts as a dead-man switch if the process crashes mid-cycle.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Generator

from app.core.silent_fallback import record_silent_return

logger = logging.getLogger(__name__)

# Unique identity for this process — used to ensure only the holder can release.
_PROCESS_ID = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _get_redis():
    """Return a Redis client or None if unavailable."""
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


@contextmanager
def worker_lock(
    name: str,
    ttl_seconds: int = 600,
) -> Generator[bool, None, None]:
    """
    Distributed worker lock.  Usage:

        with worker_lock("aggregation_worker", ttl_seconds=360) as acquired:
            if not acquired:
                logger.info("Another instance running, skipping cycle")
                return
            # ... do work ...

    The lock auto-expires after ttl_seconds (dead-man switch).
    Only the holder can release it (value contains process ID).
    """
    key = f"hs:wlock:{name}"
    client = _get_redis()

    if client is None:
        logger.warning(
            "distributed_lock: Redis unavailable — proceeding WITHOUT lock for %s. "
            "PM2 singleton is the only safety guard.",
            name,
        )
        yield True
        return

    acquired = False
    try:
        # SET NX with TTL — atomic acquire
        acquired = bool(client.set(key, _PROCESS_ID, nx=True, ex=ttl_seconds))
        if not acquired:
            holder = client.get(key)
            logger.info(
                "distributed_lock: %s already held by %s — skipping cycle",
                name, holder,
            )
        yield acquired
    except Exception as exc:
        logger.warning("distributed_lock: error acquiring %s: %s — proceeding unlocked", name, exc)
        yield True  # fail-open
    finally:
        if acquired and client is not None:
            try:
                # Only release if we still hold it (Lua atomic check-and-delete)
                _release_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                client.eval(_release_script, 1, key, _PROCESS_ID)
            except Exception as exc:
                logger.warning("distributed_lock: error releasing %s: %s", name, exc)


@contextmanager
def advisory_lock(
    key: str,
    ttl_seconds: int = 300,
) -> Generator[bool, None, None]:
    """
    General-purpose advisory lock for arbitrary resources.

    Example: lock per-shop operations to prevent concurrent repairs.

        with advisory_lock(f"repair:{shop_domain}", ttl_seconds=120) as acquired:
            if not acquired:
                return  # another process is repairing this shop
            do_repair(shop_domain)
    """
    full_key = f"hs:alock:{key}"
    client = _get_redis()

    if client is None:
        record_silent_return("distributed_lock.advisory")
        yield True
        return

    acquired = False
    try:
        acquired = bool(client.set(full_key, _PROCESS_ID, nx=True, ex=ttl_seconds))
        yield acquired
    except Exception as exc:
        logger.warning("advisory_lock: error on %s: %s", key, exc)
        yield True
    finally:
        if acquired and client is not None:
            try:
                _release_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                client.eval(_release_script, 1, full_key, _PROCESS_ID)
            except Exception as exc:
                logger.warning("advisory_lock: error releasing %s: %s", key, exc)


def extend_lock(name: str, ttl_seconds: int = 600) -> bool:
    """
    Extend the TTL of a held worker lock (heartbeat).

    Call this periodically during long cycles to prevent the dead-man
    switch from expiring while work is still in progress.

    Returns True if extended, False if lock was lost or Redis unavailable.
    """
    key = f"hs:wlock:{name}"
    client = _get_redis()
    if client is None:
        record_silent_return("distributed_lock.extend")
        return False
    try:
        # Only extend if we still hold it
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        result = client.eval(script, 1, key, _PROCESS_ID, ttl_seconds)
        return bool(result)
    except Exception as exc:
        logger.warning("distributed_lock: error extending %s: %s", name, exc)
        return False
