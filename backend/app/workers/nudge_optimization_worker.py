"""
nudge_optimization_worker.py — Scheduled A/B winner selection and challenger generation.

Cycle: every 6 hours (configurable via NUDGE_OPTIMIZER_INTERVAL_HOURS env var).

What this worker does
---------------------
  1. Calls nudge_optimizer.run_optimization_cycle() which:
       - Evaluates all active A/B nudges across all Pro shops
       - Promotes winners when MDE threshold is met
       - Generates challenger variants via AI composer
  2. Logs results to stdout (captured by PM2) and to the worker_log table.
  3. Sleeps until the next cycle.

This worker is a singleton.  Multiple instances would trigger duplicate
promotions and duplicate AI composer calls.  PM2 is configured with
instances: 1 and exec_mode: fork.

Environment variables
---------------------
  NUDGE_OPTIMIZER_INTERVAL_HOURS  — cycle interval (default: 6)
  DATABASE_URL                    — loaded from backend/.env via env_bootstrap
  OPENAI_API_KEY                  — required for challenger generation
  REDIS_URL                       — optional; used by per-shop budget guard

Logging
-------
  Each cycle logs:
    - shops processed
    - nudges evaluated
    - winners promoted
    - challengers generated
    - errors (per-nudge; non-fatal)

  Persistent cycle record written to worker_log table for audit trail.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Ensure the backend package root is on sys.path when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.env_bootstrap import load_env
load_env()

from app.core.sentry_init import init_sentry, cron_monitor
init_sentry(component="nudge_optimization_worker")

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="nudge_optimization_worker")

from app.core.database import SessionLocal
from app.services.nudge_optimizer import run_optimization_cycle

log = logging.getLogger("worker.nudge_optimizer")

_INTERVAL_HOURS: float = float(os.getenv("NUDGE_OPTIMIZER_INTERVAL_HOURS", "6"))
_INTERVAL_SECS: float = _INTERVAL_HOURS * 3600


def _record_cycle(db, result: dict, duration_ms: int) -> None:
    """Write a WorkerLog row for audit trail.

    WorkerLog columns: worker_name, started_at, finished_at, shops_processed,
    rows_written, errors, error_detail, duration_ms. No status/notes columns —
    details are embedded in error_detail when errors > 0.
    """
    try:
        from app.models.worker_log import WorkerLog
        now = datetime.now(timezone.utc)
        err_count = int(result.get("errors", 0))
        detail = None
        if err_count > 0 or result.get("winners_promoted") or result.get("challengers_generated"):
            detail = (
                f"evaluated={result.get('nudges_evaluated', 0)} "
                f"promoted={result.get('winners_promoted', 0)} "
                f"challengers={result.get('challengers_generated', 0)} "
                f"errors={err_count}"
            )
        row = WorkerLog(
            worker_name="nudge_optimization_worker",
            started_at=now,
            finished_at=now,
            shops_processed=int(result.get("shops_processed", 0)),
            rows_written=int(result.get("nudges_evaluated", 0)),
            errors=err_count,
            error_detail=detail,
            duration_ms=duration_ms,
        )
        db.add(row)
        # Update worker_state so /system/health reports this worker as running.
        from app.models.worker_state import WorkerState
        now_naive = now.replace(tzinfo=None)
        state = (
            db.query(WorkerState)
            .filter(WorkerState.worker_name == "nudge_optimization_worker")
            .first()
        )
        if state is None:
            state = WorkerState(worker_name="nudge_optimization_worker", last_run_at=now_naive)
            db.add(state)
        else:
            state.last_run_at = now_naive
        db.commit()
    except Exception as exc:
        log.warning("nudge_optimization_worker: failed to write worker_log: %s", exc)
        try:
            db.rollback()
        except Exception as exc:
            log.warning("nudge_optimization_worker: _record_cycle failed: %s", exc)


@cron_monitor(slug="nudge_optimization_worker_cycle", interval_minutes=360, max_runtime_minutes=60)
def _run_cycle() -> None:
    """Run one optimization cycle synchronously (wraps async)."""
    # SELF-PROTECTION: the optimizer generates LLM challenger variants when
    # winners are promoted. Under CRITICAL LLM pressure, skip the cycle
    # entirely — existing A/B tests keep running; challenger generation
    # resumes at the next cycle once pressure abates.
    from app.core.protection_state import protection_state
    ps = protection_state()
    if ps["level"] == "CRITICAL" or "skip_all_optional_llm_calls" in ps["protective_actions"]:
        log.info(
            "protection_state: %s — skipping nudge_optimization_worker cycle (optional LLM path)",
            ps["level"],
        )
        return
    db = SessionLocal()
    t0 = time.monotonic()
    try:
        result = asyncio.run(run_optimization_cycle(db))
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "nudge_optimization_worker: cycle complete — "
            "shops=%d nudges=%d promoted=%d challengers=%d errors=%d duration=%dms",
            result.get("shops_processed", 0),
            result.get("nudges_evaluated", 0),
            result.get("winners_promoted", 0),
            result.get("challengers_generated", 0),
            result.get("errors", 0),
            duration_ms,
        )
        _record_cycle(db, result, duration_ms)
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.error("nudge_optimization_worker: cycle failed: %s", exc, exc_info=True)
        _record_cycle(
            db,
            {"shops_processed": 0, "nudges_evaluated": 0, "winners_promoted": 0,
             "challengers_generated": 0, "errors": 1},
            duration_ms,
        )
    finally:
        db.close()


def main() -> None:
    log.info(
        "nudge_optimization_worker: starting — interval=%.1fh",
        _INTERVAL_HOURS,
    )
    while True:
        from app.core.distributed_lock import worker_lock
        from app.core.metrics import track_worker_cycle

        with worker_lock("nudge_optimization_worker", ttl_seconds=int(_INTERVAL_SECS) + 120) as acquired:
            if not acquired:
                log.info("nudge_optimization_worker: another instance holds the lock — skipping")
            else:
                with track_worker_cycle("nudge_optimization_worker"):
                    _run_cycle()
        log.info(
            "nudge_optimization_worker: sleeping %.1fh until next cycle",
            _INTERVAL_HOURS,
        )
        time.sleep(_INTERVAL_SECS)


if __name__ == "__main__":
    main()
