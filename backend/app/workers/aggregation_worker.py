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

import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.append("/opt/wishspark/backend")

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
STALE_TASK_THRESHOLD_MINUTES = 10  # release executing tasks older than this

_RETENTION_INTERVAL_S = 86_400   # run event retention at most once per 24 h

# Monotonic clock timestamp of the last SUCCESSFUL retention run.
# None = never run this process lifetime (triggers on first eligible cycle).
# Advanced only after conn.commit() succeeds — not before — so a commit
# failure does not silently consume the 24-hour retention window.
_last_retention_run: float | None = None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[AGGREGATION_WORKER] {ts} | {msg}", flush=True)


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
    state.last_run_at = datetime.utcnow()
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
    finished_at = datetime.utcnow()
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


# ---------------------------------------------------------------------------
# Stale-task sweep (runs every cycle)
# ---------------------------------------------------------------------------

def _sweep_stale_tasks(db: Session) -> int:
    """
    Find action_tasks stuck in status=executing beyond the stale threshold
    and release them back to pending using release_task().

    The threshold is STALE_TASK_THRESHOLD_MINUTES (default 10 minutes).
    Each stale task is released atomically — release_task() acquires a
    SELECT FOR UPDATE lock and appends a release note to result_detail.

    Per-task errors are non-fatal: one task failing to release does not
    abort the sweep for remaining tasks.  All errors are logged.

    Returns the number of tasks successfully released this cycle.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_TASK_THRESHOLD_MINUTES)

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
        # Capture identity and age BEFORE release_task() clears claimed_by.
        previous_claimant = task.claimed_by or "unknown"
        age_minutes = (
            (datetime.utcnow() - task.executed_at).total_seconds() / 60
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
                log(
                    f"stale-task sweep: released task_id={task.id} "
                    f"shop={task.shop_domain} "
                    f"was_claimed_by={previous_claimant} "
                    f"age={age_minutes:.1f}min"
                )
                released += 1
            else:
                # Task status changed between our query and the lock —
                # another process already handled it.  Not an error.
                log(
                    f"stale-task sweep: task_id={task.id} skipped "
                    f"(conflict={conflict!r}, likely resolved concurrently)"
                )
        except Exception as exc:
            log(
                f"stale-task sweep: error releasing task_id={task.id} "
                f"shop={task.shop_domain}: {exc}"
            )

    return released


# ---------------------------------------------------------------------------
# Step A — find active products since watermark
# ---------------------------------------------------------------------------

def _find_active_products(
    conn, last_watermark: int
) -> list[tuple[str, str]]:
    """
    Return distinct (shop_domain, product_url) pairs that have at
    least one event with timestamp > last_watermark.

    Uses events.product_url (canonical /products/{handle}) — not events.url.
    NULL product_url rows are non-product pages and are excluded implicitly
    by the IS NOT NULL filter.
    """
    result = conn.execute(
        text("""
            SELECT DISTINCT shop_domain, product_url
            FROM events
            WHERE product_url IS NOT NULL
              AND timestamp > :watermark
            ORDER BY shop_domain, product_url
        """),
        {"watermark": last_watermark},
    )
    return [(row.shop_domain, row.product_url) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Step B — compute all metrics for one (shop, product) pair
# ---------------------------------------------------------------------------

def _compute_metrics(
    conn,
    shop_domain: str,
    product_url: str,
    now_ms: int,
) -> dict:
    """
    Run a single CTE query that computes all ten metric columns for the
    given (shop_domain, product_url) pair.

    Time windows (epoch milliseconds):
        1h  = now_ms - 3_600_000
        24h = now_ms - 86_400_000
        7d  = now_ms - 604_800_000

    Metric definitions
    ------------------
    views_1h / views_24h / views_7d
        COUNT of page_view + product_view events on this product URL
        within each window.

    unique_visitors_24h / unique_visitors_7d
        COUNT DISTINCT visitor_id for page_view + product_view events
        within each window.

    cart_conversions_24h
        Count of distinct visitors who both viewed this product URL
        (page_view or product_view) AND had a cart/checkout event
        within the same 24 h window.  Cart detection: url LIKE '%/cart%'
        OR url LIKE '%/checkout%' OR event_type = 'add_to_cart'.

    return_visitor_count_7d
        Count of distinct visitors who viewed this product on 2+ distinct
        calendar days within the last 7 days.
        Calendar day is computed from the epoch-ms timestamp using
        DATE(to_timestamp(timestamp / 1000.0)).

    avg_dwell_24h / avg_scroll_24h
        AVG(dwell_seconds) / AVG(max_scroll_depth) from dwell_time and
        page_leave events on this product URL within the last 24 h,
        where the respective column is not NULL.

    last_event_at
        MAX(timestamp) across all event types for this product URL,
        bounded to the 7-day window (the same scope as the product_events
        CTE).  NULL means no events for this product in the last 7 days.
    """
    cutoff_1h = now_ms - 3_600_000
    cutoff_24h = now_ms - 86_400_000
    cutoff_7d = now_ms - 604_800_000

    result = conn.execute(
        text("""
            WITH product_events AS (
                SELECT
                    visitor_id,
                    event_type,
                    timestamp,
                    dwell_seconds,
                    max_scroll_depth,
                    DATE(to_timestamp(timestamp / 1000.0)) AS event_date
                FROM events
                WHERE shop_domain  = :shop_domain
                  AND product_url  = :product_url
                  AND timestamp   >= :cutoff_7d
            ),
            cart_visitors AS (
                -- Track the earliest cart/checkout event per visitor so we can
                -- enforce temporal ordering: cart event must come AFTER the
                -- product view (prevents crediting a visitor who already checked
                -- out for an unrelated product before browsing this one).
                SELECT visitor_id, MIN(timestamp) AS first_cart_at
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp  >= :cutoff_24h
                  AND (
                      url        LIKE '%/cart%'
                   OR url        LIKE '%/checkout%'
                   OR event_type  = 'add_to_cart'
                  )
                GROUP BY visitor_id
            ),
            return_visitors AS (
                SELECT visitor_id
                FROM product_events
                WHERE event_type IN ('page_view', 'product_view')
                GROUP BY visitor_id
                HAVING COUNT(DISTINCT event_date) >= 2
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                      AND timestamp >= :cutoff_1h
                )                                                   AS views_1h,

                COUNT(*) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                      AND timestamp >= :cutoff_24h
                )                                                   AS views_24h,

                COUNT(*) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                )                                                   AS views_7d,

                COUNT(DISTINCT visitor_id) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                      AND timestamp >= :cutoff_24h
                )                                                   AS unique_visitors_24h,

                COUNT(DISTINCT visitor_id) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                )                                                   AS unique_visitors_7d,

                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp  >= :cutoff_24h
                      AND pe.timestamp  <  cv.first_cart_at
                )                                                   AS cart_conversions_24h,

                (SELECT COUNT(*) FROM return_visitors)              AS return_visitor_count_7d,

                AVG(dwell_seconds) FILTER (
                    WHERE event_type IN ('dwell_time', 'page_leave', 'product_view')
                      AND dwell_seconds IS NOT NULL
                      AND timestamp >= :cutoff_24h
                )                                                   AS avg_dwell_24h,

                AVG(max_scroll_depth) FILTER (
                    WHERE event_type IN ('dwell_time', 'page_leave', 'product_view')
                      AND max_scroll_depth IS NOT NULL
                      AND timestamp >= :cutoff_24h
                )                                                   AS avg_scroll_24h,

                MAX(timestamp)                                      AS last_event_at

            FROM product_events
        """),
        {
            "shop_domain": shop_domain,
            "product_url": product_url,
            "cutoff_1h": cutoff_1h,
            "cutoff_24h": cutoff_24h,
            "cutoff_7d": cutoff_7d,
        },
    )
    row = result.fetchone()
    if row is None:
        return {
            "shop_domain": shop_domain,
            "product_url": product_url,
            "views_1h": 0,
            "views_24h": 0,
            "views_7d": 0,
            "unique_visitors_24h": 0,
            "unique_visitors_7d": 0,
            "cart_conversions_24h": 0,
            "return_visitor_count_7d": 0,
            "avg_dwell_24h": None,
            "avg_scroll_24h": None,
            "last_event_at": None,
        }

    m = dict(row._mapping)
    return {
        "shop_domain": shop_domain,
        "product_url": product_url,
        "views_1h": int(m["views_1h"] or 0),
        "views_24h": int(m["views_24h"] or 0),
        "views_7d": int(m["views_7d"] or 0),
        "unique_visitors_24h": int(m["unique_visitors_24h"] or 0),
        "unique_visitors_7d": int(m["unique_visitors_7d"] or 0),
        "cart_conversions_24h": int(m["cart_conversions_24h"] or 0),
        "return_visitor_count_7d": int(m["return_visitor_count_7d"] or 0),
        "avg_dwell_24h": float(m["avg_dwell_24h"]) if m["avg_dwell_24h"] is not None else None,
        "avg_scroll_24h": float(m["avg_scroll_24h"]) if m["avg_scroll_24h"] is not None else None,
        "last_event_at": int(m["last_event_at"]) if m["last_event_at"] is not None else None,
    }


# ---------------------------------------------------------------------------
# Step C — upsert one metrics row
# ---------------------------------------------------------------------------

def _upsert_metrics(conn, metrics: dict) -> None:
    """
    INSERT the metrics row; on conflict (shop_domain, product_url) update
    all metric columns.  updated_at is always set to now().
    """
    conn.execute(
        text("""
            INSERT INTO product_metrics (
                shop_domain,
                product_url,
                views_1h,
                views_24h,
                views_7d,
                unique_visitors_24h,
                unique_visitors_7d,
                cart_conversions_24h,
                return_visitor_count_7d,
                avg_dwell_24h,
                avg_scroll_24h,
                last_event_at,
                updated_at
            ) VALUES (
                :shop_domain,
                :product_url,
                :views_1h,
                :views_24h,
                :views_7d,
                :unique_visitors_24h,
                :unique_visitors_7d,
                :cart_conversions_24h,
                :return_visitor_count_7d,
                :avg_dwell_24h,
                :avg_scroll_24h,
                :last_event_at,
                now()
            )
            ON CONFLICT (shop_domain, product_url) DO UPDATE SET
                views_1h                = EXCLUDED.views_1h,
                views_24h               = EXCLUDED.views_24h,
                views_7d                = EXCLUDED.views_7d,
                unique_visitors_24h     = EXCLUDED.unique_visitors_24h,
                unique_visitors_7d      = EXCLUDED.unique_visitors_7d,
                cart_conversions_24h    = EXCLUDED.cart_conversions_24h,
                return_visitor_count_7d = EXCLUDED.return_visitor_count_7d,
                avg_dwell_24h           = EXCLUDED.avg_dwell_24h,
                avg_scroll_24h          = EXCLUDED.avg_scroll_24h,
                last_event_at           = EXCLUDED.last_event_at,
                updated_at              = now()
        """),
        metrics,
    )


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
# Step E — clean up expired opportunity signals (runs every cycle)
# ---------------------------------------------------------------------------

def _cleanup_expired_signals(conn) -> int:
    """
    Delete opportunity_signals rows whose hard expiry has passed.

    Uses the ix_opportunity_signals_expires_at index for a fast range delete.
    Runs every cycle — each individual DELETE is cheap and keeps the table
    small.  Returns the number of rows deleted.

    This is the authoritative cleanup path.  _persist_signals() in
    opportunity_engine.py no longer performs inline cleanup so that signal
    lifetime is always governed by expires_at, not by detection frequency.
    """
    result = conn.execute(
        text("DELETE FROM opportunity_signals WHERE expires_at < now()")
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Retention job (runs at most once per 24 h within this process lifetime)
# ---------------------------------------------------------------------------

def _should_run_retention() -> bool:
    if _last_retention_run is None:
        return True
    return (time.monotonic() - _last_retention_run) >= _RETENTION_INTERVAL_S


def _get_distinct_shops(conn) -> list[str]:
    result = conn.execute(
        text("SELECT DISTINCT shop_domain FROM events WHERE shop_domain IS NOT NULL")
    )
    return [row.shop_domain for row in result.fetchall()]


def _run_retention(conn, now_ms: int) -> int:
    """
    Delete events older than RETENTION_DAYS, one shop at a time.

    Each per-shop DELETE uses the events(shop_domain, timestamp DESC)
    index efficiently.  Returns total rows deleted across all shops.
    """
    cutoff_ms = now_ms - (RETENTION_DAYS * 24 * 3_600 * 1_000)
    shops = _get_distinct_shops(conn)
    total_deleted = 0

    for shop in shops:
        result = conn.execute(
            text("""
                DELETE FROM events
                WHERE shop_domain = :shop
                  AND timestamp   < :cutoff_ms
            """),
            {"shop": shop, "cutoff_ms": cutoff_ms},
        )
        total_deleted += result.rowcount

    return total_deleted


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle() -> None:
    global _last_retention_run

    started_at = datetime.utcnow()
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
            else:
                log("stale-task sweep: 0 stale tasks found")
        except Exception as exc:
            log(f"stale-task sweep error (non-fatal): {exc}")

        # ------------------------------------------------------------------ #
        # Nudge expiry sweep — mark expired active_nudges as 'expired'        #
        # ------------------------------------------------------------------ #
        try:
            nudges_expired = expire_stale_nudges(db)
            if nudges_expired > 0:
                log(f"nudge expiry: marked {nudges_expired} nudges as expired")
        except Exception as exc:
            log(f"nudge expiry error (non-fatal): {exc}")

        state = _load_state(db)
        last_watermark = state.last_watermark or 0
        log(f"last_watermark={last_watermark}")

        with engine.connect() as conn:

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
                    conn.commit()
                    _last_retention_run = time.monotonic()
                    log(f"retention: deleted {deleted} events older than {RETENTION_DAYS} days")
                except Exception as exc:
                    conn.rollback()
                    log(f"retention error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Find active products                                         #
            # ---------------------------------------------------------- #
            active_products = _find_active_products(conn, last_watermark)
            log(f"active products since watermark: {len(active_products)}")

            if not active_products:
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

                # Advance watermark — at-least-once guarantee.
                # Only advances when at least one row was written successfully.
                if rows_written > 0:
                    try:
                        new_watermark = _read_new_watermark(conn)
                        _save_state(db, state, new_watermark)
                        log(f"watermark advanced: {last_watermark} → {new_watermark}")
                    except Exception as exc:
                        log(f"watermark update error (non-fatal, will retry next cycle): {exc}")

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
        try:
            run_cycle()
        except Exception as exc:
            log(f"unhandled exception: {exc}")
            raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
