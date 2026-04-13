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

_RETENTION_INTERVAL_S = 86_400   # run event retention at most once per 24 h

# Monotonic clock timestamp of the last SUCCESSFUL retention run.
# None = never run this process lifetime (triggers on first eligible cycle).
# Advanced only after conn.commit() succeeds — not before — so a commit
# failure does not silently consume the 24-hour retention window.
_last_retention_run: float | None = None

# Webhook health check — once per 24h, same pattern as retention
_WEBHOOK_CHECK_INTERVAL_S = 86_400  # 24 hours
_last_webhook_check: float | None = None

# CIG computation — once per 24h (weekly would be ideal but daily is cheap)
_CIG_INTERVAL_S = 86_400  # 24 hours
_last_cig_run: float | None = None

# Worker watchdog — once per hour, checks worker_log for repeated errors
_WATCHDOG_INTERVAL_S = 3_600  # 1 hour
_WATCHDOG_ERROR_THRESHOLD = 3  # consecutive cycles with errors = alert
_WATCHDOG_WINDOW_HOURS = 2    # look back this many hours
_last_watchdog_run: float | None = None

# Data integrity probe — semantic drift detection (attribution, orders,
# AOV, nudge lift). Runs every 6h; the probe itself is cheap but each
# sub-check sweeps N merchants, so we do not want it in every 5-min cycle.
_DATA_INTEGRITY_INTERVAL_S = 6 * 3_600
_last_data_integrity_run: float | None = None

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
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=STALE_TASK_THRESHOLD_MINUTES)

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


def _sweep_stuck_candidates(db: Session) -> int:
    """
    Find bugfix_candidates stuck in 'applying' status for > 10 minutes.
    These are candidates where the apply pipeline crashed or timed out
    without completing. Mark them as 'apply_failed' and release any locks.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

    from app.models.bugfix_candidate import BugFixCandidate
    stuck = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applying",
            BugFixCandidate.decided_at < cutoff,
        )
        .all()
    )

    if not stuck:
        return 0

    recovered = 0
    for c in stuck:
        c.status = "apply_failed"
        c.failure_reason = "stuck_in_applying: process crash or timeout — recovered by watchdog"
        recovered += 1

        # Release any execution lock
        try:
            from app.core.telegram_safety import release_execution_lock
            release_execution_lock("bugfix", str(c.id))
        except Exception:
            pass

        # Alert operator
        try:
            from app.services.alerting import write_alert
            write_alert(
                db, severity="warning", source="watchdog",
                alert_type="stuck_candidate_recovered",
                summary=f"Bugfix #{c.id} stuck in 'applying' for >10min — recovered to 'apply_failed'",
                detail={"candidate_id": c.id, "title": c.title},
            )
        except Exception:
            pass

        log(f"stuck-candidate sweep: recovered #{c.id} from 'applying' → 'apply_failed'")

    db.flush()
    return recovered


# ---------------------------------------------------------------------------
# Step A — find active products since watermark
# ---------------------------------------------------------------------------

BATCH_SIZE = 100  # products per cycle — prevents cycle overflow at scale


def _find_active_products(
    conn, last_watermark: int
) -> list[tuple[str, str]]:
    """
    Return distinct (shop_domain, product_url) pairs that have at
    least one event with timestamp > last_watermark.

    Uses events.product_url (canonical /products/{handle}) — not events.url.
    NULL product_url rows are non-product pages and are excluded implicitly
    by the IS NOT NULL filter.

    DEPRECATED — use _find_active_products_batch for cursor-based pagination.
    Kept for backward compatibility; callers should migrate.
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


def _find_active_products_batch(
    conn,
    last_watermark: int,
    cursor_shop: str | None = None,
    cursor_product: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[tuple[str, str]]:
    """
    Cursor-based batch fetch of active products.

    Returns up to batch_size (shop_domain, product_url) pairs starting
    AFTER the cursor position.  The cursor is the last (shop, product)
    pair from the previous batch.

    At 10k merchants × 100 products, this ensures each cycle processes
    at most BATCH_SIZE products, keeping cycle time < 60s.  Remaining
    products are picked up in subsequent cycles.

    Returns fewer than batch_size rows when the end is reached.
    """
    if cursor_shop is not None and cursor_product is not None:
        result = conn.execute(
            text("""
                SELECT DISTINCT shop_domain, product_url
                FROM events
                WHERE product_url IS NOT NULL
                  AND timestamp > :watermark
                  AND (shop_domain, product_url) > (:cursor_shop, :cursor_product)
                ORDER BY shop_domain, product_url
                LIMIT :batch_size
            """),
            {
                "watermark": last_watermark,
                "cursor_shop": cursor_shop,
                "cursor_product": cursor_product,
                "batch_size": batch_size,
            },
        )
    else:
        result = conn.execute(
            text("""
                SELECT DISTINCT shop_domain, product_url
                FROM events
                WHERE product_url IS NOT NULL
                  AND timestamp > :watermark
                ORDER BY shop_domain, product_url
                LIMIT :batch_size
            """),
            {"watermark": last_watermark, "batch_size": batch_size},
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
                    device_type,
                    source_type,
                    utm_medium,
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
                   OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart')
                  )
                GROUP BY visitor_id
            ),
            cart_visitors_7d AS (
                SELECT visitor_id, MIN(timestamp) AS first_cart_at
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp  >= :cutoff_7d
                  AND (
                      url        LIKE '%/cart%'
                   OR url        LIKE '%/checkout%'
                   OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart')
                  )
                GROUP BY visitor_id
            ),
            return_visitors AS (
                SELECT visitor_id
                FROM product_events
                WHERE event_type IN ('page_view', 'product_view')
                GROUP BY visitor_id
                HAVING COUNT(DISTINCT event_date) >= 2
            ),
            -- First event per visitor to determine their source bucket
            visitor_source AS (
                SELECT DISTINCT ON (visitor_id)
                    visitor_id,
                    source_type,
                    utm_medium
                FROM product_events
                ORDER BY visitor_id, timestamp ASC
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

                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors_7d cv ON cv.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp  <  cv.first_cart_at
                )                                                   AS cart_conversions_7d,

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

                MAX(timestamp)                                      AS last_event_at,

                -- Device segmentation (24h, view events only)
                COUNT(*) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                      AND timestamp >= :cutoff_24h
                      AND device_type = 'mobile'
                )                                                   AS views_mobile,

                COUNT(*) FILTER (
                    WHERE event_type IN ('page_view', 'product_view')
                      AND timestamp >= :cutoff_24h
                      AND device_type = 'desktop'
                )                                                   AS views_desktop,

                -- Device segmentation for carts: distinct visitors with device_type
                -- who viewed this product on that device AND later added to cart
                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp  >= :cutoff_24h
                      AND pe.timestamp  <  cv.first_cart_at
                      AND pe.device_type = 'mobile'
                )                                                   AS carts_mobile,

                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp  >= :cutoff_24h
                      AND pe.timestamp  <  cv.first_cart_at
                      AND pe.device_type = 'desktop'
                )                                                   AS carts_desktop,

                -- Source segmentation (24h views, using first-touch source per visitor)
                (
                    SELECT COUNT(*)
                    FROM product_events pe
                    INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp >= :cutoff_24h
                      AND (vs.utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                              'retargeting', 'display', 'banner', 'shopping')
                           OR vs.source_type IN ('paid_search', 'paid_social', 'google_shopping'))
                )                                                   AS views_paid,

                (
                    SELECT COUNT(*)
                    FROM product_events pe
                    INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp >= :cutoff_24h
                      AND COALESCE(vs.source_type, 'unknown') IN ('direct', 'unknown')
                      AND vs.utm_medium IS NULL
                )                                                   AS views_direct,

                -- Carts by source
                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
                    INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp >= :cutoff_24h
                      AND pe.timestamp <  cv.first_cart_at
                      AND (vs.utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                              'retargeting', 'display', 'banner', 'shopping')
                           OR vs.source_type IN ('paid_search', 'paid_social', 'google_shopping'))
                )                                                   AS carts_paid,

                (
                    SELECT COUNT(DISTINCT pe.visitor_id)
                    FROM product_events pe
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe.visitor_id
                    INNER JOIN visitor_source vs ON vs.visitor_id = pe.visitor_id
                    WHERE pe.event_type IN ('page_view', 'product_view')
                      AND pe.timestamp >= :cutoff_24h
                      AND pe.timestamp <  cv.first_cart_at
                      AND COALESCE(vs.source_type, 'unknown') IN ('direct', 'unknown')
                      AND vs.utm_medium IS NULL
                )                                                   AS carts_direct,

                -- Time-of-day: 4 x 6-hour blocks (UTC). Peak = block with most views.
                -- Block 0: 00-05, Block 1: 06-11, Block 2: 12-17, Block 3: 18-23
                (
                    SELECT json_agg(json_build_object('blk', blk, 'v', v, 'c', c))
                    FROM (
                        SELECT
                            EXTRACT(HOUR FROM to_timestamp(pe2.timestamp / 1000.0))::int / 6 AS blk,
                            COUNT(*) FILTER (WHERE pe2.event_type IN ('page_view', 'product_view')) AS v,
                            COUNT(DISTINCT pe2.visitor_id) FILTER (
                                WHERE pe2.visitor_id IN (SELECT visitor_id FROM cart_visitors)
                                  AND pe2.event_type IN ('page_view', 'product_view')
                            ) AS c
                        FROM product_events pe2
                        WHERE pe2.timestamp >= :cutoff_24h
                        GROUP BY blk
                    ) AS blocks
                )                                                   AS hourly_blocks,

                -- Session context: landing vs browsing
                -- A "landing" view = first event for this visitor in the 24h window
                -- is on this product_url (visitor entered through this product page)
                (
                    SELECT COUNT(*)
                    FROM product_events pe2
                    WHERE pe2.event_type IN ('page_view', 'product_view')
                      AND pe2.timestamp >= :cutoff_24h
                      AND pe2.timestamp = (
                          SELECT MIN(e3.timestamp)
                          FROM events e3
                          WHERE e3.shop_domain = :shop_domain
                            AND e3.visitor_id = pe2.visitor_id
                            AND e3.timestamp >= :cutoff_24h
                            AND e3.event_type IN ('page_view', 'product_view')
                      )
                )                                                   AS landing_views_24h,

                -- Landing carts: visitors whose first page was this product AND added to cart
                (
                    SELECT COUNT(DISTINCT pe2.visitor_id)
                    FROM product_events pe2
                    INNER JOIN cart_visitors cv ON cv.visitor_id = pe2.visitor_id
                    WHERE pe2.event_type IN ('page_view', 'product_view')
                      AND pe2.timestamp >= :cutoff_24h
                      AND pe2.timestamp < cv.first_cart_at
                      AND pe2.timestamp = (
                          SELECT MIN(e3.timestamp)
                          FROM events e3
                          WHERE e3.shop_domain = :shop_domain
                            AND e3.visitor_id = pe2.visitor_id
                            AND e3.timestamp >= :cutoff_24h
                            AND e3.event_type IN ('page_view', 'product_view')
                      )
                )                                                   AS landing_carts_24h

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

    _ZERO_PURCHASE = {
        "purchases_24h": 0, "purchases_7d": 0, "revenue_24h": 0.0,
        "purchases_mobile": 0, "purchases_desktop": 0,
        "purchases_paid": 0, "purchases_organic": 0, "purchases_direct": 0,
    }

    _ZERO_METRICS = {
        "shop_domain": shop_domain,
        "product_url": product_url,
        "views_1h": 0, "views_24h": 0, "views_7d": 0,
        "unique_visitors_24h": 0, "unique_visitors_7d": 0,
        "cart_conversions_24h": 0, "cart_conversions_7d": 0,
        "return_visitor_count_7d": 0,
        "avg_dwell_24h": None, "avg_scroll_24h": None,
        "last_event_at": None,
        "views_mobile": 0, "views_desktop": 0,
        "carts_mobile": 0, "carts_desktop": 0,
        "views_paid": 0, "views_organic": 0, "views_direct": 0,
        "carts_paid": 0, "carts_organic": 0, "carts_direct": 0,
        "peak_hour_views": 0, "peak_hour_carts": 0,
        "off_peak_hour_views": 0, "off_peak_hour_carts": 0,
        "landing_views_24h": 0, "browsing_views_24h": 0,
        "landing_carts_24h": 0, "browsing_carts_24h": 0,
        **_ZERO_PURCHASE,
    }

    if row is None:
        return _ZERO_METRICS

    m = dict(row._mapping)

    # Derive organic = total - paid - direct (avoids double-counting)
    views_paid = int(m["views_paid"] or 0)
    views_direct = int(m["views_direct"] or 0)
    views_24h = int(m["views_24h"] or 0)
    views_organic = max(0, views_24h - views_paid - views_direct)

    carts_paid = int(m["carts_paid"] or 0)
    carts_direct = int(m["carts_direct"] or 0)
    cart_conversions_24h = int(m["cart_conversions_24h"] or 0)
    carts_organic = max(0, cart_conversions_24h - carts_paid - carts_direct)

    # Time-of-day: parse hourly blocks JSON, find peak block
    hourly_blocks_raw = m.get("hourly_blocks")
    peak_hour_views = 0
    peak_hour_carts = 0
    off_peak_hour_views = 0
    off_peak_hour_carts = 0
    if hourly_blocks_raw:
        try:
            blocks = hourly_blocks_raw if isinstance(hourly_blocks_raw, list) else _json.loads(hourly_blocks_raw)
            if blocks:
                # Find the block with the most views
                peak_block = max(blocks, key=lambda b: b.get("v", 0))
                peak_hour_views = int(peak_block.get("v", 0))
                peak_hour_carts = int(peak_block.get("c", 0))
                for b in blocks:
                    if b.get("blk") != peak_block.get("blk"):
                        off_peak_hour_views += int(b.get("v", 0))
                        off_peak_hour_carts += int(b.get("c", 0))
        except Exception:
            pass  # degrade to zeros

    # Session context
    landing_views = int(m.get("landing_views_24h") or 0)
    landing_carts = int(m.get("landing_carts_24h") or 0)
    browsing_views = max(0, views_24h - landing_views)
    browsing_carts = max(0, cart_conversions_24h - landing_carts)

    metrics_result = {
        "shop_domain": shop_domain,
        "product_url": product_url,
        "views_1h": int(m["views_1h"] or 0),
        "views_24h": views_24h,
        "views_7d": int(m["views_7d"] or 0),
        "unique_visitors_24h": int(m["unique_visitors_24h"] or 0),
        "unique_visitors_7d": int(m["unique_visitors_7d"] or 0),
        "cart_conversions_24h": cart_conversions_24h,
        "cart_conversions_7d": int(m["cart_conversions_7d"] or 0),
        "return_visitor_count_7d": int(m["return_visitor_count_7d"] or 0),
        "avg_dwell_24h": float(m["avg_dwell_24h"]) if m["avg_dwell_24h"] is not None else None,
        "avg_scroll_24h": float(m["avg_scroll_24h"]) if m["avg_scroll_24h"] is not None else None,
        "last_event_at": int(m["last_event_at"]) if m["last_event_at"] is not None else None,
        "views_mobile": int(m["views_mobile"] or 0),
        "views_desktop": int(m["views_desktop"] or 0),
        "carts_mobile": int(m["carts_mobile"] or 0),
        "carts_desktop": int(m["carts_desktop"] or 0),
        "views_paid": views_paid,
        "views_organic": views_organic,
        "views_direct": views_direct,
        "carts_paid": carts_paid,
        "carts_organic": carts_organic,
        "carts_direct": carts_direct,
        "peak_hour_views": peak_hour_views,
        "peak_hour_carts": peak_hour_carts,
        "off_peak_hour_views": off_peak_hour_views,
        "off_peak_hour_carts": off_peak_hour_carts,
        "landing_views_24h": landing_views,
        "browsing_views_24h": browsing_views,
        "landing_carts_24h": landing_carts,
        "browsing_carts_24h": browsing_carts,
        **_ZERO_PURCHASE,  # defaults, overwritten by purchase query below
    }

    # --------------------------------------------------------------------- #
    # Purchase attribution (separate query — joins across tables)            #
    # --------------------------------------------------------------------- #
    purchase_result = _compute_purchase_metrics(
        conn, shop_domain, product_url, cutoff_24h, cutoff_7d
    )
    metrics_result.update(purchase_result)

    return metrics_result


def _compute_purchase_metrics(
    conn,
    shop_domain: str,
    product_url: str,
    cutoff_24h: int,
    cutoff_7d: int,
) -> dict:
    """
    Compute purchase-level attribution by joining:
    visitor_purchase_sessions → shop_orders (line_items JSONB) → events (device/source).

    Returns purchases_24h/7d, revenue_24h, and device/source purchase splits.

    Three key design decisions (hardened):

    1. PER-LINE-ITEM REVENUE — extracts price×quantity for the matching line item
       from the JSONB array, NOT the full order total.  Multi-product orders
       attribute only the product's share of revenue.

    2. PRODUCT MATCHING — uses product_url first, falls back to product_id
       (resolved via the events table mapping) when product_url is NULL in
       line_items.  This handles the common case where Shopify webhooks don't
       include the product handle but the tracker has captured the product_id.

    3. NEAREST-TOUCH ATTRIBUTION — device_type and source_type are taken from
       the visitor's most recent event BEFORE the purchase (not first-ever
       event).  This reflects the device/source active during the buying
       session, not an ancient first visit months ago.
    """
    _ZERO = {
        "purchases_24h": 0, "purchases_7d": 0, "revenue_24h": 0.0,
        "purchases_mobile": 0, "purchases_desktop": 0,
        "purchases_paid": 0, "purchases_organic": 0, "purchases_direct": 0,
    }

    # Step 1: resolve product_id(s) for this product_url from events table.
    # This enables the fallback match path in the JSONB query.
    pid_result = conn.execute(
        text("""
            SELECT DISTINCT product_id
            FROM events
            WHERE shop_domain  = :shop_domain
              AND product_url  = :product_url
              AND product_id  IS NOT NULL
            LIMIT 10
        """),
        {"shop_domain": shop_domain, "product_url": product_url},
    )
    product_ids = [r[0] for r in pid_result.fetchall()]
    # Build a SQL-safe array literal for the fallback; empty = no fallback
    pid_array = product_ids if product_ids else ["__none__"]

    result = conn.execute(
        text("""
            WITH matched_orders AS (
                -- Orders containing this product (by product_url OR product_id fallback).
                -- Extracts per-line-item revenue: price × quantity for the MATCHING item only.
                SELECT
                    vps.visitor_id,
                    vps.shopify_order_id,
                    EXTRACT(EPOCH FROM so.created_at) * 1000 AS order_ms,
                    EXTRACT(EPOCH FROM vps.confirmed_at) * 1000 AS confirmed_ms,
                    (
                        SELECT COALESCE(
                            SUM((li->>'price')::numeric * GREATEST((li->>'quantity')::int, 1)),
                            0
                        )
                        FROM jsonb_array_elements(so.line_items) AS li
                        WHERE li->>'product_url' = :product_url
                           OR (li->>'product_url' IS NULL AND li->>'product_id' = ANY(:product_ids))
                    ) AS line_revenue
                FROM visitor_purchase_sessions vps
                INNER JOIN shop_orders so
                    ON so.shopify_order_id = vps.shopify_order_id
                WHERE vps.shop_domain = :shop_domain
                  AND so.shop_domain  = :shop_domain
                  AND EXTRACT(EPOCH FROM so.created_at) * 1000 >= :cutoff_7d
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(so.line_items) AS item
                          WHERE item->>'product_url' = :product_url
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(so.line_items) AS item
                          WHERE item->>'product_url' IS NULL
                            AND item->>'product_id' = ANY(:product_ids)
                      )
                  )
            ),
            -- Nearest-touch attribution: most recent event BEFORE purchase
            -- (reflects the device/source active during the buying session)
            purchaser_attrs AS (
                SELECT DISTINCT ON (mo.visitor_id, mo.shopify_order_id)
                    mo.visitor_id,
                    mo.shopify_order_id,
                    mo.line_revenue,
                    mo.order_ms,
                    e.device_type,
                    e.source_type,
                    e.utm_medium
                FROM matched_orders mo
                INNER JOIN events e
                    ON e.visitor_id  = mo.visitor_id
                   AND e.shop_domain = :shop_domain
                   AND e.timestamp   <= mo.confirmed_ms
                ORDER BY mo.visitor_id, mo.shopify_order_id, e.timestamp DESC
            )
            SELECT
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h)     AS purchases_24h,
                COUNT(*)                                            AS purchases_7d,
                COALESCE(SUM(line_revenue) FILTER (WHERE order_ms >= :cutoff_24h), 0) AS revenue_24h,
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h AND device_type = 'mobile')  AS purchases_mobile,
                COUNT(*) FILTER (WHERE order_ms >= :cutoff_24h AND device_type = 'desktop') AS purchases_desktop,
                COUNT(*) FILTER (
                    WHERE order_ms >= :cutoff_24h
                      AND (utm_medium IN ('cpc', 'ppc', 'paid', 'paidsocial', 'paid_social',
                                           'retargeting', 'display', 'banner', 'shopping')
                           OR source_type IN ('paid_search', 'paid_social', 'google_shopping'))
                )                                                   AS purchases_paid,
                COUNT(*) FILTER (
                    WHERE order_ms >= :cutoff_24h
                      AND COALESCE(source_type, 'unknown') IN ('direct', 'unknown')
                      AND utm_medium IS NULL
                )                                                   AS purchases_direct
            FROM purchaser_attrs
        """),
        {
            "shop_domain": shop_domain,
            "product_url": product_url,
            "product_ids": pid_array,
            "cutoff_24h": cutoff_24h,
            "cutoff_7d": cutoff_7d,
        },
    )
    row = result.fetchone()
    if row is None:
        return _ZERO

    pm = dict(row._mapping)
    p24 = int(pm["purchases_24h"] or 0)
    p_paid = int(pm["purchases_paid"] or 0)
    p_direct = int(pm["purchases_direct"] or 0)
    p_organic = max(0, p24 - p_paid - p_direct)

    return {
        "purchases_24h": p24,
        "purchases_7d": int(pm["purchases_7d"] or 0),
        "revenue_24h": round(float(pm["revenue_24h"] or 0), 2),
        "purchases_mobile": int(pm["purchases_mobile"] or 0),
        "purchases_desktop": int(pm["purchases_desktop"] or 0),
        "purchases_paid": p_paid,
        "purchases_organic": p_organic,
        "purchases_direct": p_direct,
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
                shop_domain, product_url,
                views_1h, views_24h, views_7d,
                unique_visitors_24h, unique_visitors_7d,
                cart_conversions_24h, cart_conversions_7d,
                return_visitor_count_7d,
                avg_dwell_24h, avg_scroll_24h, last_event_at,
                views_mobile, views_desktop, carts_mobile, carts_desktop,
                views_paid, views_organic, views_direct,
                carts_paid, carts_organic, carts_direct,
                purchases_24h, purchases_7d, revenue_24h,
                purchases_mobile, purchases_desktop,
                purchases_paid, purchases_organic, purchases_direct,
                peak_hour_views, peak_hour_carts,
                off_peak_hour_views, off_peak_hour_carts,
                landing_views_24h, browsing_views_24h,
                landing_carts_24h, browsing_carts_24h,
                updated_at
            ) VALUES (
                :shop_domain, :product_url,
                :views_1h, :views_24h, :views_7d,
                :unique_visitors_24h, :unique_visitors_7d,
                :cart_conversions_24h, :cart_conversions_7d,
                :return_visitor_count_7d,
                :avg_dwell_24h, :avg_scroll_24h, :last_event_at,
                :views_mobile, :views_desktop, :carts_mobile, :carts_desktop,
                :views_paid, :views_organic, :views_direct,
                :carts_paid, :carts_organic, :carts_direct,
                :purchases_24h, :purchases_7d, :revenue_24h,
                :purchases_mobile, :purchases_desktop,
                :purchases_paid, :purchases_organic, :purchases_direct,
                :peak_hour_views, :peak_hour_carts,
                :off_peak_hour_views, :off_peak_hour_carts,
                :landing_views_24h, :browsing_views_24h,
                :landing_carts_24h, :browsing_carts_24h,
                now()
            )
            ON CONFLICT (shop_domain, product_url) DO UPDATE SET
                views_1h                = EXCLUDED.views_1h,
                views_24h               = EXCLUDED.views_24h,
                views_7d                = EXCLUDED.views_7d,
                unique_visitors_24h     = EXCLUDED.unique_visitors_24h,
                unique_visitors_7d      = EXCLUDED.unique_visitors_7d,
                cart_conversions_24h    = EXCLUDED.cart_conversions_24h,
                cart_conversions_7d     = EXCLUDED.cart_conversions_7d,
                return_visitor_count_7d = EXCLUDED.return_visitor_count_7d,
                avg_dwell_24h           = EXCLUDED.avg_dwell_24h,
                avg_scroll_24h          = EXCLUDED.avg_scroll_24h,
                last_event_at           = EXCLUDED.last_event_at,
                views_mobile            = EXCLUDED.views_mobile,
                views_desktop           = EXCLUDED.views_desktop,
                carts_mobile            = EXCLUDED.carts_mobile,
                carts_desktop           = EXCLUDED.carts_desktop,
                views_paid              = EXCLUDED.views_paid,
                views_organic           = EXCLUDED.views_organic,
                views_direct            = EXCLUDED.views_direct,
                carts_paid              = EXCLUDED.carts_paid,
                carts_organic           = EXCLUDED.carts_organic,
                carts_direct            = EXCLUDED.carts_direct,
                purchases_24h           = EXCLUDED.purchases_24h,
                purchases_7d            = EXCLUDED.purchases_7d,
                revenue_24h             = EXCLUDED.revenue_24h,
                purchases_mobile        = EXCLUDED.purchases_mobile,
                purchases_desktop       = EXCLUDED.purchases_desktop,
                purchases_paid          = EXCLUDED.purchases_paid,
                purchases_organic       = EXCLUDED.purchases_organic,
                purchases_direct        = EXCLUDED.purchases_direct,
                peak_hour_views         = EXCLUDED.peak_hour_views,
                peak_hour_carts         = EXCLUDED.peak_hour_carts,
                off_peak_hour_views     = EXCLUDED.off_peak_hour_views,
                off_peak_hour_carts     = EXCLUDED.off_peak_hour_carts,
                landing_views_24h       = EXCLUDED.landing_views_24h,
                browsing_views_24h      = EXCLUDED.browsing_views_24h,
                landing_carts_24h       = EXCLUDED.landing_carts_24h,
                browsing_carts_24h      = EXCLUDED.browsing_carts_24h,
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


def _run_ai_nudge_compose(db: Session) -> int:
    """
    Upgrade Pro nudges flagged ai_compose_pending=True with AI-composed
    variants. Runs once per 5-minute aggregation cycle, bounded to 5 nudges
    per cycle to protect the LLM budget.

    The merchant's request path NEVER blocks on OpenAI anymore — baseline
    variants are live immediately, and this loop replaces them with
    context-specific variants within minutes.

    Returns: count of nudges upgraded this cycle.
    """
    import asyncio
    import json as _json
    from app.models.active_nudge import ActiveNudge
    from app.models.product import Product
    from app.services.nudge_composer import compose_nudge_variants
    from app.core.protection_state import protection_state

    # SELF-PROTECTION: AI nudge composition is an OPTIONAL LLM call.
    # When the system is under LLM pressure, we skip entirely (baseline
    # variants remain live — nudges still serve). When the system is
    # DEGRADED for other reasons, we halve the batch to 2.
    state = protection_state()
    if state["level"] == "CRITICAL" or "skip_all_optional_llm_calls" in state["protective_actions"]:
        log(f"protection_state: {state['level']} — skipping _run_ai_nudge_compose")
        return 0
    if "skip_optional_llm_calls" in state["protective_actions"]:
        log(f"protection_state: DEGRADED (llm) — skipping _run_ai_nudge_compose")
        return 0
    # Reduce batch when system is DEGRADED with batch reduction requested,
    # or when the LLM subsystem specifically is degraded.
    reduce_batch = (
        "reduce_batch_sizes" in state.get("protective_actions", [])
        or state.get("subsystems", {}).get("llm", {}).get("level") == "degraded"
    )
    _MAX_PER_CYCLE = 2 if reduce_batch else 5
    if reduce_batch:
        log(f"protection_state: {state['level']} — reducing _run_ai_nudge_compose batch from 5 to {_MAX_PER_CYCLE}")

    pending = (
        db.query(ActiveNudge)
        .filter(
            ActiveNudge.ai_compose_pending == True,  # noqa: E712
            ActiveNudge.status == "active",
        )
        .order_by(ActiveNudge.created_at.asc())
        .limit(_MAX_PER_CYCLE)
        .all()
    )
    if not pending:
        return 0

    upgraded = 0
    for nudge in pending:
        try:
            # Fetch product title for prompt context
            product = (
                db.query(Product)
                .filter_by(shop_domain=nudge.shop_domain, product_url=nudge.product_url)
                .first()
            )
            product_title = (
                product.title.strip() if product and product.title
                else nudge.product_url.replace("/products/", "").replace("-", " ").title()
            )

            # Reconstruct minimal signals from current nudge context
            signals = {
                "unique_visitors_24h": nudge.visitor_count or 0,
                "action_type": nudge.action_type,
            }

            # compose_nudge_variants is async — run it to completion here.
            variants, meta = asyncio.run(
                compose_nudge_variants(
                    product_title=product_title,
                    product_url=nudge.product_url,
                    signals=signals,
                    data_window_hours=72,
                )
            )

            # Replace nudge copy with AI-composed variants
            if variants and len(variants) >= 2:
                primary = variants[0]
                nudge.copy_variant = primary.get("variant_name", nudge.copy_variant)
                nudge.copy_config = _json.dumps(primary.get("copy_config", {}))
                nudge.copy_variants = _json.dumps(variants)
                nudge.ai_compose_pending = False
                upgraded += 1
                log(f"ai_nudge_compose: upgraded nudge_id={nudge.id} shop={nudge.shop_domain} variants={len(variants)} fallback={meta.get('fallback_used')}")
            else:
                # Composer returned no usable output — clear the flag anyway
                # to avoid retrying forever.
                nudge.ai_compose_pending = False
                log(f"ai_nudge_compose: composer returned no variants for nudge_id={nudge.id}, flag cleared")
        except Exception as exc:
            # One nudge failure must not break the batch.
            log(f"ai_nudge_compose: failed for nudge_id={nudge.id} err={type(exc).__name__}: {exc}")
            continue

    # Flush so in-memory mutations are visible to the caller's commit/refresh.
    db.flush()
    return upgraded


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


def _run_nudge_event_retention(conn) -> int:
    """Delete nudge_events older than NUDGE_EVENT_RETENTION_DAYS."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=NUDGE_EVENT_RETENTION_DAYS)
    result = conn.execute(
        text("DELETE FROM nudge_events WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return result.rowcount


def _run_worker_log_retention(conn) -> int:
    """Delete worker_log entries older than WORKER_LOG_RETENTION_DAYS."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WORKER_LOG_RETENTION_DAYS)
    result = conn.execute(
        text("DELETE FROM worker_log WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Webhook health check (runs at most once per 24 h)
# ---------------------------------------------------------------------------

def _should_run_webhook_check() -> bool:
    if _last_webhook_check is None:
        return True
    return (time.monotonic() - _last_webhook_check) >= _WEBHOOK_CHECK_INTERVAL_S


def _run_webhook_health_check() -> None:
    """
    Check and optionally repair webhooks for all active merchants.

    Runs in its own DB session (not the connection-scoped cycle session)
    because webhook_health.py uses ORM queries that need a Session.

    Non-fatal: errors are logged but never crash the cycle.
    """
    from app.models.merchant import Merchant
    from app.services.webhook_health import check_webhook_health, repair_missing_webhooks
    from app.services.audit import write_audit_log
    from app.services.alerting import write_alert

    db = SessionLocal()
    try:
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.access_token.isnot(None),
            )
            .all()
        )
        log(f"webhook health: checking {len(merchants)} active merchant(s)")

        from app.services.onboarding import _ONBOARDING_BLOCKLIST

        for m in merchants:
            if m.shop_domain in _ONBOARDING_BLOCKLIST:
                continue

            try:
                from app.services.webhook_monitor import record_check_result

                report = check_webhook_health(db, m.shop_domain)
                if report.healthy:
                    record_check_result(m.shop_domain, healthy=True)
                    continue

                if report.error:
                    record_check_result(
                        m.shop_domain, healthy=False, error=report.error,
                    )
                    log(f"webhook health: skip shop={m.shop_domain} error={report.error}")
                    continue

                # Drift detected — attempt repair
                log(f"webhook health: drift detected shop={m.shop_domain} missing={report.missing} stale={report.stale}")
                result = repair_missing_webhooks(db, m.shop_domain)
                db.commit()

                repair_succeeded = bool(result.repaired) and not result.failed
                record_check_result(
                    m.shop_domain, healthy=repair_succeeded,
                    missing=report.missing, stale=report.stale,
                    repair_attempted=True, repair_succeeded=repair_succeeded,
                )

                write_audit_log(
                    db,
                    actor_type="worker",
                    actor_name="aggregation_worker",
                    action_type="webhook_repair",
                    target_type="merchant",
                    target_id=m.shop_domain,
                    shop_domain=m.shop_domain,
                    before_state={"missing": report.missing, "stale": report.stale},
                    after_state={"repaired": result.repaired, "failed": result.failed},
                    status="completed" if not result.failed else "partial",
                    approval_mode="autonomous",
                )
                db.commit()

                if result.repaired:
                    log(f"webhook health: repaired shop={m.shop_domain} topics={result.repaired}")
                    write_alert(
                        db, severity="info", source="aggregation_worker",
                        alert_type="webhook_repaired", shop_domain=m.shop_domain,
                        summary=f"Auto-repaired webhooks: {result.repaired}",
                    )
                    db.commit()
                if result.failed:
                    log(f"webhook health: repair FAILED shop={m.shop_domain} topics={result.failed}")
                    write_alert(
                        db, severity="warning", source="aggregation_worker",
                        alert_type="webhook_repair_failed", shop_domain=m.shop_domain,
                        summary=f"Webhook repair failed for: {result.failed}",
                        detail={"failed": result.failed, "repaired": result.repaired},
                    )
                    db.commit()

            except Exception as exc:
                log(f"webhook health: error shop={m.shop_domain}: {exc}")
                db.rollback()

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Worker watchdog — detect repeated failures via worker_log
# ---------------------------------------------------------------------------

def _should_run_watchdog() -> bool:
    if _last_watchdog_run is None:
        return True
    return (time.monotonic() - _last_watchdog_run) >= _WATCHDOG_INTERVAL_S


def _should_run_data_integrity_probe() -> bool:
    if _last_data_integrity_run is None:
        return True
    return (time.monotonic() - _last_data_integrity_run) >= _DATA_INTEGRITY_INTERVAL_S


def _run_data_integrity_probe() -> None:
    """
    Sweep active merchants and flag semantic drift: attribution collapse,
    order collapse, AOV drift, nudge lift decay. Findings are written to
    ops_alerts and picked up by bugfix_pipeline.run_bug_triage Rule 6.

    Runs in its own DB session. Non-fatal — a probe failure logs and exits
    cleanly so it never blocks the aggregation cycle.
    """
    db = SessionLocal()
    try:
        from app.services.data_integrity_probe import run_probe
        result = run_probe(db)
        db.commit()
        if result.findings:
            log(
                f"data_integrity_probe: checks={result.checks_run} "
                f"findings={len(result.findings)} errors={len(result.errors)}"
            )
    except Exception as exc:
        log(f"data_integrity_probe: error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _run_worker_watchdog() -> None:
    """
    Check worker_log for workers with repeated errors in recent cycles.

    If a worker has >= _WATCHDOG_ERROR_THRESHOLD consecutive cycles with
    errors > 0 in the last _WATCHDOG_WINDOW_HOURS, write a warning alert.

    Runs in its own DB session. Non-fatal.
    """
    from app.services.alerting import write_alert

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=_WATCHDOG_WINDOW_HOURS)

        # Get recent worker_log entries grouped by worker
        rows = db.execute(text("""
            SELECT worker_name, errors, started_at
            FROM worker_log
            WHERE started_at >= :cutoff
            ORDER BY worker_name, started_at DESC
        """), {"cutoff": cutoff}).fetchall()

        # Group by worker, check for consecutive error runs
        from itertools import groupby
        for worker_name, entries in groupby(rows, key=lambda r: r[0]):
            entry_list = list(entries)
            # Count consecutive cycles with errors > 0 (most recent first)
            consecutive_errors = 0
            for entry in entry_list:
                if entry[1] and entry[1] > 0:
                    consecutive_errors += 1
                else:
                    break

            if consecutive_errors >= _WATCHDOG_ERROR_THRESHOLD:
                # Check if we already have an unresolved alert for this worker
                from app.models.ops_alert import OpsAlert
                existing = (
                    db.query(OpsAlert)
                    .filter(
                        OpsAlert.alert_type == "worker_repeated_failure",
                        OpsAlert.source == worker_name,
                        OpsAlert.resolved == False,
                    )
                    .first()
                )
                if existing:
                    continue  # Already alerted, don't spam

                write_alert(
                    db,
                    severity="warning",
                    source=worker_name,
                    alert_type="worker_repeated_failure",
                    summary=f"{worker_name} has errored in {consecutive_errors} consecutive cycles",
                    detail={
                        "consecutive_errors": consecutive_errors,
                        "window_hours": _WATCHDOG_WINDOW_HOURS,
                        "recent_entries": len(entry_list),
                    },
                )
                db.commit()
                log(f"watchdog: alert raised for {worker_name} ({consecutive_errors} consecutive errors)")

    except Exception as exc:
        log(f"watchdog: error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Step G — compute and upsert store_metrics (per shop, every cycle)
# ---------------------------------------------------------------------------

def _compute_store_metrics(conn, shop_domain: str) -> dict:
    """
    Compute store-level intelligence for one shop:
    1. Co-viewed product pairs (bounded: top 15 products, >= 3 shared visitors, top 10 pairs)
    2. Cohort snapshot (new vs returning visitors, 7d window)

    Both queries use existing indexes. Total cost is bounded and predictable.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_7d = now_ms - 604_800_000

    # --- Co-viewed pairs ---
    co_viewed = []
    try:
        # Get top 15 product URLs by 7d views for this shop
        top_result = conn.execute(
            text("""
                SELECT product_url, views_7d
                FROM product_metrics
                WHERE shop_domain = :shop AND views_7d > 0
                ORDER BY views_7d DESC
                LIMIT 15
            """),
            {"shop": shop_domain},
        )
        top_rows = top_result.fetchall()
        top_urls = [r[0] for r in top_rows]
        view_map = {r[0]: int(r[1] or 0) for r in top_rows}

        if len(top_urls) >= 2:
            pair_result = conn.execute(
                text("""
                    WITH visitor_products AS (
                        SELECT DISTINCT visitor_id, product_url
                        FROM events
                        WHERE shop_domain = :shop
                          AND product_url = ANY(:urls)
                          AND event_type IN ('page_view', 'product_view')
                          AND timestamp >= :cutoff_7d
                    ),
                    pairs AS (
                        SELECT
                            a.product_url AS product_a,
                            b.product_url AS product_b,
                            COUNT(DISTINCT a.visitor_id) AS shared_visitors
                        FROM visitor_products a
                        INNER JOIN visitor_products b
                            ON a.visitor_id = b.visitor_id
                           AND a.product_url < b.product_url
                        GROUP BY a.product_url, b.product_url
                        HAVING COUNT(DISTINCT a.visitor_id) >= 3
                        ORDER BY shared_visitors DESC
                        LIMIT 10
                    )
                    SELECT * FROM pairs
                """),
                {"shop": shop_domain, "urls": top_urls, "cutoff_7d": cutoff_7d},
            )
            for r in pair_result.fetchall():
                co_viewed.append({
                    "product_a": r[0],
                    "product_b": r[1],
                    "shared_visitors": int(r[2]),
                    "a_views": view_map.get(r[0], 0),
                    "b_views": view_map.get(r[1], 0),
                })
    except Exception as exc:
        log(f"store_metrics co_viewed error for {shop_domain} (non-fatal): {exc}")

    # --- Cohort snapshot ---
    new_v, new_c, ret_v, ret_c = 0, 0, 0, 0
    try:
        cohort_result = conn.execute(
            text("""
                WITH visitor_status AS (
                    SELECT
                        v.visitor_id,
                        CASE WHEN v.first_seen >= NOW() - INTERVAL '7 days'
                             THEN 'new' ELSE 'returning' END AS cohort
                    FROM visitors v
                    WHERE v.shop_domain = :shop
                      AND v.last_seen >= NOW() - INTERVAL '7 days'
                ),
                visitor_carts AS (
                    SELECT DISTINCT visitor_id
                    FROM events
                    WHERE shop_domain = :shop
                      AND timestamp >= :cutoff_7d
                      AND (url LIKE '%%/cart%%' OR url LIKE '%%/checkout%%'
                           OR event_type IN ('add_to_cart', 'begin_checkout', 'view_cart'))
                )
                SELECT
                    vs.cohort,
                    COUNT(DISTINCT vs.visitor_id) AS visitors,
                    COUNT(DISTINCT vc.visitor_id) AS carters
                FROM visitor_status vs
                LEFT JOIN visitor_carts vc ON vc.visitor_id = vs.visitor_id
                GROUP BY vs.cohort
            """),
            {"shop": shop_domain, "cutoff_7d": cutoff_7d},
        )
        for r in cohort_result.fetchall():
            if r[0] == "new":
                new_v, new_c = int(r[1]), int(r[2])
            elif r[0] == "returning":
                ret_v, ret_c = int(r[1]), int(r[2])
    except Exception as exc:
        log(f"store_metrics cohort error for {shop_domain} (non-fatal): {exc}")

    return {
        "shop_domain": shop_domain,
        "co_viewed_pairs": co_viewed,
        "new_visitors_7d": new_v,
        "returning_visitors_7d": ret_v,
        "new_visitor_cart_rate": round(new_c / new_v, 4) if new_v > 0 else None,
        "returning_visitor_cart_rate": round(ret_c / ret_v, 4) if ret_v > 0 else None,
    }


def _upsert_store_metrics(conn, metrics: dict) -> None:
    """Upsert one store_metrics row. Execution opportunities are in their own table."""
    conn.execute(
        text("""
            INSERT INTO store_metrics (
                shop_domain, co_viewed_pairs,
                new_visitors_7d, returning_visitors_7d,
                new_visitor_cart_rate, returning_visitor_cart_rate,
                updated_at
            ) VALUES (
                :shop_domain, CAST(:co_viewed_pairs AS jsonb),
                :new_visitors_7d, :returning_visitors_7d,
                :new_visitor_cart_rate, :returning_visitor_cart_rate,
                now()
            )
            ON CONFLICT (shop_domain) DO UPDATE SET
                co_viewed_pairs            = CAST(:co_viewed_pairs AS jsonb),
                new_visitors_7d            = EXCLUDED.new_visitors_7d,
                returning_visitors_7d      = EXCLUDED.returning_visitors_7d,
                new_visitor_cart_rate       = EXCLUDED.new_visitor_cart_rate,
                returning_visitor_cart_rate = EXCLUDED.returning_visitor_cart_rate,
                updated_at                 = now()
        """),
        {
            **{k: v for k, v in metrics.items() if k != "co_viewed_pairs"},
            "co_viewed_pairs": _json.dumps(metrics.get("co_viewed_pairs", [])),
        },
    )


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle() -> None:
    global _last_retention_run, _last_webhook_check, _last_watchdog_run, _last_data_integrity_run

    from app.core.metrics import track_worker_cycle
    with track_worker_cycle(WORKER_NAME):
        _run_cycle_inner()


def _run_cycle_inner() -> None:
    global _last_retention_run, _last_webhook_check, _last_watchdog_run, _last_data_integrity_run

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
                    _last_retention_run = time.monotonic()
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
                    _last_webhook_check = time.monotonic()
                except Exception as exc:
                    log(f"webhook health check error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Worker watchdog (once per hour)                               #
            # ---------------------------------------------------------- #
            if _should_run_watchdog():
                try:
                    _run_worker_watchdog()
                    _last_watchdog_run = time.monotonic()
                except Exception as exc:
                    log(f"watchdog error (non-fatal): {exc}")

            # ---------------------------------------------------------- #
            # Data integrity probe (once per 6 h) — semantic drift       #
            # ---------------------------------------------------------- #
            if _should_run_data_integrity_probe():
                try:
                    _run_data_integrity_probe()
                    _last_data_integrity_run = time.monotonic()
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
                except Exception:
                    pass

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
                            text("SELECT DISTINCT shop_domain FROM product_metrics LIMIT 50")
                        ).fetchall()
                        all_shops = {r[0] for r in shop_rows}
                    except Exception:
                        pass

                from app.services.execution_engine import (
                    process_execution_opportunities,
                    _update_tracking_outcomes,
                    compute_post_execution_deltas,
                    detect_holdout_leakage,
                )

                store_count = 0
                exec_count = 0
                for shop in all_shops:
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
                except Exception:
                    pass
        except Exception as exc:
            log(f"proactive precompute error (non-fatal): {exc}")

        # Always update last_run_at on successful cycle completion,
        # even when rows_written == 0 (no new events to process).
        # This is the liveness signal that /system/health reads —
        # separate from the watermark which only advances on real work.
        try:
            state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        except Exception:
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
