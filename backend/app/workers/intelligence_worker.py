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
# Round-robin keyset cursor over (shop_domain, product_url)
# ---------------------------------------------------------------------------
# Born 2026-05-17 (sibling of the aggregation_worker fix). The pairs query
# was `.distinct().limit(5000)` with NO ORDER BY (a nondeterministic
# 5000-row sample) and the loop had a 480s budget `break` with NO cursor
# — at 10k the same head re-ground every cycle, so the (shop,product) tail
# never got `update_product_opportunity` ⟹ stale intent/RARS for exactly
# the tail. Fix = the proven in-repo keyset pattern
# (find_active_products_batch): ORDER BY (shop,product) +
# WHERE (shop,product) > (:cs,:cp), advance to the LAST ACTUALLY-PROCESSED
# pair (budget-break-safe), wrap on a short final page. 24h TTL;
# redis-down → degrade-open (no cursor = process from head).

_INTEL_CURSOR_KEY = "hs:intel:cursor"


def _load_intel_cursor() -> tuple[str, str] | None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("intelligence_worker.load_cursor.redis_down")
            return None
        import json as _json
        v = rc.get(_INTEL_CURSOR_KEY)
        if not v:
            return None
        d = _json.loads(v)
        return (d["shop"], d["product"])
    except Exception as exc:
        _log.warning("intelligence_worker: _load_intel_cursor failed: %s", exc)
        return None


def _save_intel_cursor(pair: tuple[str, str] | None) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("intelligence_worker.save_cursor.redis_down")
            return
        if pair is None:
            rc.delete(_INTEL_CURSOR_KEY)  # keyset exhausted → wrap to head
            return
        import json as _json
        rc.set(
            _INTEL_CURSOR_KEY,
            _json.dumps({"shop": pair[0], "product": pair[1]}),
            ex=86400,
        )
    except Exception as exc:
        _log.warning("intelligence_worker: _save_intel_cursor failed: %s", exc)


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
        # Keyset round-robin: deterministic ORDER BY (the old query had
        # none → a nondeterministic 5000-row sample) + resume past the
        # last processed pair so the 10k tail is reached over bounded
        # cycles instead of never (the 480s `break` below otherwise
        # re-grinds the same head forever). Mirrors
        # find_active_products_batch.
        from sqlalchemy import tuple_ as _tuple
        _intel_cursor = _load_intel_cursor()
        _pairs_q = db.query(
            VisitorProductState.shop_domain,
            VisitorProductState.product_url,
        ).filter(
            VisitorProductState.shop_domain.isnot(None),
            VisitorProductState.product_url.isnot(None),
        )
        if _intel_cursor is not None:
            _pairs_q = _pairs_q.filter(
                _tuple(
                    VisitorProductState.shop_domain,
                    VisitorProductState.product_url,
                ) > _tuple(_intel_cursor[0], _intel_cursor[1])
            )
        pairs = (
            _pairs_q
            .distinct()
            .order_by(
                VisitorProductState.shop_domain,
                VisitorProductState.product_url,
            )
            .limit(_PAIR_LIMIT)
            .all()
        )

        log(
            f"found {len(pairs)} (shop_domain, product_url) pairs "
            f"(limit {_PAIR_LIMIT}, "
            f"cursor={'∅' if _intel_cursor is None else _intel_cursor})"
        )

        import time as _time
        from app.core.query_count_monitor import worker_scope as _worker_scope
        _cycle_start = _time.monotonic()
        _broke = False
        _last_pair: tuple[str, str] | None = None
        for shop_domain, product_url in pairs:
            if _time.monotonic() - _cycle_start > _TIME_BUDGET_SECONDS:
                log(f"time budget exhausted ({_TIME_BUDGET_SECONDS}s) after {rows_written} rows — yielding (cursor resumes past last pair)")
                _broke = True
                break
            # Consumed for THIS cycle before the work (success OR
            # caught-error) — an erroring pair must not block the cursor
            # forever; the cursor advances past it next cycle.
            _last_pair = (shop_domain, product_url)
            try:
                with _worker_scope("intelligence_worker.update_opportunity", shop_domain):
                    update_product_opportunity(db, product_url, shop_domain)
                log(f"updated opportunity for {shop_domain} | {product_url}")
                rows_written += 1
                shops_seen.add(shop_domain)
            except Exception as e:
                errors += 1
                last_error = f"{shop_domain} | {product_url} | {e}"
                log(f"error on {shop_domain} | {product_url}: {e}")

        # Advance the keyset cursor. Budget-broke OR a full page
        # (len == _PAIR_LIMIT) ⟹ more pairs may remain → resume past the
        # last processed pair. A short final page we fully consumed ⟹ the
        # keyset is exhausted → wrap to the head next cycle (None).
        # Nothing processed ⟹ leave the cursor untouched (don't lose
        # ground). update_product_opportunity is an idempotent upsert, so
        # a bounded wrap re-process is harmless.
        if not _broke and len(pairs) < _PAIR_LIMIT:
            _save_intel_cursor(None)
        elif _last_pair is not None:
            _save_intel_cursor(_last_pair)

        # ---------------------------------------------------------------
        # Klaviyo intent events — push fresh signals to connected merchants
        # ---------------------------------------------------------------
        for shop_domain in shops_seen:
            try:
                from app.services.klaviyo_export import push_intent_signals_to_klaviyo
                with _worker_scope("intelligence_worker.klaviyo_push", shop_domain):
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
