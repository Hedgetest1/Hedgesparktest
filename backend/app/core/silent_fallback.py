"""
silent_fallback.py — observability for Redis-down / fallback fast paths.

Tier 2.1 of the top-1 hardening roadmap says: "every silent return must
be a deliberate silent return with an alert hook for the unexpected
case". The hundreds of `if rc is None: return ...` sites across the
services layer are the correct behaviour on paper — Redis is a
performance layer, not a data store — but if Redis quietly goes away in
prod, the whole app degrades into the slow path and nothing tells us.

This module is the observability plane over those fallbacks.

    from app.core.silent_fallback import record_silent_return

    def _circuit_open(webhook_id: str) -> bool:
        rc = _redis()
        if rc is None:
            record_silent_return("signal_webhooks.circuit")
            return False
        ...

What happens on each call
-------------------------
* `INCR hs:silent_fallback:{service}:{YYYY-MM-DD}` (TTL 7 days) — counts
  how many times this specific fast-path fired today.
* `INCR hs:silent_fallback:total:{YYYY-MM-DD}` (TTL 7 days) — global
  fallback pressure.
* The writes themselves use the same Redis client that's presumably
  unavailable, so they too fail-open. That's intentional: when Redis
  comes back we lose the offline counter window but regain visibility.
  Without this hook we had neither.

Read-back
---------
`GET /ops/silent-fallback/summary` (wired later) returns the top N
services by fallback count over the last 24h so we can tell immediately
when the fast path has become the slow path.

Zero dependencies
-----------------
This file must not import from services/workers/api — it's imported by
them. Circular imports have bitten us before; keep this module flat.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.redis_client import _client

log = logging.getLogger("silent_fallback")

_TTL_SECONDS = 7 * 24 * 3600


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_silent_return(service: str) -> None:
    """
    Bump the silent-fallback counter for *service*.

    Never raises. Safe to call from any code path, including ones that
    already know Redis is unavailable — the counter write will itself
    no-op and the caller's fast path proceeds.
    """
    rc = _client()
    if rc is None:
        return
    day = _today()
    try:
        pipe = rc.pipeline()
        pipe.incr(f"hs:silent_fallback:{service}:{day}")
        pipe.expire(f"hs:silent_fallback:{service}:{day}", _TTL_SECONDS)
        pipe.incr(f"hs:silent_fallback:total:{day}")
        pipe.expire(f"hs:silent_fallback:total:{day}", _TTL_SECONDS)
        pipe.execute()
    except Exception:
        # Never raise from the observability plane — if Redis is flaky
        # we lose the counter, not the request.
        pass


def read_summary(days: int = 1, top_n: int = 20) -> dict:
    """
    Summarise silent-fallback counters over the last *days* days.

    Returns::

        {
            "window_days": 1,
            "total": 123,
            "by_service": [("signal_webhooks.circuit", 80), ...],
            "available": True,
        }

    When Redis is unavailable returns ``{"available": False}``.
    """
    rc = _client()
    if rc is None:
        return {"available": False}
    now = datetime.now(timezone.utc)
    day_strs = [
        (now.replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")
    ]
    # Walk prior days too if requested.
    from datetime import timedelta as _td
    for i in range(1, max(1, days)):
        day_strs.append((now - _td(days=i)).strftime("%Y-%m-%d"))

    total = 0
    by_service: dict[str, int] = {}
    try:
        for day in day_strs:
            total_key = f"hs:silent_fallback:total:{day}"
            try:
                v = rc.get(total_key)
                if v is not None:
                    total += int(v)
            except Exception:
                pass
            cursor = 0
            pattern = f"hs:silent_fallback:*:{day}"
            # Bound SCAN so a misconfigured prod can't wedge this endpoint.
            seen = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match=pattern, count=200)
                for key in keys:
                    k = key.decode() if isinstance(key, bytes) else key
                    if k == total_key:
                        continue
                    # Strip prefix + suffix → service name.
                    svc = k[len("hs:silent_fallback:") : -(len(day) + 1)]
                    try:
                        n = int(rc.get(key) or 0)
                    except Exception:
                        n = 0
                    by_service[svc] = by_service.get(svc, 0) + n
                    seen += 1
                    if seen > 5000:
                        break
                if cursor == 0 or seen > 5000:
                    break
    except Exception as exc:
        log.warning("silent_fallback.read_summary failed: %s", exc)
        return {"available": False, "error": str(exc)}

    ranked = sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return {
        "available": True,
        "window_days": days,
        "total": total,
        "by_service": ranked,
    }
