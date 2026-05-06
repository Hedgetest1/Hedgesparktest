"""
watchdog_task.py — Worker watchdog: detect repeated errors across workers.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Reads the
worker_log table and raises a warning alert when any worker shows
>= N consecutive error cycles within the lookback window.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from itertools import groupby

from sqlalchemy import text

_log = logging.getLogger("worker.aggregation.watchdog")

_INTERVAL_S = 3_600  # 1 hour
_ERROR_THRESHOLD = 3
_WINDOW_HOURS = 2

_last_run: float | None = None


def should_run() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def run() -> None:
    from app.core.database import SessionLocal
    from app.services.alerting import write_alert

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=_WINDOW_HOURS)

        rows = db.execute(text("""
            SELECT worker_name, errors, started_at
            FROM worker_log
            WHERE started_at >= :cutoff
            ORDER BY worker_name, started_at DESC
        """), {"cutoff": cutoff}).fetchall()

        for worker_name, entries in groupby(rows, key=lambda r: r[0]):
            entry_list = list(entries)
            consecutive_errors = 0
            for entry in entry_list:
                if entry[1] and entry[1] > 0:
                    consecutive_errors += 1
                else:
                    break

            if consecutive_errors >= _ERROR_THRESHOLD:
                from app.models.ops_alert import OpsAlert
                existing = (
                    db.query(OpsAlert)
                    .filter(
                        OpsAlert.alert_type == "worker_repeated_failure",
                        OpsAlert.source == worker_name,
                        OpsAlert.resolved == False,  # noqa: E712
                    )
                    .first()
                )
                if existing:
                    continue

                # heal-detection: watchdog dispatch event log — fires when watchdog detects + restarts; mirrors worker_watchdog event-log semantics
                write_alert(
                    db,
                    severity="warning",
                    source=worker_name,
                    alert_type="worker_repeated_failure",
                    summary=f"{worker_name} has errored in {consecutive_errors} consecutive cycles",
                    detail={
                        "consecutive_errors": consecutive_errors,
                        "window_hours": _WINDOW_HOURS,
                        "recent_entries": len(entry_list),
                    },
                )
                db.commit()
                _log.info("watchdog: alert raised for %s (%d consecutive errors)",
                          worker_name, consecutive_errors)

    except Exception as exc:
        _log.warning("watchdog: error (non-fatal): %s", exc)
        db.rollback()
    finally:
        db.close()
