"""
dashboard_auto_remediation.py — Deterministic auto-remediation for
`dashboard_asset_drift` ops_alerts.

Context
-------
The `dashboard_asset_probe` task (app/workers/tasks/dashboard_asset_probe_task.py)
detects the "stale Next.js in-memory manifest" bug class: served HTML
references chunks that no longer exist on disk because a rebuild happened
mid-process-lifetime. The remedy is **always** the same deterministic
shell command:

    pm2 restart wishspark-dashboard --update-env

That is why `dashboard_asset_drift` is listed in
`bugfix_pipeline._PIPELINE_INTERNAL_ALERT_TYPES` — an LLM-authored code
patch here would be guessing at a non-code problem. Instead, this module
owns the remediation.

What this module does
---------------------
Every agent_worker cycle:
  1. Find the most recent unresolved `dashboard_asset_drift` alert that
     has NOT been auto-remediated yet (no matching audit_log row).
  2. Check hourly cooldown (max 3 restarts/hour — beyond that the root
     cause is not the in-memory manifest, escalate to humans).
  3. Run `pm2 restart wishspark-dashboard --update-env` with a 30s timeout.
  4. Wait for Next.js to warm up (~8s).
  5. Re-run the same asset probe used by the structural preflight audit.
  6. If green → resolve the original alert + write an info follow-up
     `dashboard_asset_drift_auto_remediated`.
     If still failing → write a critical follow-up
     `dashboard_asset_drift_auto_remediation_failed` (needs human eyes).
  7. Either way, log an audit_log row with the before/after probe state.

Scope locked (NEVER widen without §10 TIER_1 review):
  - Handles only `dashboard_asset_drift`. No other alert class, no
    generic "shell-remediation" framework. Targeted, one-bug remedy.
  - Never modifies code. Never writes outside ops_alerts + audit_log.
  - No LLM call — fully deterministic.

Kill switch: env DASHBOARD_AUTO_REMEDIATION_ENABLED=1 (default ON).
Flip to 0 during planned deploys if the restart would collide with
ongoing build work.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("dashboard_auto_remediation")

_ALERT_TYPE = "dashboard_asset_drift"
_FOLLOWUP_OK = "dashboard_asset_drift_auto_remediated"
_FOLLOWUP_FAIL = "dashboard_asset_drift_auto_remediation_failed"
_AUDIT_ACTION = "dashboard_auto_remediation"
_PM2_PROCESS = "wishspark-dashboard"

# Max restarts per UTC hour before escalating.
_RATE_LIMIT_PER_HOUR = 3

# Cooldown after each attempt — don't chain restarts back-to-back.
_ATTEMPT_COOLDOWN_S = 120

# Time to allow Next.js to come back up before re-probing.
_WARMUP_S = 8

# Restart command timeout.
_RESTART_TIMEOUT_S = 30


def is_enabled() -> bool:
    """Default ON — the remedy is a shell command, not an LLM call, so
    no budget gate applies. Operators can flip to 0 during debug/deploy."""
    return os.getenv("DASHBOARD_AUTO_REMEDIATION_ENABLED", "1") == "1"


def _rate_limit_key() -> str:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return f"hs:auto_remediation:dashboard_drift:count:{hour}"


def _cooldown_key() -> str:
    return "hs:auto_remediation:dashboard_drift:cooldown"


def _rate_limited() -> bool:
    """Return True if we've already hit the hourly ceiling OR the
    back-to-back cooldown is still warm."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("dashboard_auto_remediation.rate_limited_read")
            return False
        if rc.exists(_cooldown_key()):
            return True
        count = rc.get(_rate_limit_key())
        if count is None:
            return False
        try:
            return int(count) >= _RATE_LIMIT_PER_HOUR
        except (TypeError, ValueError):
            return False
    except Exception as exc:
        log.warning("dashboard_auto_remediation: rate limit read failed: %s", exc)
        return False


def _record_attempt() -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("dashboard_auto_remediation.rate_limit_write")
            return
        # Bump hourly counter with 1h TTL on first write (SET if not exists)
        pipe = rc.pipeline()
        pipe.incr(_rate_limit_key())
        pipe.expire(_rate_limit_key(), 3700)
        pipe.setex(_cooldown_key(), _ATTEMPT_COOLDOWN_S, "1")
        pipe.execute()
    except Exception as exc:
        log.warning("dashboard_auto_remediation: record_attempt failed: %s", exc)


def _find_target_alert(db: Session) -> dict | None:
    """Return the most recent unresolved `dashboard_asset_drift` alert
    (last 24h) that has NOT been auto-remediated yet — i.e., no
    audit_log row with action_type=dashboard_auto_remediation and
    target_id=<alert.id>."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    row = db.execute(
        sql_text(
            """
            SELECT a.id, a.created_at, a.severity, a.alert_type,
                   a.summary, a.detail
            FROM ops_alerts a
            WHERE a.alert_type = :kind
              AND a.resolved = false
              AND a.created_at >= :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM audit_log al
                  WHERE al.action_type = :action
                    AND al.target_id = a.id::text
              )
            ORDER BY a.created_at DESC
            LIMIT 1
            """
        ),
        {"kind": _ALERT_TYPE, "cutoff": cutoff, "action": _AUDIT_ACTION},
    ).mappings().first()
    return dict(row) if row else None


def _pm2_restart() -> tuple[bool, str]:
    """Invoke `pm2 restart wishspark-dashboard --update-env`. Return
    (success, stderr_tail)."""
    try:
        result = subprocess.run(
            ["pm2", "restart", _PM2_PROCESS, "--update-env"],
            capture_output=True,
            text=True,
            timeout=_RESTART_TIMEOUT_S,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or "")[:250]
    except subprocess.TimeoutExpired:
        return False, f"timeout after {_RESTART_TIMEOUT_S}s"
    except FileNotFoundError:
        return False, "pm2 binary not found"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:250]


def _probe_after_restart() -> list[str]:
    """Re-run the same probe logic the task uses. Returns the list of
    failure strings — empty means green."""
    import httpx
    from app.workers.tasks.dashboard_asset_probe_task import (
        _probe_host,
        _HOST,
        _PROBE_TIMEOUT_S,
    )
    try:
        with httpx.Client(follow_redirects=False) as client:
            # Reachability check first — dashboard may still be warming up
            try:
                r = client.get(f"{_HOST}/", timeout=_PROBE_TIMEOUT_S)
                if r.status_code >= 500:
                    return [f"/: post-restart HTTP {r.status_code}"]
            except Exception as exc:
                return [f"/: unreachable after restart ({type(exc).__name__})"]
            return _probe_host(client)
    except Exception as exc:
        log.warning("dashboard_auto_remediation: post-restart probe failed: %s", exc)
        return [f"probe harness error: {type(exc).__name__}"]


def attempt(db: Session) -> dict:
    """Execute one remediation attempt. Safe to call every cycle.

    Returns a telemetry dict:
        {
            "enabled": bool,
            "alert_id": int | None,
            "action": "skipped_disabled" | "skipped_no_alert" | "skipped_rate_limited"
                    | "remediated" | "escalated",
            "restart_ok": bool,
            "post_probe_failures": list[str] | None,
        }
    """
    report: dict = {
        "enabled": is_enabled(),
        "alert_id": None,
        "action": None,
        "restart_ok": False,
        "post_probe_failures": None,
    }

    if not is_enabled():
        report["action"] = "skipped_disabled"
        return report

    alert = _find_target_alert(db)
    if alert is None:
        report["action"] = "skipped_no_alert"
        return report

    report["alert_id"] = alert["id"]

    if _rate_limited():
        report["action"] = "skipped_rate_limited"
        # Escalate: if we keep being rate-limited on the same alert, the
        # remedy is clearly NOT working. Surface that specifically.
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="critical",
                source="dashboard_auto_remediation",
                alert_type=_FOLLOWUP_FAIL,
                summary=(
                    f"Auto-remediation rate-limited for dashboard_asset_drift "
                    f"alert {alert['id']} — restarts not clearing the drift, "
                    "human investigation needed"
                ),
                detail={
                    "origin_alert_id": alert["id"],
                    "reason": "rate_limited",
                    "rate_limit_per_hour": _RATE_LIMIT_PER_HOUR,
                },
            )
            db.commit()
        except Exception as exc:
            log.warning(
                "dashboard_auto_remediation: escalation write failed: %s", exc
            )
            try:
                db.rollback()
            except Exception:
                pass  # SILENT-EXCEPT-OK: rollback best-effort after primary write error already logged
        return report

    _record_attempt()

    restart_ok, restart_err = _pm2_restart()
    report["restart_ok"] = restart_ok

    if not restart_ok:
        report["action"] = "escalated"
        _write_outcome(
            db,
            alert=alert,
            success=False,
            post_failures=None,
            restart_error=restart_err,
        )
        return report

    # Let Next.js come back up before probing.
    time.sleep(_WARMUP_S)

    post_failures = _probe_after_restart()
    report["post_probe_failures"] = post_failures

    if not post_failures:
        report["action"] = "remediated"
        _write_outcome(
            db, alert=alert, success=True, post_failures=None, restart_error=""
        )
    else:
        report["action"] = "escalated"
        _write_outcome(
            db,
            alert=alert,
            success=False,
            post_failures=post_failures,
            restart_error="",
        )

    return report


def _write_outcome(
    db: Session,
    *,
    alert: dict,
    success: bool,
    post_failures: list[str] | None,
    restart_error: str,
) -> None:
    """Write follow-up alert + audit_log row. Commits on success path.
    On failure path, writes escalation alert so the follow-up is NOT
    rate-limited by the original alert's cooldown bucket."""
    from app.services.alerting import write_alert
    from app.services.audit import write_audit_log

    try:
        if success:
            # Mark origin alert resolved — the merchant-facing problem is fixed.
            from app.services.alerting import resolve_alert
            resolve_alert(db, alert["id"])

            write_alert(
                db,
                severity="info",
                source="dashboard_auto_remediation",
                alert_type=_FOLLOWUP_OK,
                summary=(
                    f"Auto-restarted {_PM2_PROCESS}; dashboard_asset_drift "
                    f"alert {alert['id']} resolved"
                ),
                detail={
                    "origin_alert_id": alert["id"],
                    "restart_command": f"pm2 restart {_PM2_PROCESS} --update-env",
                },
            )
            write_audit_log(
                db,
                actor_type="autonomous",
                actor_name="dashboard_auto_remediation",
                action_type=_AUDIT_ACTION,
                target_type="ops_alert",
                target_id=str(alert["id"]),
                status="completed",
                before_state={"alert_summary": alert.get("summary")},
                after_state={"origin_resolved": True},
            )
        else:
            reason = "pm2_restart_failed" if restart_error else "probe_still_failing"
            write_alert(
                db,
                severity="critical",
                source="dashboard_auto_remediation",
                alert_type=_FOLLOWUP_FAIL,
                summary=(
                    f"Auto-remediation of dashboard_asset_drift alert "
                    f"{alert['id']} FAILED ({reason}) — human action required"
                ),
                detail={
                    "origin_alert_id": alert["id"],
                    "reason": reason,
                    "restart_error": restart_error or None,
                    "post_restart_failures": (post_failures or [])[:20],
                },
            )
            write_audit_log(
                db,
                actor_type="autonomous",
                actor_name="dashboard_auto_remediation",
                action_type=_AUDIT_ACTION,
                target_type="ops_alert",
                target_id=str(alert["id"]),
                status="failed",
                before_state={"alert_summary": alert.get("summary")},
                after_state={
                    "reason": reason,
                    "restart_error": restart_error or None,
                    "post_restart_failures": (post_failures or [])[:20],
                },
            )
        db.commit()
    except Exception as exc:
        log.warning("dashboard_auto_remediation: outcome write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback best-effort after primary write error already logged
