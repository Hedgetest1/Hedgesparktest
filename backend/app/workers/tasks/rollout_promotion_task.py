"""
rollout_promotion_task.py — Auto-promote healthy flags through rings.

Walks the flag registry and calls `promote_if_healthy` on each. The
primitive handles dwell time + SLO gate, so this task is a thin
driver that runs every cycle and lets the promotion logic decide
when it's safe to advance.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("rollout_promotion_task")

_MIN_CYCLE_INTERVAL_SEC = 300  # don't re-evaluate more than every 5 min
_last_run_ts = 0.0


def is_due() -> bool:
    global _last_run_ts
    now = time.monotonic()
    if now - _last_run_ts < _MIN_CYCLE_INTERVAL_SEC:
        return False
    return True


def run() -> dict:
    global _last_run_ts
    _last_run_ts = time.monotonic()
    try:
        from app.core.feature_flags import REGISTRY
        from app.core.staged_rollout import promote_if_healthy
    except Exception as exc:
        log.warning("rollout_promotion: import failed: %s", exc)
        return {"error": str(exc)}

    results = []
    for name in REGISTRY.keys():
        try:
            res = promote_if_healthy(name)
            if res.get("promoted"):
                log.info("rollout_promotion: promoted %s %s",
                         name, f"{res.get('from_ring')} -> {res.get('to_ring')}")
            results.append(res)
        except Exception as exc:
            log.warning("rollout_promotion: %s failed: %s", name, exc)
            results.append({"flag": name, "error": str(exc)[:200]})
    return {"evaluated": len(results), "promoted": sum(1 for r in results if r.get("promoted"))}
