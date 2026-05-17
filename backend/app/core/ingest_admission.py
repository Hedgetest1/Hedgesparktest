"""Storefront-ingest admission — the missing 10k WRITE-path primitive.

Born 2026-05-17 (jewel-structure J3-part-1). An independent capillary
Agent audit found the #1 write-path blast-radius gap: `/track` has NO
aggregate ingest admission. Its rate limit is per-IP (millions of
distinct end-user browser IPs at 10k merchants ⟹ does NOTHING to bound
aggregate write QPS). 10k active storefronts ≈ 10k–500k events/s into a
row-by-row INSERT path that shares the 80-conn PgBouncer pool with the
dashboard reads + 8 workers → an ingest storm SATURATES the pool and
CASCADES into every endpoint. The structural ceiling (~30k txn/s) is
below the arithmetic load.

This is the EXACT proven `dashboard._cold_build_admit` shape (Redis
ZSET global cap + per-WORKER BoundedSemaphore floor + degrade-open),
applied to the write hot path: it caps the number of *concurrent
expensive ingests* well below the pool so the write storm can never
exhaust it. Excess is SHED with a fast 429 — the storefront tracker is
fire-and-forget by design (best-effort analytics, not a transaction),
so a bounded fast shed under an extreme aggregate storm is graceful
degradation (the §0 "never problematic" property), NOT data-integrity
loss; the alternative is the pool-cascade that 500s the merchant's
dashboard + every other endpoint.

Scope honesty: this closes the CATASTROPHIC mode (pool-cascade) by
bounding concurrency. It does NOT yet let the system ABSORB the full
50k/s — that is the async batched-COPY writer (J3-part-2), a separate
sprint that needs the write-path rig as its proof gate. This primitive
is the prerequisite + the cascade-stopper, mirroring code already
proven in production (the dashboard 4th-tier admission).
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("ingest_admission")

# Concurrent expensive-ingest ceiling. MUST stay well below PgBouncer
# default_pool_size=80 because the write path SHARES that pool with
# dashboard reads + 8 workers — the ingest cap has to leave headroom,
# not consume the whole pool (that is exactly the cascade we prevent).
# 30 ≪ 80 → ~50 conns always free for everything else. Env kill/scale
# knob (§2 r11).
_INGEST_BUDGET = int(os.getenv("INGEST_ADMIT_BUDGET", "30"))
_INGEST_KEY = "hs:ingest:cb"
# > a single ingest's worst-case wall (visitor upsert + event INSERT +
# commit + ~6 Redis round-trips, ~tens of ms) by a wide margin so a
# crashed/hung worker's slot self-heals (purged each admit) instead of
# permanently shrinking the budget.
_INGEST_STALE_SEC = 10

# Per-WORKER floor so a Redis outage cannot degrade the cap to
# unbounded-OPEN (the honest-residual-#2 lesson, applied pre-emptively;
# 8 uvicorn workers × this ≈ the global budget; the Redis-DOWN
# structural floor — never serialises requests, acquire is always
# blocking=False so this is NOT a §12 request-path lock).
_INGEST_LOCAL_BUDGET = int(os.getenv(
    "INGEST_ADMIT_LOCAL_BUDGET", str(max(2, _INGEST_BUDGET // 8))))
# multi-worker: accept-degrade — per-process by design (audit checks
# the declaration line or the one directly above; keep this here).
_ingest_local_sem = threading.BoundedSemaphore(_INGEST_LOCAL_BUDGET)


def ingest_admit() -> str | None:
    """Return a release token if an ingest slot was taken, None if the
    budget is full (caller MUST shed with a fast 429 — NOT proceed to
    the expensive DB write). Mirrors dashboard._cold_build_admit:

    Redis UP → ZSET is the PRECISE global cap (crash-safe {token:
    epoch}; stale entries purged every call; the zcard→zadd race can
    let a few extra through under burst — tolerated: budget 30 ≪ pool
    80, a small overshoot never exhausts it).
    Redis DOWN/flaky → per-WORKER BoundedSemaphore floor: bounded,
    never the unbounded-open that re-creates the cascade."""
    try:
        import uuid
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            if _ingest_local_sem.acquire(blocking=False):
                record_silent_return(
                    "ingest.admit_redis_down_local_bounded")
                return "local"
            record_silent_return("ingest.admit_redis_down_local_full")
            return None
        now = time.time()
        rc.zremrangebyscore(_INGEST_KEY, 0, now - _INGEST_STALE_SEC)
        if rc.zcard(_INGEST_KEY) >= _INGEST_BUDGET:
            return None
        token = uuid.uuid4().hex
        rc.zadd(_INGEST_KEY, {token: now})
        rc.expire(_INGEST_KEY, _INGEST_STALE_SEC + 5)
        return token
    except Exception as exc:
        log.warning("ingest: admit failed: %s", exc)
        try:
            if _ingest_local_sem.acquire(blocking=False):
                return "local"
        except Exception as exc2:
            log.warning("ingest: local-sem acquire failed: %s", exc2)
        return None  # shed — bounded, never unbounded-open


def ingest_release(token: str | None) -> None:
    if not token:
        return
    if token == "local":
        try:
            _ingest_local_sem.release()
        except ValueError:
            pass  # BoundedSemaphore over-release guard — benign
        except Exception as exc:
            log.warning("ingest: local release failed: %s", exc)
        return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.zrem(_INGEST_KEY, token)
    except Exception as exc:
        log.warning("ingest: release failed: %s", exc)
