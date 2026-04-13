"""
night_shift_task.py — Worker task wrapper for the Night Shift Agent.

Extracted from aggregation_worker to make it isolable and testable
outside the monolith loop. The orchestrator imports `run` and calls
it once per cycle after checking `is_due`.
"""
from __future__ import annotations

import logging

log = logging.getLogger("night_shift_task")


def is_due() -> bool:
    """Gate — uses the same day-lock logic the agent exposes."""
    try:
        from app.services.night_shift_agent import should_run_nightly_now
        return should_run_nightly_now()
    except Exception as exc:
        log.warning("night_shift_task: is_due failed: %s", exc)
        return False


def run() -> int:
    """
    Run one nightly pass. Opens its own DB session (the monolith used
    to share `db` which made isolation hard). Returns the number of
    reports generated.
    """
    try:
        from app.core.database import SessionLocal
        from app.services.night_shift_agent import run_nightly_for_all_pro
    except Exception as exc:
        log.warning("night_shift_task: import failed: %s", exc)
        return 0

    db = SessionLocal()
    try:
        n = run_nightly_for_all_pro(db)
        if n > 0:
            log.info("night_shift_task: generated %d report(s)", n)
        return n
    except Exception as exc:
        log.warning("night_shift_task: run failed: %s", exc)
        return 0
    finally:
        try:
            db.close()
        except Exception:
            pass
