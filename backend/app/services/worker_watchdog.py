"""
worker_watchdog.py — Self-recovery for stale PM2 workers.

Problem: during the audit I found `segment_monitor_worker` stale for 6 days
and `nudge_optimization_worker` stale for 1.5 days. The system observes
its own worker health but doesn't act on it.

Solution: every agent_worker cycle, check worker_state table for any
worker whose last_run_at exceeds 2× its expected cadence. If stale,
invoke `pm2 restart {name}` to resuscitate it. Rate-limited via Redis
so a permanently-broken worker doesn't get restart-hammered.

CRITICAL: the watchdog runs INSIDE agent_worker. If it tries to restart
its own host (wishspark-agent-worker), `pm2 restart wishspark-agent-worker`
sends SIGTERM to the same process running this code — subprocess hangs,
then KeyboardInterrupt fires, crash. PM2 respawns agent_worker, which
runs the watchdog, which sees itself stale (last_run_at not yet
updated because the previous cycle was killed mid-run), tries restart
again → infinite suicide loop. Bug surfaced 2026-05-04 evening: 131
restart cycles before audit_worker_memory_growth caught the symptom
(99MB module-load baseline misclassified as leak).
Fix: SELF_HOST_PM2_NAME constant — watchdog refuses to restart itself.

Thresholds (2× the normal cadence from CLAUDE.md):
  wishspark-worker             : 10 min → 20 min stale
  wishspark-agent-worker       : 15 min → 30 min stale
  wishspark-aggregation-worker : 5  min → 10 min stale
  wishspark-segment-monitor    : 5  min → 10 min stale
  wishspark-nudge-optimizer    : 6h    → 12h  stale
  wishspark-gdpr-worker        : 5  min → 10 min stale

All restart attempts emit an ops_alert (deduped via write_alert) so
operators see a pattern of flapping and can investigate.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("worker_watchdog")

# Worker → (stale_after_minutes, pm2_process_name)
WORKER_THRESHOLDS: dict[str, tuple[int, str]] = {
    "intelligence_worker":       (20,  "wishspark-worker"),
    "agent_worker":              (30,  "wishspark-agent-worker"),
    "aggregation_worker":        (10,  "wishspark-aggregation-worker"),
    "segment_monitor_worker":    (10,  "wishspark-segment-monitor"),
    "nudge_optimization_worker": (720, "wishspark-nudge-optimizer"),
    "gdpr_worker":               (10,  "wishspark-gdpr-worker"),
}

_RESTART_COOLDOWN_S = 1800  # 30 min — don't hammer a broken worker

# Watchdog runs inside agent_worker. It cannot restart its own host
# without committing suicide (see module docstring). This constant
# documents the host so the host-skip rule is explicit.
SELF_HOST_PM2_NAME = "wishspark-agent-worker"


def _restart_cooldown_key(worker: str) -> str:
    return f"hs:watchdog:restart_cooldown:{worker}"


def _on_cooldown(worker: str) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("worker_watchdog.cooldown_read")
            return False
        return bool(rc.exists(_restart_cooldown_key(worker)))
    except Exception as exc:
        log.warning("worker_watchdog: _on_cooldown failed: %s", exc)
        return False


def _set_cooldown(worker: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("worker_watchdog.cooldown_write")
            return
        rc.setex(_restart_cooldown_key(worker), _RESTART_COOLDOWN_S, "1")
    except Exception as exc:
        log.warning("worker_watchdog: _set_cooldown failed: %s", exc)


def _pm2_restart(process_name: str) -> bool:
    """Invoke pm2 restart for a process. Returns True on apparent success."""
    try:
        result = subprocess.run(
            ["pm2", "restart", process_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("worker_watchdog: restarted %s", process_name)
            return True
        log.warning(
            "worker_watchdog: pm2 restart %s failed rc=%d: %s",
            process_name, result.returncode, (result.stderr or "")[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        log.warning("worker_watchdog: pm2 restart %s timed out", process_name)
        return False
    except FileNotFoundError:
        log.warning("worker_watchdog: pm2 binary not found — skip")
        return False
    except Exception as exc:
        log.warning("worker_watchdog: restart failed %s: %s", process_name, exc)
        return False


def run_watchdog(db: Session) -> dict:
    """Check all workers, restart any that are >2× stale."""
    report = {
        "checked": 0,
        "stale": 0,
        "restarted": 0,
        "on_cooldown": 0,
        "details": [],
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        rows = db.execute(
            sql_text(
                "SELECT worker_name, last_run_at FROM worker_state "
                "WHERE worker_name = ANY(:workers)"
            ),
            {"workers": list(WORKER_THRESHOLDS.keys())},
        ).fetchall()
    except Exception as exc:
        log.warning("worker_watchdog: worker_state query failed: %s", exc)
        return report

    state_by_worker = {r[0]: r[1] for r in rows}

    for worker, (threshold_min, pm2_name) in WORKER_THRESHOLDS.items():
        report["checked"] += 1

        # Suicide-prevention: skip self-restart. The watchdog runs inside
        # agent_worker; restarting the host would crash the watchdog mid-
        # subprocess.run() and trigger an infinite restart loop. An
        # external monitor would be needed to recover a stuck agent_worker.
        if pm2_name == SELF_HOST_PM2_NAME:
            continue

        last_run = state_by_worker.get(worker)
        if last_run is None:
            # Worker has no state yet — skip
            continue
        age_s = (now - last_run).total_seconds()
        threshold_s = threshold_min * 60
        if age_s < threshold_s:
            continue

        report["stale"] += 1
        detail = {
            "worker": worker,
            "pm2_name": pm2_name,
            "last_run": last_run.isoformat(),
            "age_minutes": round(age_s / 60, 1),
            "threshold_minutes": threshold_min,
            "restarted": False,
        }

        if _on_cooldown(worker):
            detail["action"] = "cooldown"
            report["on_cooldown"] += 1
            report["details"].append(detail)
            continue

        ok = _pm2_restart(pm2_name)
        _set_cooldown(worker)
        if ok:
            report["restarted"] += 1
            detail["restarted"] = True
            detail["action"] = "restart_ok"
        else:
            detail["action"] = "restart_failed"

        report["details"].append(detail)

        # Emit dedup'd ops_alert so operators see flapping patterns
        try:
            from app.services.alerting import write_alert
            # heal-detection: worker_auto_restarted is fired ONCE per successful pm2 restart action — the event IS the recovery, no recurring condition to close
            write_alert(
                db,
                severity="warning",
                source=f"worker_watchdog:{worker}",
                alert_type="worker_auto_restarted",
                summary=(
                    f"Watchdog restarted {worker} — stale for "
                    f"{detail['age_minutes']} min (threshold {threshold_min} min)"
                ),
                detail=detail,
            )
        except Exception as exc:
            log.warning("worker_watchdog: run_watchdog failed: %s", exc)

    return report
