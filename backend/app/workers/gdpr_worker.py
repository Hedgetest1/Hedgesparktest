"""
gdpr_worker.py — processes pending GDPR deletion/redaction requests.

Cycle: every 5 minutes, picks up pending GdprRequest rows and processes
them via gdpr_processor.  Follows the aggregation_worker pattern:
  - PM2-managed singleton process
  - Writes worker_state + worker_log each cycle
  - Crashes are auto-restarted by PM2
  - Idempotent: reprocessing a completed request is a no-op (status check)
"""
import logging
import sys
import time
from datetime import datetime, timezone

sys.path.append("/opt/wishspark/backend")

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="gdpr_worker")

from sqlalchemy.orm import sessionmaker
from app.core.database import engine
from app.models.gdpr_request import GdprRequest
from app.models.worker_log import WorkerLog
from app.models.worker_state import WorkerState
from app.services.gdpr_processor import process_gdpr_request

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

WORKER_NAME = "gdpr_worker"
SLEEP_SECONDS = 300       # 5 minutes between cycles
BATCH_SIZE = 10           # max requests per cycle


_log = logging.getLogger("worker.gdpr")

def log(msg: str) -> None:
    _log.info(msg)


def _load_state(db) -> WorkerState:
    state = db.query(WorkerState).filter(WorkerState.worker_name == WORKER_NAME).first()
    if state is None:
        state = WorkerState(worker_name=WORKER_NAME, last_run_at=None)
        db.add(state)
        db.commit()
        db.refresh(state)
        log("created new worker_state row (first run)")
    return state


def run_cycle() -> dict:
    """Process pending GDPR requests.  Returns cycle stats."""
    db = SessionLocal()
    stats = {"processed": 0, "errors": 0}
    try:
        # Pick up pending requests, oldest first
        pending = (
            db.query(GdprRequest)
            .filter(GdprRequest.status == "pending")
            .order_by(GdprRequest.created_at)
            .limit(BATCH_SIZE)
            .all()
        )

        if not pending:
            log("no pending GDPR requests")
        else:
            log(f"processing {len(pending)} GDPR request(s)")

        for req in pending:
            try:
                process_gdpr_request(db, req)
                stats["processed"] += 1
            except Exception as exc:
                stats["errors"] += 1
                log(f"ERROR processing request_id={req.id}: {exc}")
                # process_gdpr_request already handles status=failed internally
                # but catch any unexpected outer exception too

        # Update worker state
        state = _load_state(db)
        state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

    except Exception as exc:
        log(f"CYCLE ERROR: {exc}")
        db.rollback()
        stats["errors"] += 1
    finally:
        db.close()

    return stats


def main() -> None:
    log(f"starting — cycle every {SLEEP_SECONDS}s, batch size {BATCH_SIZE}")
    while True:
        t0 = time.monotonic()
        try:
            stats = run_cycle()
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Write worker log
            db = SessionLocal()
            try:
                db.add(WorkerLog(
                    worker_name=WORKER_NAME,
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    shops_processed=stats["processed"],
                    rows_written=stats["processed"],
                    errors=stats["errors"],
                    duration_ms=duration_ms,
                ))
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

            log(f"cycle complete — processed={stats['processed']} errors={stats['errors']} duration={duration_ms}ms")
        except Exception as exc:
            log(f"FATAL: {exc}")
            raise  # PM2 will restart

        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
