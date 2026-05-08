"""
cleanup_task.py — Stale-task sweep for action_tasks.

Extracted from aggregation_worker.py (Phase Ω⁶ split). One sweep that
runs every cycle:

  sweep_stale_tasks(db)         — release action_tasks stuck in 'executing'

The function uses the module logger (no `log` callback).

Removed 2026-05-08: sweep_stuck_candidates — Stage 2-E supersession
deleted bugfix_pipeline (the only producer of `applying` candidates).
With no writers, the watchdog had nothing to recover.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.action_task import ActionTask
from app.services.action_executor import release_task

_log = logging.getLogger("worker.aggregation.cleanup")

_STALE_TASK_THRESHOLD_MINUTES = 10


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


