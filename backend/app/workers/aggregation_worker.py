"""
aggregation_worker.py — Phase 1 incremental metrics aggregation worker.

Responsibility
--------------
Reads raw events from the events table and writes pre-aggregated per-
(shop_domain, product_url) metrics into the product_metrics table.

Only products that have new events since the last watermark are
processed each cycle.  All metric windows (1h, 24h, 7d) are always
recomputed from the full window — the watermark only controls WHICH
products are touched, not which events are counted.

Cycle
-----
  1. Stale-task sweep — release executing action_tasks stuck > threshold.
  2. Load watermark from worker_state (0 on first run = process all).
  3. Find (shop_domain, product_url) pairs with events newer than watermark.
  4. For each pair: run one CTE query, upsert into product_metrics.
     Continue on per-product errors — never abort the cycle.
  5. Advance watermark to MAX(events.timestamp) over all product events.
  6. Clean up expired opportunity_signals (DELETE WHERE expires_at < now()).
  7. Write worker_log row.
  8. Sleep SLEEP_SECONDS (300 s / 5 min).

Retention (once per 24 h)
-------------------------
Deletes events older than RETENTION_DAYS (90) per shop.  Runs per-shop
so each DELETE uses the (shop_domain, timestamp DESC) index rather than
a full table scan.  Tracked via a module-level monotonic timestamp —
safe to re-run after a process restart (deletes are idempotent).

_last_retention_run is advanced only after conn.commit() succeeds.  If
the commit fails, the timer is NOT advanced and the next eligible cycle
will retry.  This prevents silently consuming the 24-hour window without
actually deleting anything.

Signal cleanup
--------------
Runs every cycle.  Deletes opportunity_signals rows WHERE expires_at < now().
This is cheap — the expires_at index makes it a fast range delete.
Cleanup is decoupled from signal detection: signals always expire on their
hard expires_at timestamp regardless of how often the detection engine runs.

Stale-task sweep
----------------
Runs every cycle.  Finds action_tasks where status=executing and
executed_at < now() - STALE_TASK_THRESHOLD_MINUTES.  Calls release_task()
for each stale task, which atomically resets it to pending and appends a
release note to result_detail.  Per-task errors are non-fatal.

At-least-once delivery guarantee
---------------------------------
The watermark is advanced only AFTER all active products have been
processed.  Two failure scenarios are handled intentionally:

  1. All active products fail (errors > 0, rows_written = 0):
     The watermark does NOT advance.  The next cycle re-queries the
     same window and re-attempts the same products.  Upserts are
     idempotent, so re-processing already-successful rows is safe.

  2. Metric writes succeed (rows_written > 0) but the watermark
     update itself fails:
     The watermark stays at its previous value.  The next cycle
     reprocesses the same window.  Again, idempotent upserts make
     this safe — rows are overwritten with equivalent values.

This means product_metrics rows may occasionally be recomputed and
rewritten without change.  That is acceptable and preferable to the
alternative of losing a cycle's work silently.
"""

import json as _json
import logging
log = logging.getLogger("aggregation_worker")
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="aggregation_worker")

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import engine
from app.models.action_task import ActionTask
from app.models.worker_log import WorkerLog
from app.models.worker_state import WorkerState
from app.services.action_executor import release_task
from app.services.nudge_engine import expire_stale_nudges
from app.services.opportunity_engine import SIGNAL_TTL_HOURS  # noqa: F401 — documents dependency

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKER_NAME = "aggregation_worker"
SLEEP_SECONDS = 300              # 5 minutes between cycles
RETENTION_DAYS = 90              # delete events older than this
NUDGE_EVENT_RETENTION_DAYS = 60  # delete nudge_events older than this
WORKER_LOG_RETENTION_DAYS = 30   # delete worker_log older than this
STALE_TASK_THRESHOLD_MINUTES = 10  # release executing tasks older than this


# CIG computation — once per 24h (weekly would be ideal but daily is cheap)
_CIG_INTERVAL_S = 86_400  # 24 hours
_last_cig_run: float | None = None


# ---------------------------------------------------------------------------
# Logging — structured JSON via app.core.logging_config
# ---------------------------------------------------------------------------

_log = logging.getLogger("worker.aggregation")

def log(msg: str) -> None:
    _log.info(msg)


# ---------------------------------------------------------------------------
# WorkerState helpers
# ---------------------------------------------------------------------------

def _load_state(db) -> WorkerState:
    """
    Return the WorkerState row for this worker, creating it if absent.
    last_watermark = 0 on first run so the first cycle processes all
    existing product events.
    """
    state = (
        db.query(WorkerState)
        .filter(WorkerState.worker_name == WORKER_NAME)
        .first()
    )
    if state is None:
        state = WorkerState(
            worker_name=WORKER_NAME,
            last_watermark=0,
            last_run_at=None,
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        log("created new worker_state row (first run)")
    return state


def _save_state(db, state: WorkerState, new_watermark: int) -> None:
    state.last_watermark = new_watermark
    state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


# ---------------------------------------------------------------------------
# WorkerLog helper
# ---------------------------------------------------------------------------

# Cycle-time regression guard. The 5-min SLEEP_SECONDS interval is the
# outer bound. A cycle that pushes 60s (20% of window) is very slow
# and warrants a look. A cycle that pushes 180s (60% of window) is
# one step from thrashing — two concurrent cycles would overlap.
# Thresholds below trigger a single alert per-hour via a Redis cooldown
# so we don't alert-storm on a slow-cycle streak.
_CYCLE_SLOW_WARN_MS = 60_000     # 60s — slow
_CYCLE_SLOW_CRIT_MS = 180_000    # 3min — nearly-thrashing
_CYCLE_ALERT_COOLDOWN_KEY = "hs:alert:agg_cycle_slow:{hour}"
_CYCLE_ALERT_COOLDOWN_TTL = 3600


def _maybe_alert_slow_cycle(db, duration_ms: int) -> None:
    """If the cycle took over the warn/crit threshold, emit a single
    `aggregation_cycle_slow` ops_alert. Cooldown 1/hour so a stretch of
    slow cycles doesn't create an alert storm. Never raises."""
    if duration_ms < _CYCLE_SLOW_WARN_MS:
        return
    try:
        from app.core.redis_client import _client
        rc = _client()
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        key = _CYCLE_ALERT_COOLDOWN_KEY.format(hour=hour)
        if rc is not None:
            acquired = rc.set(key, "1", nx=True, ex=_CYCLE_ALERT_COOLDOWN_TTL)
            if not acquired:
                return
        severity = "critical" if duration_ms >= _CYCLE_SLOW_CRIT_MS else "warning"
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity=severity,
            source="aggregation_worker",
            alert_type="aggregation_cycle_slow",
            summary=(
                f"aggregation_worker cycle took {duration_ms}ms "
                f"({duration_ms/1000:.1f}s) — threshold "
                f"{_CYCLE_SLOW_CRIT_MS if severity=='critical' else _CYCLE_SLOW_WARN_MS}ms"
            ),
            detail={
                "duration_ms": duration_ms,
                "warn_threshold_ms": _CYCLE_SLOW_WARN_MS,
                "crit_threshold_ms": _CYCLE_SLOW_CRIT_MS,
                "sleep_seconds": SLEEP_SECONDS,
            },
        )
    except Exception as exc:
        log(f"_maybe_alert_slow_cycle error (non-fatal): {exc}")


def _write_log(
    db,
    started_at: datetime,
    shops_processed: int,
    rows_written: int,
    errors: int,
    error_detail: str | None,
) -> None:
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    duration_ms = int(
        (finished_at - started_at).total_seconds() * 1000
    )
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
    # Regression guard — if this cycle was suspiciously slow, elevate
    # to an ops_alert (1/hour cooldown so we don't alert-storm on
    # a slow-cycle streak). Runs AFTER commit so the log row lands
    # even if the alert write fails.
    _maybe_alert_slow_cycle(db, duration_ms)


# ---------------------------------------------------------------------------
# Stale-task sweep (runs every cycle)
# ---------------------------------------------------------------------------
# Steps A-C — product metric computation + upsert
# Phase Ω⁶ split: functions live in workers/tasks/product_metrics_task.py.
# We re-export under the legacy `_`-prefixed names for orchestrator
# backward compatibility — zero behavior change.
# ---------------------------------------------------------------------------

from app.workers.tasks.product_metrics_task import (  # noqa: E402
    BATCH_SIZE,
    find_active_products as _find_active_products,
    find_active_products_batch as _find_active_products_batch,
    compute_metrics as _compute_metrics,
    compute_purchase_metrics as _compute_purchase_metrics,
    upsert_metrics as _upsert_metrics,
)

# Phase Ω⁶ split — retention / signal cleanup live in workers/tasks/retention_task.py
from app.workers.tasks import retention_task as _retention_task  # noqa: E402
_cleanup_expired_signals = _retention_task.cleanup_expired_signals
_should_run_retention = _retention_task.should_run_event_retention
_get_distinct_shops = _retention_task.get_distinct_shops
_run_retention = _retention_task.run_event_retention
_run_nudge_event_retention = _retention_task.run_nudge_event_retention
_run_worker_log_retention = _retention_task.run_worker_log_retention
_mark_retention_done = _retention_task.mark_retention_done

# Phase Ω⁶ split — webhook health in workers/tasks/webhook_health_task.py
from app.workers.tasks import webhook_health_task as _webhook_health_task  # noqa: E402
_should_run_webhook_check = _webhook_health_task.should_run
_run_webhook_health_check = _webhook_health_task.run

# Phase Ω⁶ split — watchdog / data_integrity / cleanup tasks
from app.workers.tasks import watchdog_task as _watchdog_task  # noqa: E402
from app.workers.tasks import data_integrity_task as _data_integrity_task  # noqa: E402
from app.workers.tasks import cleanup_task as _cleanup_task_module  # noqa: E402
_should_run_watchdog = _watchdog_task.should_run
_run_worker_watchdog = _watchdog_task.run
_should_run_data_integrity_probe = _data_integrity_task.should_run
_run_data_integrity_probe = _data_integrity_task.run
_sweep_stale_tasks = _cleanup_task_module.sweep_stale_tasks
_sweep_stuck_candidates = _cleanup_task_module.sweep_stuck_candidates

# Phase Ω⁶ split — nudge compose in workers/tasks/nudge_compose_task.py
from app.workers.tasks import nudge_compose_task as _nudge_compose_task  # noqa: E402
_run_ai_nudge_compose = _nudge_compose_task.run


# ---------------------------------------------------------------------------
# Step D — advance the watermark
# ---------------------------------------------------------------------------

def _read_new_watermark(conn) -> int:
    """
    Return MAX(timestamp) over all product events in the events table.
    Uses product_url IS NOT NULL — consistent with _find_active_products.
    Returns 0 if no product events exist.
    """
    result = conn.execute(
        text("""
            SELECT COALESCE(MAX(timestamp), 0) AS max_ts
            FROM events
            WHERE product_url IS NOT NULL
        """)
    )
    row = result.fetchone()
    return int(row.max_ts) if row and row.max_ts is not None else 0


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step G — compute and upsert store_metrics (per shop, every cycle)
# Phase Ω⁶ split: body lives in workers/tasks/store_metrics_task.py.
# ---------------------------------------------------------------------------

from app.workers.tasks.store_metrics_task import (  # noqa: E402
    compute_store_metrics as _compute_store_metrics,
    upsert_store_metrics as _upsert_store_metrics,
)


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle() -> None:

    from app.core.metrics import track_worker_cycle
    with track_worker_cycle(WORKER_NAME):
        _run_cycle_inner()


def _run_cycle_inner() -> None:

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    shops_processed_set: set[str] = set()
    rows_written = 0
    errors = 0
    last_error: str | None = None

    db = SessionLocal()
    try:
        # ------------------------------------------------------------------ #
        # Stale-task sweep — runs every cycle, non-fatal                      #
        # ------------------------------------------------------------------ #
        try:
            released = _sweep_stale_tasks(db)
            if released > 0:
                log(f"stale-task sweep: released {released} stale tasks")
        except Exception as exc:
            log(f"stale-task sweep error (non-fatal): {exc}")

        # Stuck candidate sweep — recover candidates stuck in 'applying'
        try:
            recovered = _sweep_stuck_candidates(db)
            if recovered > 0:
                db.commit()
                log(f"stuck-candidate sweep: recovered {recovered} candidates")
        except Exception as exc:
            db.rollback()
            log(f"stuck-candidate sweep error (non-fatal): {exc}")

        # AI nudge compose upgrade — merchants' Pro nudges are created with
        # baseline variants synchronously (request never blocks on OpenAI).
        # Here we upgrade them with AI-composed variants in the background,
        # bounded to 5 per cycle to protect the LLM budget.
        try:
            upgraded = _run_ai_nudge_compose(db)
            if upgraded > 0:
                db.commit()
                log(f"ai_nudge_compose: upgraded {upgraded} nudge(s)")
        except Exception as exc:
            db.rollback()
            log(f"ai_nudge_compose error (non-fatal): {exc}")

        # ------------------------------------------------------------------ #
        # Nudge expiry sweep — mark expired active_nudges as 'expired'        #
        # ------------------------------------------------------------------ #
        try:
            nudges_expired = expire_stale_nudges(db)
            if nudges_expired > 0:
                log(f"nudge expiry: marked {nudges_expired} nudges as expired")
        except Exception as exc:
            log(f"nudge expiry error (non-fatal): {exc}")

        # ------------------------------------------------------------------ #
        # Observability spikes — elevate high-volume low-severity signals     #
        # (tracker runtime errors, dashboard frontend errors, p95 drift)      #
        # into single ops_alert events the bugfix_pipeline can triage.        #
        # Each detector is independently try/except'd inside the service.     #
        # ------------------------------------------------------------------ #
        try:
            from app.services.observability_spikes import run_all_spike_detectors
            spike_summary = run_all_spike_detectors(db)
            total_spikes = sum(spike_summary.values())
            if total_spikes > 0:
                db.commit()
                log(f"observability_spikes: {spike_summary}")
        except Exception as exc:
            db.rollback()
            log(f"observability_spikes error (non-fatal): {exc}")

        # ------------------------------------------------------------------ #
        # Lighthouse nightly — once-per-day guard inside the service.         #
        # Runs only in the 02:00-04:00 UTC window (low dashboard traffic)     #
        # and skips if already ran today. Returns early on any other cycle,  #
        # so this adds ~0ms when out-of-window.                               #
        # ------------------------------------------------------------------ #
        try:
            from app.services.lighthouse_monitor import run_nightly_check
            lh_result = run_nightly_check(db)
            if lh_result.get("ran"):
                db.commit()
                log(f"lighthouse_monitor: {lh_result}")
        except Exception as exc:
            db.rollback()
            log(f"lighthouse_monitor error (non-fatal): {exc}")

        # ------------------------------------------------------------------ #
        # LLM guardrail benchmark — once-per-week Sunday 04:00-06:00 UTC.     #
        # Runs the structural test_llm_propose_bench.py suite via subprocess #
        # pytest (fake LLM stubs — zero API cost, ~4s). Alerts on regression.#
        # ------------------------------------------------------------------ #
        try:
            from app.services.llm_benchmark_monitor import run_weekly_check
            llm_result = run_weekly_check(db)
            if llm_result.get("ran"):
                db.commit()
                log(f"llm_benchmark_monitor: {llm_result}")
        except Exception as exc:
            db.rollback()
            log(f"llm_benchmark_monitor error (non-fatal): {exc}")

        state = _load_state(db)
        last_watermark = state.last_watermark or 0
        log(f"last_watermark={last_watermark}")

        with engine.connect() as conn:

            # ---------------------------------------------------------- #
            # Nudge impression daily cleanup (30-day retention)            #
            # ---------------------------------------------------------- #
            try:
                nid_result = conn.execute(
                    text("DELETE FROM nudge_impression_daily WHERE impression_date < CURRENT_DATE - INTERVAL '30 days'")
                )
                conn.commit()
                if nid_result.rowcount > 0:
                    log(f"nudge_impression_daily cleanup: deleted {nid_result.rowcount} old rows")
            except Exception as exc:
                conn.rollback()
                log(f"nudge_impression_daily cleanup error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Signal cleanup — runs every cycle, cheap range delete        #
            # ---------------------------------------------------------- #
            try:
                signals_deleted = _cleanup_expired_signals(conn)
                conn.commit()
                if signals_deleted > 0:
                    log(f"signal cleanup: deleted {signals_deleted} expired rows")
            except Exception as exc:
                conn.rollback()
                log(f"signal cleanup error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Event retention (once per 24 h)                             #
            # ---------------------------------------------------------- #
            if _should_run_retention():
                try:
                    deleted = _run_retention(conn, now_ms)
                    nudge_deleted = _run_nudge_event_retention(conn)
                    wlog_deleted = _run_worker_log_retention(conn)
                    conn.commit()
                    _mark_retention_done()
                    log(f"retention: deleted {deleted} events (>{RETENTION_DAYS}d), "
                        f"{nudge_deleted} nudge_events (>{NUDGE_EVENT_RETENTION_DAYS}d), "
                        f"{wlog_deleted} worker_log (>{WORKER_LOG_RETENTION_DAYS}d)")
                except Exception as exc:
                    conn.rollback()
                    log(f"retention error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Webhook health check (once per 24 h)                         #
            # ---------------------------------------------------------- #
            if _should_run_webhook_check():
                try:
                    _run_webhook_health_check()
                    _webhook_health_task.mark_done()
                except Exception as exc:
                    log(f"webhook health check error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Worker watchdog (once per hour)                               #
            # ---------------------------------------------------------- #
            if _should_run_watchdog():
                try:
                    _run_worker_watchdog()
                    _watchdog_task.mark_done()
                except Exception as exc:
                    log(f"watchdog error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Data integrity probe (once per 6 h) — semantic drift       #
            # ---------------------------------------------------------- #
            if _should_run_data_integrity_probe():
                try:
                    _run_data_integrity_probe()
                    _data_integrity_task.mark_done()
                except Exception as exc:
                    log(f"data_integrity_probe error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Find active products — BATCHED with cursor pagination        #
            # ---------------------------------------------------------- #
            # Load cursor from Redis (survives PM2 restarts within cycle)
            from app.core.redis_client import cache_get as _cget, cache_set as _cset
            _cursor_key = "hs:agg_cursor"
            _cursor = _cget(_cursor_key)  # {"shop": str, "product": str} or None

            cursor_shop = _cursor["shop"] if _cursor else None
            cursor_product = _cursor["product"] if _cursor else None

            active_products = _find_active_products_batch(
                conn, last_watermark,
                cursor_shop=cursor_shop,
                cursor_product=cursor_product,
                batch_size=BATCH_SIZE,
            )
            batch_label = f"batch({BATCH_SIZE})"
            if _cursor:
                batch_label += f" cursor=({cursor_shop[:20]}…,{cursor_product[:20]}…)"
            log(f"active products {batch_label}: {len(active_products)}")

            if not active_products:
                # No more products in this sweep — reset cursor for next sweep
                if _cursor is not None:
                    _cset(_cursor_key, None, 1)  # delete cursor
                    log("batch sweep complete — cursor reset for next full pass")
                else:
                    log("no new product events — skipping metric computation")
            else:
                for shop_domain, product_url in active_products:
                    try:
                        metrics = _compute_metrics(
                            conn, shop_domain, product_url, now_ms
                        )
                        _upsert_metrics(conn, metrics)
                        conn.commit()
                        shops_processed_set.add(shop_domain)
                        rows_written += 1

                    except Exception as exc:
                        conn.rollback()
                        errors += 1
                        last_error = f"{shop_domain} | {product_url} | {exc}"
                        log(f"error processing {shop_domain} / {product_url}: {exc}")

                # Save cursor for next cycle (last processed product)
                last_shop, last_product = active_products[-1]
                _cset(_cursor_key, {"shop": last_shop, "product": last_product}, 3600)

                # Advance watermark only when full sweep completes
                # (batch returned fewer than BATCH_SIZE = end of sweep)
                if len(active_products) < BATCH_SIZE and rows_written > 0:
                    try:
                        new_watermark = _read_new_watermark(conn)
                        _save_state(db, state, new_watermark)
                        _cset(_cursor_key, None, 1)  # reset cursor
                        log(f"watermark advanced: {last_watermark} → {new_watermark} (sweep complete)")
                    except Exception as exc:
                        log(f"watermark update error (non-fatal, will retry next cycle): {exc}")
                elif rows_written > 0:
                    log(f"batch processed {rows_written}/{len(active_products)} — continuing next cycle")

            # ---------------------------------------------------------------
            # Event retention cleanup — GDPR / privacy hygiene
            #
            # Delete events older than 180 days.  Runs once per cycle but
            # the DELETE is bounded (LIMIT 5000) to prevent long-running
            # transactions.  Repeated cycles will drain the backlog.
            # ---------------------------------------------------------------
            try:
                cutoff_ms = int(
                    (datetime.now(timezone.utc) - timedelta(days=180)).timestamp() * 1000
                )
                result = conn.execute(text("""
                    DELETE FROM events
                    WHERE id IN (
                        SELECT id FROM events
                        WHERE timestamp < :cutoff AND timestamp IS NOT NULL
                        LIMIT 5000
                    )
                """), {"cutoff": cutoff_ms})
                purged = result.rowcount
                if purged > 0:
                    conn.commit()
                    log(f"retention: purged {purged} events older than 180 days")
            except Exception as exc:
                log(f"retention: cleanup error (non-fatal): {exc}")
                try:
                    conn.rollback()
                except Exception as exc:
                    log.warning("aggregation_worker: _run_cycle_inner failed: %s", exc)

            # ---------------------------------------------------------------
            # Closed-loop proof — compute pending action deltas
            # Runs every cycle; finds snapshots past their compare_after date
            # and computes before/after metrics.
            # ---------------------------------------------------------------
            try:
                from app.services.action_proof import compute_pending_deltas
                computed = compute_pending_deltas(db)
                if computed > 0:
                    log(f"proof: computed {computed} action delta(s)")
            except Exception as exc:
                log(f"proof: delta computation error (non-fatal): {exc}")

            # ---------------------------------------------------------------
            # Store-level intelligence — precompute per-shop store_metrics
            # Runs every cycle for shops that had product updates.
            # Non-fatal: errors don't block the cycle.
            # ---------------------------------------------------------------
            try:
                # Process all shops that had activity this cycle, plus any
                # shop with existing product_metrics (ensures store_metrics
                # stays fresh even during quiet periods).
                all_shops = shops_processed_set.copy()
                if not all_shops:
                    # No active products this cycle — still refresh existing shops
                    try:
                        shop_rows = conn.execute(
                            text("SELECT shop_domain FROM merchants WHERE install_status = 'active'")
                        ).fetchall()
                        all_shops = {r[0] for r in shop_rows}
                    except Exception as exc:
                        log.warning("aggregation_worker: _run_cycle_inner failed: %s", exc)

                from app.services.execution_engine import (
                    process_execution_opportunities,
                    _update_tracking_outcomes,
                    compute_post_execution_deltas,
                    detect_holdout_leakage,
                )

                store_count = 0
                exec_count = 0
                _agg_budget_seconds = 240  # 4 min budget inside a 5 min cycle
                _agg_start = time.monotonic()
                for shop in all_shops:
                    if time.monotonic() - _agg_start > _agg_budget_seconds:
                        log(f"store_metrics: time budget exhausted ({_agg_budget_seconds}s) after {store_count} shops — yielding")
                        break
                    try:
                        sm = _compute_store_metrics(conn, shop)
                        _upsert_store_metrics(conn, sm)
                        conn.commit()

                        # Generate/upsert execution opportunities + audiences
                        try:
                            n = process_execution_opportunities(
                                conn, shop, sm["co_viewed_pairs"]
                            )
                            conn.commit()
                            exec_count += n
                        except Exception as exc_e:
                            conn.rollback()
                            log(f"execution_engine error for {shop} (non-fatal): {exc_e}")

                        # Update outcome tracking for existing audiences
                        try:
                            _update_tracking_outcomes(conn, shop)
                            conn.commit()
                        except Exception as exc_t:
                            conn.rollback()
                            log(f"execution tracking error for {shop} (non-fatal): {exc_t}")

                        # Detect holdout leakage (before computing deltas)
                        try:
                            leaked = detect_holdout_leakage(conn, shop)
                            conn.commit()
                            if leaked > 0:
                                log(f"holdout leakage: flagged {leaked} rows for {shop}")
                        except Exception as exc_l:
                            conn.rollback()
                            log(f"leakage detection error for {shop} (non-fatal): {exc_l}")

                        # Compute post-execution deltas for confirmed executions
                        try:
                            deltas = compute_post_execution_deltas(conn, shop)
                            conn.commit()
                            if deltas > 0:
                                log(f"execution deltas: computed {deltas} for {shop}")
                        except Exception as exc_d:
                            conn.rollback()
                            log(f"execution delta error for {shop} (non-fatal): {exc_d}")

                        # Compute Store Intelligence Profile (SIP)
                        try:
                            from app.services.sip_engine import compute_sip, upsert_sip, maybe_snapshot
                            sip_data = compute_sip(conn, shop)
                            if sip_data:
                                upsert_sip(conn, sip_data)
                                maybe_snapshot(conn, sip_data)
                                conn.commit()
                        except Exception as exc_sip:
                            conn.rollback()
                            log(f"SIP error for {shop} (non-fatal): {exc_sip}")

                        # Autonomous Revenue Loop (Pro merchants only)
                        try:
                            from app.services.autonomous_loop import run_autonomous_cycle
                            db_session = SessionLocal()
                            try:
                                # Gate: only run for Pro merchants with billing active
                                from app.models.merchant import Merchant
                                merchant = db_session.query(Merchant).filter(
                                    Merchant.shop_domain == shop,
                                    Merchant.plan == "pro",
                                    Merchant.billing_active == True,  # noqa: E712
                                    Merchant.install_status == "active",
                                ).first()
                                if merchant:
                                    auto_count = run_autonomous_cycle(db_session, shop)
                                    if auto_count > 0:
                                        log(f"autonomous_loop: {auto_count} action(s) for {shop}")
                            finally:
                                db_session.close()
                        except Exception as exc_auto:
                            log(f"autonomous_loop error for {shop} (non-fatal): {exc_auto}")

                        store_count += 1
                    except Exception as exc:
                        conn.rollback()
                        log(f"store_metrics error for {shop} (non-fatal): {exc}")

                if store_count > 0:
                    log(f"store_metrics: updated {store_count} shop(s), {exec_count} opportunities")
            except Exception as exc:
                log(f"store_metrics: top-level error (non-fatal): {exc}")

        # Phase Ω⁶ — extracted task modules (night_shift, rollout_promotion)
        try:
            from app.workers.tasks import night_shift_task
            if night_shift_task.is_due():
                night_shift_task.run()
        except Exception as exc:
            log(f"night_shift_task error (non-fatal): {exc}")

        try:
            from app.workers.tasks import rollout_promotion_task
            if rollout_promotion_task.is_due():
                res = rollout_promotion_task.run()
                if res.get("promoted", 0) > 0:
                    log(f"rollout_promotion: promoted {res['promoted']} flag(s)")
        except Exception as exc:
            log(f"rollout_promotion_task error (non-fatal): {exc}")

        # Commerce Intelligence Graph — cross-store aggregation (daily)
        global _last_cig_run
        try:
            _now_mono = time.monotonic()
            if _last_cig_run is None or (_now_mono - _last_cig_run) > _CIG_INTERVAL_S:
                from app.services.cig_engine import compute_cig
                with engine.connect() as cig_conn:
                    n_cohorts = compute_cig(cig_conn)
                    if n_cohorts > 0:
                        log(f"CIG: computed {n_cohorts} cohorts")
                _last_cig_run = _now_mono
        except Exception as exc:
            log(f"CIG error (non-fatal): {exc}")

        # Pre-compute proactive chat messages + intelligence briefs per shop (cached in Redis)
        try:
            from app.services.proactive_chat import precompute_proactive_messages
            from app.services.store_insight_engine import generate_store_brief
            from app.core.redis_client import cache_set as _cs
            for shop_d in shops_processed_set:
                try:
                    precompute_proactive_messages(db, shop_d)
                    brief = generate_store_brief(db, shop_d)
                    if brief:
                        _cs(f"hs:brief:{shop_d}", brief.to_dict(), 600)
                except Exception as exc:
                    log.warning("aggregation_worker: _run_cycle_inner failed: %s", exc)
        except Exception as exc:
            log(f"proactive precompute error (non-fatal): {exc}")

        # Always update last_run_at on successful cycle completion,
        # even when rows_written == 0 (no new events to process).
        # This is the liveness signal that /system/health reads —
        # separate from the watermark which only advances on real work.
        try:
            state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        except Exception as exc:
            log.warning("aggregation_worker: _run_cycle_inner failed: %s", exc)
            pass  # non-fatal — next cycle will update it

        log(
            f"cycle complete — shops={len(shops_processed_set)} "
            f"rows_written={rows_written} errors={errors}"
        )

    except Exception as exc:
        errors += 1
        last_error = str(exc)
        log(f"cycle-level error: {exc}")
        raise

    finally:
        try:
            _write_log(
                db,
                started_at=started_at,
                shops_processed=len(shops_processed_set),
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

def main() -> None:
    log("worker started")

    while True:
        from app.core.distributed_lock import worker_lock, extend_lock

        with worker_lock(WORKER_NAME, ttl_seconds=SLEEP_SECONDS + 60) as acquired:
            if not acquired:
                log("another instance holds the lock — skipping cycle")
            else:
                try:
                    run_cycle()
                except Exception as exc:
                    log(f"unhandled exception: {exc}")
                    raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
