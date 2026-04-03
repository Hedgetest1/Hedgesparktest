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
  DATABASE_URL                    — read by load_dotenv() from backend/.env
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

from dotenv import load_dotenv
load_dotenv()

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="nudge_optimization_worker")

from app.core.database import SessionLocal
from app.services.nudge_optimizer import run_optimization_cycle

log = logging.getLogger("worker.nudge_optimizer")

_INTERVAL_HOURS: float = float(os.getenv("NUDGE_OPTIMIZER_INTERVAL_HOURS", "6"))
_INTERVAL_SECS: float = _INTERVAL_HOURS * 3600


def _record_cycle(db, result: dict, duration_ms: int) -> None:
    """Write a WorkerLog row for audit trail."""
    try:
        from app.models.worker_log import WorkerLog
        row = WorkerLog(
            worker_name="nudge_optimization_worker",
            started_at=datetime.now(timezone.utc),
            shops_processed=result.get("shops_processed", 0),
            records_processed=result.get("nudges_evaluated", 0),
            duration_ms=duration_ms,
            status="ok" if result.get("errors", 0) == 0 else "partial",
            notes=(
                f"promoted={result.get('winners_promoted', 0)} "
                f"challengers={result.get('challengers_generated', 0)} "
                f"errors={result.get('errors', 0)}"
            ),
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        log.warning("nudge_optimization_worker: failed to write worker_log: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def _run_cycle() -> None:
    """Run one optimization cycle synchronously (wraps async)."""
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
