import logging
import sys
sys.path.append("/opt/wishspark/backend")

import time
from datetime import datetime, timezone

from app.core.env_bootstrap import load_env
load_env()

from app.core.sentry_init import init_sentry, cron_monitor
init_sentry(component="intelligence_worker")

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="intelligence_worker")

from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.models.visitor_product_state import VisitorProductState
from app.models.worker_log import WorkerLog
from app.models.worker_state import WorkerState
from app.services.opportunity_engine import update_product_opportunity

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKER_NAME = "intelligence_worker"
SLEEP_SECONDS = 600     # 10 minutes between cycles


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger("worker.intelligence")

def log(msg):
    _log.info(msg)


# ---------------------------------------------------------------------------
# WorkerState helpers
# ---------------------------------------------------------------------------

def _load_state(db) -> WorkerState:
    """
    Return the WorkerState row for this worker, creating it if absent.
    last_watermark is unused by this worker; last_run_at is the only
    field updated each cycle.
    """
    state = (
        db.query(WorkerState)
        .filter(WorkerState.worker_name == WORKER_NAME)
        .first()
    )
    if state is None:
        state = WorkerState(
            worker_name=WORKER_NAME,
            last_watermark=None,
            last_run_at=None,
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        log("created new worker_state row (first run)")
    return state


def _save_state(db, state: WorkerState) -> None:
    state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


# ---------------------------------------------------------------------------
# WorkerLog helper
# ---------------------------------------------------------------------------

def _write_log(
    db,
    started_at: datetime,
    shops_processed: int,
    rows_written: int,
    errors: int,
    error_detail: str | None,
) -> None:
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    entry = WorkerLog(
        worker_name=WORKER_NAME,
        started_at=started_at,
        finished_at=finished_at,
        shops_processed=shops_processed,
        rows_written=rows_written,
        errors=errors,
        error_detail=error_detail,
        duration_ms=duration_ms,
    )
    db.add(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

@cron_monitor(slug="intelligence_worker_cycle", interval_minutes=10, max_runtime_minutes=18)
def run_cycle():
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows_written = 0
    errors = 0
    last_error: str | None = None
    shops_seen: set[str] = set()

    log("starting intelligence cycle")
    db = SessionLocal()

    try:
        state = _load_state(db)

        # Enumerate distinct (shop_domain, product_url) pairs so that every
        # write is scoped to the correct tenant.  Querying product_url alone
        # (the previous behaviour) caused cross-tenant data contamination when
        # two shops had products at the same URL path.
        _PAIR_LIMIT = 5000  # 10k-merchant safe: cap per-cycle to prevent unbounded loops
        _TIME_BUDGET_SECONDS = 480  # 8 min budget inside a 10 min cycle
        pairs = (
            db.query(
                VisitorProductState.shop_domain,
                VisitorProductState.product_url,
            )
            .filter(
                VisitorProductState.shop_domain.isnot(None),
                VisitorProductState.product_url.isnot(None),
            )
            .distinct()
            .limit(_PAIR_LIMIT)
            .all()
        )

        log(f"found {len(pairs)} (shop_domain, product_url) pairs (limit {_PAIR_LIMIT})")

        import time as _time
        _cycle_start = _time.monotonic()
        for shop_domain, product_url in pairs:
            if _time.monotonic() - _cycle_start > _TIME_BUDGET_SECONDS:
                log(f"time budget exhausted ({_TIME_BUDGET_SECONDS}s) after {rows_written} rows — yielding")
                break
            try:
                update_product_opportunity(db, product_url, shop_domain)
                log(f"updated opportunity for {shop_domain} | {product_url}")
                rows_written += 1
                shops_seen.add(shop_domain)
            except Exception as e:
                errors += 1
                last_error = f"{shop_domain} | {product_url} | {e}"
                log(f"error on {shop_domain} | {product_url}: {e}")

        # ---------------------------------------------------------------
        # Klaviyo intent events — push fresh signals to connected merchants
        # ---------------------------------------------------------------
        for shop_domain in shops_seen:
            try:
                from app.services.klaviyo_export import push_intent_signals_to_klaviyo
                result = push_intent_signals_to_klaviyo(db=db, shop_domain=shop_domain)
                if result.get("pushed", 0) > 0:
                    log(f"klaviyo: pushed {result['pushed']} intent events for {shop_domain}")
                    db.commit()
            except Exception as exc:
                # best-effort: Klaviyo is an optional integration. If the
                # push fails for one shop, log and continue to the next.
                # The merchant's own pipeline state is unaffected.
                log(f"klaviyo: intent push failed (non-fatal) for {shop_domain}: {exc}")

        _save_state(db, state)
        log(
            f"cycle complete — shops={len(shops_seen)} "
            f"rows_written={rows_written} errors={errors}"
        )

    except Exception as exc:
        errors += 1
        last_error = str(exc)
        log(f"cycle-level error: {exc}")
        raise   # worker_log is written by finally, then main() re-raises for PM2

    finally:
        try:
            _write_log(
                db,
                started_at=started_at,
                shops_processed=len(shops_seen),
                rows_written=rows_written,
                errors=errors,
                error_detail=last_error,
            )
        except Exception as exc:
            log(f"worker_log write error (non-fatal): {exc}")
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log("worker started")

    while True:
        from app.core.distributed_lock import worker_lock
        from app.core.metrics import track_worker_cycle

        with worker_lock(WORKER_NAME, ttl_seconds=SLEEP_SECONDS + 60) as acquired:
            if not acquired:
                log("another instance holds the lock — skipping cycle")
            else:
                try:
                    with track_worker_cycle(WORKER_NAME):
                        run_cycle()
                except Exception as exc:
                    log(f"unhandled exception: {exc}")
                    raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
