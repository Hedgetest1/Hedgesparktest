"""
staged_rollout.py — Ring 0 → Ring 1 → Ring 2 → Ring 3 promotion primitive.

The production version of "canary → beta → all" built on top of the
feature flag primitive. Each flag declares a `max_ring` that pins how
far the rollout has progressed. To promote, you bump `max_ring`.

Ring assignment is deterministic per (flag, shop):
  Ring 0 — internal shops (HS_INTERNAL_SHOPS env or first allowlist entries)
  Ring 1 — canary 5% bucket
  Ring 2 — beta 5..25% bucket
  Ring 3 — general population

Safety hooks
------------
- `promote_if_healthy(flag)` — advances max_ring by 1 only if the SLO
  report for the covered routes is healthy. This is the kind of thing
  you hook into a promotion cron so bad rollouts get frozen automatically.

- `rollback(flag)` — drops max_ring to 0 and fires an alert.

Promotion dwell time
--------------------
Min dwell times per ring live here (not in the flag state) so promotion
cadence is not a Redis tweak — it's a code change the reviewer layer
sees. Override by env var if you need to move faster in an incident.
"""
from __future__ import annotations

import logging
import os
import time

from app.core.feature_flags import (
    REGISTRY,
    get_flag_state,
    is_enabled,
    ring_for_shop,
    set_flag,
)

log = logging.getLogger("staged_rollout")

# Minimum seconds at each ring before promote_if_healthy will advance.
# Overridable via HS_ROLLOUT_DWELL env for emergencies.
_DEFAULT_DWELL = {
    0: 6 * 3600,   # 6h in canary
    1: 24 * 3600,  # 24h in beta
    2: 48 * 3600,  # 48h in wider beta
}


def dwell_seconds(ring: int) -> int:
    override = os.environ.get(f"HS_ROLLOUT_DWELL_{ring}")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return _DEFAULT_DWELL.get(ring, 0)


def is_eligible_for_shop(flag: str, shop: str) -> bool:
    """True if the shop is within the currently-rolled-out ring."""
    state = get_flag_state(flag)
    if not state.get("enabled"):
        return False
    if state.get("killswitch"):
        return False
    max_ring = int(state.get("ring", 3))
    assigned = ring_for_shop(flag, shop)
    return assigned <= max_ring


def _slo_health_for_flag(flag: str) -> dict:
    """
    Look at the SLO report and return a health summary for the routes
    this flag is believed to influence. For now we look at the overall
    health (not route-specific) until each flag declares its affected
    routes — that's a refinement for v2.
    """
    try:
        from app.core.slo import slo_report
        report = slo_report()
    except Exception:
        return {"healthy": False, "reason": "slo_unavailable"}

    breaches = [s for s in report if s["health"] in ("breach", "critical_burn", "latency_breach")]
    warns = [s for s in report if "warning" in s["health"]]
    insufficient = [s for s in report if s["health"] == "insufficient_data"]

    if breaches:
        return {"healthy": False, "reason": f"{len(breaches)} breach(es)", "breaches": [b["name"] for b in breaches]}
    if warns:
        return {"healthy": False, "reason": f"{len(warns)} warning(s)", "warnings": [w["name"] for w in warns]}
    # Insufficient data is not a block on promotion — we don't pretend
    # to know what's unknowable. The caller can still gate on dwell time.
    return {"healthy": True, "insufficient_data": [s["name"] for s in insufficient]}


# Track ring transitions in Redis so dwell times are enforceable
_RING_HIST_KEY = "hs:rollout:ring_ts"


def _record_ring_change(flag: str, ring: int) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("staged_rollout.ring_write")
            return
        rc.hset(_RING_HIST_KEY, f"{flag}:{ring}", str(int(time.time())))
    except Exception:
        pass


def _ring_started_at(flag: str, ring: int) -> int | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("staged_rollout.ring_read")
            return None
        v = rc.hget(_RING_HIST_KEY, f"{flag}:{ring}")
        if not v:
            return None
        if isinstance(v, bytes):
            v = v.decode()
        return int(v)
    except Exception:
        return None


def promote_if_healthy(flag: str) -> dict:
    """
    Attempt to advance the flag by one ring. Returns a decision doc.

    Preconditions to promote:
      1. Dwell time at the current ring satisfied
      2. SLO health is not in breach
      3. Flag is registered and not killswitched
    """
    if flag not in REGISTRY:
        return {"flag": flag, "promoted": False, "reason": "not_registered"}

    state = get_flag_state(flag)
    if state.get("killswitch"):
        return {"flag": flag, "promoted": False, "reason": "killswitched"}

    current_ring = int(state.get("ring", 3))
    if current_ring >= 3:
        return {"flag": flag, "promoted": False, "reason": "already_at_max_ring", "current_ring": current_ring}

    # Dwell check
    started = _ring_started_at(flag, current_ring)
    required = dwell_seconds(current_ring)
    if started is not None and required > 0:
        elapsed = int(time.time()) - started
        if elapsed < required:
            return {
                "flag": flag,
                "promoted": False,
                "reason": "dwell_time_not_met",
                "current_ring": current_ring,
                "elapsed_seconds": elapsed,
                "required_seconds": required,
            }

    # SLO health check
    health = _slo_health_for_flag(flag)
    if not health["healthy"]:
        return {
            "flag": flag,
            "promoted": False,
            "reason": f"slo_unhealthy: {health.get('reason')}",
            "health": health,
            "current_ring": current_ring,
        }

    # Promote
    new_ring = current_ring + 1
    ok = set_flag(flag, ring=new_ring)
    if ok:
        _record_ring_change(flag, new_ring)
        log.info("staged_rollout: promoted %s from ring %d -> %d", flag, current_ring, new_ring)
    return {
        "flag": flag,
        "promoted": ok,
        "from_ring": current_ring,
        "to_ring": new_ring,
        "health": health,
    }


def rollback(flag: str, reason: str) -> dict:
    """Hard rollback a flag to ring 0 + raise an alert."""
    ok = set_flag(flag, ring=0)
    if ok:
        _record_ring_change(flag, 0)
    log.warning("staged_rollout: rollback %s to ring 0 — %s", flag, reason)
    try:
        from app.services.alerting import write_alert
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            write_alert(
                db,
                severity="warning",
                source="staged_rollout",
                alert_type="flag_rollback",
                summary=f"Flag {flag} rolled back to ring 0: {reason}",
                detail={"flag": flag, "reason": reason},
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        log.warning("rollback_flag: alert commit failed for %s: %s", flag, exc)
    return {"flag": flag, "rolled_back": ok, "reason": reason}
