"""
app/core/rate_limit.py — Sliding-window rate limiter middleware.

Uses Redis when available (correct across multiple uvicorn workers).
Falls back to in-process dict when Redis is unavailable (single-process only).

Rules are registered in main.py as:
  {("POST", "/track"): (600, 60), ...}

Returns 429 Too Many Requests with Retry-After header on breach.
"""

import logging
import time
import threading
from collections import defaultdict
from typing import Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

RuleMap = Dict[Tuple[str, str], Tuple[int, int]]


# Sentinel indicating Redis check was inconclusive (exception/unavailable).
# Caller MUST fall through to in-process fallback.
_REDIS_UNAVAILABLE = "redis_unavailable"


def _redis_check(bucket_key: str, max_requests: int, window: int) -> Tuple[bool | str, int]:
    """
    Check rate limit via Redis INCR + EXPIRE.
    Returns (allowed: bool | _REDIS_UNAVAILABLE, retry_after: int).

    On Redis errors, returns (_REDIS_UNAVAILABLE, 0) so the caller
    MUST fall through to the in-process fallback. Never fails open.
    """
    try:
        from app.core.redis_client import _client
        client = _client()
        if client is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("rate_limit.check")
            return _REDIS_UNAVAILABLE, 0

        key = f"hs:rl:{bucket_key}"
        pipe = client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.ttl(key)
        results = pipe.execute()

        count = results[0]
        ttl = results[1]

        # Set expiry on first request in window
        if count == 1 or ttl == -1:
            client.expire(key, window)
            ttl = window

        if count > max_requests:
            return False, max(1, ttl)

        return True, 0

    except Exception as exc:
        log.debug("rate_limit: Redis check failed, falling back to in-process: %s", exc)
        return _REDIS_UNAVAILABLE, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiter applied before routing.

    Primary: Redis-backed (correct across workers).
    Fallback: in-process dict (when Redis unavailable).
    Thread-safe: single lock protects in-process fallback.
    """

    def __init__(self, app, rules: RuleMap) -> None:
        super().__init__(app)
        self._rules: RuleMap = rules
        # In-process fallback
        self._buckets: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _check_in_process(self, bucket_key: str, max_requests: int, window: int) -> Tuple[bool, int]:
        """In-process sliding window fallback."""
        now = time.monotonic()
        cutoff = now - window

        with self._lock:
            timestamps = self._buckets[bucket_key]
            idx = 0
            while idx < len(timestamps) and timestamps[idx] < cutoff:
                idx += 1
            if idx:
                del timestamps[:idx]

            if len(timestamps) >= max_requests:
                oldest = timestamps[0]
                retry_after = int(window - (now - oldest)) + 1
                return False, retry_after

            timestamps.append(now)

        return True, 0

    async def dispatch(self, request: Request, call_next):
        key = (request.method.upper(), request.url.path)
        rule = self._rules.get(key)

        if rule is None:
            return await call_next(request)

        max_requests, window = rule
        ip = request.client.host if request.client else "unknown"
        bucket_key = f"{ip}|{key[0]}|{key[1]}"

        # Try Redis first
        redis_result, retry_after = _redis_check(bucket_key, max_requests, window)

        if redis_result is False:
            # Redis definitively said "rate exceeded"
            return JSONResponse(
                {"detail": "Too many requests — slow down"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        if redis_result is True:
            # Redis definitively said "allowed"
            return await call_next(request)

        # Redis unavailable — ALWAYS fall back to in-process limiter (never fail open)
        allowed, retry_after = self._check_in_process(bucket_key, max_requests, window)
        if not allowed:
            return JSONResponse(
                {"detail": "Too many requests — slow down"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
