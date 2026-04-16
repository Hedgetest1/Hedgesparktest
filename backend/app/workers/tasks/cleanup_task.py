"""
cleanup_task.py — Stale/stuck sweeps for action_tasks and bugfix candidates.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Two sweeps that
run every cycle:

  sweep_stale_tasks(db)         — release action_tasks stuck in 'executing'
  sweep_stuck_candidates(db)    — recover bugfix candidates stuck in 'applying'

Neither function takes a `log` callback — they use the module logger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.action_task import ActionTask
from app.services.action_executor import release_task

_log = logging.getLogger("worker.aggregation.cleanup")

_STALE_TASK_THRESHOLD_MINUTES = 10
_STUCK_CANDIDATE_THRESHOLD_MINUTES = 10


def sweep_stale_tasks(db: Session) -> int:
    """
    Release action_tasks stuck in status=executing beyond the stale threshold.

    Returns the number of tasks successfully released.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=_STALE_TASK_THRESHOLD_MINUTES)

    stale_tasks = (
        db.query(ActionTask)
        .filter(
            ActionTask.status == "executing",
            ActionTask.executed_at < cutoff,
        )
        .all()
    )

    if not stale_tasks:
        return 0

    released = 0
    for task in stale_tasks:
        previous_claimant = task.claimed_by or "unknown"
        age_minutes = (
            (datetime.now(timezone.utc).replace(tzinfo=None) - task.executed_at).total_seconds() / 60
            if task.executed_at else 0
        )
        try:
            _, conflict = release_task(
                db=db,
                task_id=task.id,
                shop_domain=task.shop_domain,
                reason="stale_task_sweep",
            )
            if conflict is None:
                _log.info(
                    "stale-task sweep: released task_id=%s shop=%s was_claimed_by=%s age=%.1fmin",
                    task.id, task.shop_domain, previous_claimant, age_minutes,
                )
                released += 1
            else:
                _log.info(
                    "stale-task sweep: task_id=%s skipped (conflict=%r, likely resolved concurrently)",
                    task.id, conflict,
                )
        except Exception as exc:
            _log.warning(
                "stale-task sweep: error releasing task_id=%s shop=%s: %s",
                task.id, task.shop_domain, exc,
            )

    return released


def sweep_stuck_candidates(db: Session) -> int:
    """
    Find bugfix_candidates stuck in 'applying' > threshold. Recover them
    to 'apply_failed' and release locks.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=_STUCK_CANDIDATE_THRESHOLD_MINUTES)

    from app.models.bugfix_candidate import BugFixCandidate
    stuck = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applying",
            BugFixCandidate.decided_at < cutoff,
        )
        .all()
    )

    if not stuck:
        return 0

    recovered = 0
    for c in stuck:
        c.status = "apply_failed"
        c.failure_reason = "stuck_in_applying: process crash or timeout — recovered by watchdog"
        recovered += 1

        try:
            from app.core.telegram_safety import release_execution_lock
            release_execution_lock("bugfix", str(c.id))
        except Exception as exc:
            _log.warning("cleanup: execution lock release failed for candidate #%s: %s", c.id, exc)

        try:
            from app.services.alerting import write_alert
            write_alert(
                db, severity="warning", source="watchdog",
                alert_type="stuck_candidate_recovered",
                summary=f"Bugfix #{c.id} stuck in 'applying' for >10min — recovered to 'apply_failed'",
                detail={"candidate_id": c.id, "title": c.title},
            )
        except Exception as exc:
            _log.warning("cleanup: alert write failed for candidate #%s: %s", c.id, exc)

        _log.info("stuck-candidate sweep: recovered #%s from 'applying' → 'apply_failed'", c.id)

    db.flush()
    return recovered
