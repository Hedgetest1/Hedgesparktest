"""
app/core/rate_limit.py — In-process sliding-window rate limiter middleware.

Design
------
Zero external dependencies.  Uses a per-process in-memory dict keyed by
(ip, method, path).  Sliding-window algorithm: each request appends a
timestamp; entries older than the window are pruned on every hit.

This is appropriate for closed-beta scale (single backend process, limited
merchant traffic).  If the backend ever moves to multi-worker / cluster mode,
replace with a Redis-backed solution.

Rules registered in main.py
----------------------------
  POST /track                        60 req / 60 s per IP
  POST /nudge/event                  60 req / 60 s per IP
  POST /webhooks/shopify/orders-paid 20 req / 60 s per IP
  POST /pro/nudges                   10 req / 60 s per IP

Responses
---------
  429 Too Many Requests
  Retry-After: <window_seconds>
  Content-Type: application/json
  {"detail": "Too many requests — slow down"}
"""

import time
import threading
from collections import defaultdict
from typing import Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# Type alias: rules dict maps (METHOD, /path) → (max_requests, window_seconds)
RuleMap = Dict[Tuple[str, str], Tuple[int, int]]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter applied before routing.

    Thread-safe: a single lock protects _buckets.  Lock contention is
    negligible at closed-beta traffic levels.
    """

    def __init__(self, app, rules: RuleMap) -> None:
        super().__init__(app)
        self._rules: RuleMap = rules
        self._buckets: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        key = (request.method.upper(), request.url.path)
        rule = self._rules.get(key)

        if rule is None:
            return await call_next(request)

        max_requests, window = rule
        ip = request.client.host if request.client else "unknown"
        bucket_key = f"{ip}|{key[0]}|{key[1]}"
        now = time.monotonic()
        cutoff = now - window

        with self._lock:
            # Prune stale timestamps
            timestamps = self._buckets[bucket_key]
            # In-place prune — avoids reallocation on every hit
            idx = 0
            while idx < len(timestamps) and timestamps[idx] < cutoff:
                idx += 1
            if idx:
                del timestamps[:idx]

            if len(timestamps) >= max_requests:
                oldest = timestamps[0]
                retry_after = int(window - (now - oldest)) + 1
                return JSONResponse(
                    {"detail": "Too many requests — slow down"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)

        return await call_next(request)
