"""
invariant_monitor.py — Periodic post-merge invariant check.

Problem solved
--------------
Preflight audits (backend/scripts/audit_*.py) block commits at git
pre-commit hook time. But if someone bypasses the hook (--no-verify,
merge conflict resolution, emergency fix), a structural regression
can land in main without the hook firing. No runtime signal exists
for that class of regression because the invariants are LATENT — no
merchant triggers them today, so nothing writes to ops_alerts, so the
bugfix pipeline never sees the problem.

This module runs the critical audits on the live source tree on a
schedule (agent_worker cycle, every 15 min) and writes an ops_alert
when any audit fails. From there, bug_triage Rule 7 (generic
≥3-recurrence catch-all) creates a BugFixCandidate after 45 minutes
of the invariant being broken, and the normal self-healing flow
takes it from there.

Design constraints
------------------
- Read-only: this module MUST NOT attempt to fix anything. Only
  detect + alert. Fix proposals go through the standard bugfix
  pipeline (LLM propose → reviewer_layer → governed apply).
- Cheap: subprocess to existing audit scripts. No new LLM calls.
- Fail-safe: if the audit script itself errors, log but don't
  raise — never take down the worker loop.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
_PYTHON_BIN = str(_BACKEND_ROOT / "venv" / "bin" / "python")

# Registered audits to run on each cycle. Each entry is
# (script_name, alert_type_on_failure, source_key). source_key is the
# stable identifier in ops_alerts.source so dedup + thrash detection
# key off the same name across repeated failures.
_AUDITS: list[tuple[str, str, str]] = [
    (
        "audit_session_durability_invariants.py",
        "invariant_regression",
        "invariant:session_durability",
    ),
]

_TIMEOUT_SECONDS = 30


def run_invariant_check(db: Session) -> dict:
    """
    Run every registered audit once. Emit an ops_alert for each
    failure. Returns a summary dict for agent_worker logging.

    Never raises — a broken audit script writes a `critical` alert
    rather than crashing the worker loop.
    """
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    if not os.path.isdir(_SCRIPTS_DIR):
        log.warning("invariant_monitor: scripts dir missing at %s", _SCRIPTS_DIR)
        return summary
    if not os.path.isfile(_PYTHON_BIN):
        log.warning("invariant_monitor: venv python missing at %s", _PYTHON_BIN)
        return summary

    from app.services.alerting import write_alert

    for script_name, alert_type, source in _AUDITS:
        summary["checked"] += 1
        script_path = _SCRIPTS_DIR / script_name
        if not script_path.is_file():
            log.warning("invariant_monitor: audit script missing: %s", script_path)
            continue
        try:
            result = subprocess.run(
                [_PYTHON_BIN, str(script_path)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                cwd=str(_BACKEND_ROOT),
            )
        except subprocess.TimeoutExpired:
            # Audit itself hung — treat as critical so operators notice
            summary["failed"] += 1
            try:
                write_alert(
                    db,
                    severity="critical",
                    source=source,
                    alert_type="invariant_audit_timeout",
                    summary=f"{script_name} timed out after {_TIMEOUT_SECONDS}s",
                    detail={"script": script_name, "timeout": _TIMEOUT_SECONDS},
                )
                summary["alerts_written"] += 1
            except Exception as exc:
                log.error("invariant_monitor: failed to write timeout alert: %s", exc)
            continue
        except Exception as exc:
            log.error("invariant_monitor: subprocess failed for %s: %s", script_name, exc)
            continue

        if result.returncode == 0:
            # Audit green — no action. The chronic-aggregation logic in
            # write_alert handles the case where a previous failure has
            # now healed (alert stays open until resolved explicitly).
            continue

        summary["failed"] += 1
        # Trim audit output to a reasonable detail size
        stdout_tail = "\n".join(result.stdout.splitlines()[-40:])
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
        try:
            write_alert(
                db,
                severity="critical",
                source=source,
                alert_type=alert_type,
                summary=f"{script_name} failed — structural invariant broken on main",
                detail={
                    "script": script_name,
                    "exit_code": result.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "remediation": (
                        "Restore the invariant in source OR update the E2E "
                        "suite + audit to reflect the intentional design "
                        "change. See dashboard/e2e/session_durability.spec.ts "
                        "and backend/scripts/audit_session_durability_invariants.py."
                    ),
                },
            )
            summary["alerts_written"] += 1
        except Exception as exc:
            log.error("invariant_monitor: failed to write invariant_regression alert: %s", exc)

    return summary
