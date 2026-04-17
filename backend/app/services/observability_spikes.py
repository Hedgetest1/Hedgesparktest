"""
observability_spikes.py — Spike detectors for the self-healing pipeline.

Purpose
-------
Convert high-volume low-severity signals (tracker runtime errors,
dashboard frontend errors) and slow-trend regressions (p95 latency
drift) into single high-severity ops_alert events the bugfix_pipeline
can triage.

Each detector is:
  - Idempotent  — safe to call every aggregation cycle (5 min)
  - Deduplicated — Redis cooldown key prevents alert storm
  - Fail-open   — a Redis outage degrades to "no spike alert" rather
                  than crashing the worker cycle
  - Measured    — every silent fallback is logged via
                  record_silent_return

Invoked from: aggregation_worker._run_cycle_inner
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("observability_spikes")


# ---------------------------------------------------------------------------
# Tracker runtime error spike
# ---------------------------------------------------------------------------
# When a shop's storefront tracker throws > N distinct errors in a day,
# something changed (theme deployment, browser update, our code). Alert
# once per (shop, day) so the self-healing pipeline can triage.
_TRACKER_SPIKE_THRESHOLD_DISTINCT_HASHES = 5
_TRACKER_SPIKE_THRESHOLD_TOTAL_EVENTS = 50
_TRACKER_SPIKE_COOLDOWN_KEY = "hs:spike:tracker_runtime:{shop}:{day}"
_TRACKER_SPIKE_COOLDOWN_TTL = 86400  # 1 per shop per day


_TEST_MODE_COOLDOWN: set[str] = set()


def reset_test_cooldowns() -> None:
    """Test helper — clears the in-process cooldown set. Call from a
    pytest fixture to isolate tests that exercise the dedup path."""
    _TEST_MODE_COOLDOWN.clear()


def _cooldown_ok(cooldown_key: str, ttl_seconds: int) -> bool:
    """Returns True if cooldown not active (allowed to fire), False if
    we've already fired for this key. Fail-open on Redis outage — we'd
    rather emit a duplicate alert than lose an alert entirely.

    Under APP_ENV=test we use an in-process set instead of Redis so
    tests can assert both the "first fires" AND "second dedups"
    behavior within one test function. Call reset_test_cooldowns()
    at the start of each test that exercises the dedup path."""
    import os
    if os.environ.get("APP_ENV") == "test":
        if cooldown_key in _TEST_MODE_COOLDOWN:
            return False
        _TEST_MODE_COOLDOWN.add(cooldown_key)
        return True
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("observability_spikes.cooldown.no_client")
            return True
        # SETNX-style: only set if absent. Returns True if we set it.
        acquired = rc.set(cooldown_key, "1", nx=True, ex=ttl_seconds)
        return bool(acquired)
    except Exception:
        record_silent_return("observability_spikes.cooldown.exception")
        return True


def detect_tracker_error_spikes(db: Session) -> int:
    """Scan Redis tracker-error counters (populated by
    POST /public/tracker-error) and emit one
    `tracker_runtime_error_spike` per shop crossing the threshold.

    Reads Redis-not-DB: the tracker endpoint bypasses the ops_alerts
    dedup machinery (which collapses duplicate reports into single
    rows) so distinct-hash accounting stays accurate. Redis SCAN is
    used instead of KEYS to stay non-blocking at 10k+ merchants."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day = now.strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("observability_spikes.tracker_scan.no_client")
            return 0
        cursor = 0
        candidate_shops: set[str] = set()
        pattern = f"hs:trkerr:tot:*:{day}"
        while True:
            cursor, keys = rc.scan(cursor=cursor, match=pattern, count=200)
            for raw_key in keys:
                k = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
                # Key layout: hs:trkerr:tot:{shop}:{date}
                parts = k.split(":")
                if len(parts) >= 5:
                    shop = ":".join(parts[3:-1])  # defensive against colons in shop
                    candidate_shops.add(shop)
            if cursor == 0:
                break
    except Exception as exc:
        log.warning("tracker spike scan redis read failed: %s", exc)
        return 0

    fired = 0
    for shop in candidate_shops:
        try:
            total_raw = rc.get(f"hs:trkerr:tot:{shop}:{day}")
            total = int(total_raw) if total_raw else 0
            distinct = int(rc.scard(f"hs:trkerr:hash:{shop}:{day}") or 0)
        except Exception as exc:
            log.warning("tracker spike read failed shop=%s: %s", shop, exc)
            continue

        crossed = (
            total >= _TRACKER_SPIKE_THRESHOLD_TOTAL_EVENTS
            or distinct >= _TRACKER_SPIKE_THRESHOLD_DISTINCT_HASHES
        )
        if not crossed:
            continue
        key = _TRACKER_SPIKE_COOLDOWN_KEY.format(shop=shop, day=day)
        if not _cooldown_ok(key, _TRACKER_SPIKE_COOLDOWN_TTL):
            continue
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source=f"tracker_runtime_spike:{shop}"[:64],
                alert_type="tracker_runtime_error_spike",
                summary=(
                    f"tracker errors spike on {shop}: "
                    f"{total} events / {distinct} distinct in 24h"
                ),
                detail={
                    "shop_domain": shop,
                    "total_events_24h": total,
                    "distinct_hashes_24h": distinct,
                    "threshold_total": _TRACKER_SPIKE_THRESHOLD_TOTAL_EVENTS,
                    "threshold_distinct": _TRACKER_SPIKE_THRESHOLD_DISTINCT_HASHES,
                },
            )
            fired += 1
        except Exception as exc:
            log.warning("tracker spike alert write failed shop=%s: %s", shop, exc)
    return fired


# ---------------------------------------------------------------------------
# Frontend error spike (dashboard-side)
# ---------------------------------------------------------------------------
# A burst of client-side errors on the merchant dashboard is ALWAYS a
# regression — our dashboard is a small surface with typed API calls.
# Threshold: 10 new frontend_error alerts in the last 15 min.
_FRONTEND_SPIKE_WINDOW_MIN = 15
_FRONTEND_SPIKE_THRESHOLD = 10
_FRONTEND_SPIKE_COOLDOWN_KEY = "hs:spike:frontend_error:{hour}"
_FRONTEND_SPIKE_COOLDOWN_TTL = 3600  # max 1/hour


def detect_frontend_error_spike(db: Session) -> int:
    """Detect dashboard-side error volume spikes. Returns 1 if an alert
    was emitted, 0 otherwise."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=_FRONTEND_SPIKE_WINDOW_MIN)

    try:
        row = db.execute(
            sql_text(
                """
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT source) AS distinct_sources
                FROM ops_alerts
                WHERE alert_type = 'frontend_error'
                  AND created_at >= :cutoff
                """
            ),
            {"cutoff": cutoff},
        ).fetchone()
    except Exception as exc:
        log.warning("frontend spike scan query failed: %s", exc)
        return 0

    if not row or (row[0] or 0) < _FRONTEND_SPIKE_THRESHOLD:
        return 0

    total, distinct_sources = int(row[0] or 0), int(row[1] or 0)
    hour = now.strftime("%Y-%m-%dT%H")
    key = _FRONTEND_SPIKE_COOLDOWN_KEY.format(hour=hour)
    if not _cooldown_ok(key, _FRONTEND_SPIKE_COOLDOWN_TTL):
        return 0

    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="frontend_error_spike",
            alert_type="frontend_error_spike",
            summary=(
                f"dashboard frontend errors spike: {total} events "
                f"across {distinct_sources} distinct sources in "
                f"{_FRONTEND_SPIKE_WINDOW_MIN}min"
            ),
            detail={
                "window_minutes": _FRONTEND_SPIKE_WINDOW_MIN,
                "total_events": total,
                "distinct_sources": distinct_sources,
                "threshold": _FRONTEND_SPIKE_THRESHOLD,
            },
        )
        return 1
    except Exception as exc:
        log.warning("frontend spike alert write failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# p95 latency slow-trend (the "slow death" catcher)
# ---------------------------------------------------------------------------
# Hard-gate at preflight catches p95 > 200ms — but a route whose p95
# crept from 30ms to 120ms over a month is invisible to the hard gate
# and catastrophic cumulatively. This detector compares the last 24h
# to the trailing 7-day baseline (excluding the last 24h).
_P95_WINDOW_HOURS = 24
_P95_BASELINE_DAYS = 7
_P95_DRIFT_RATIO = 1.5  # last 24h p95 >= 1.5× baseline
_P95_MIN_SAMPLES = 50   # don't alert on sparse-traffic routes
_P95_MIN_ABS_MS = 50    # don't alert when everything is <50ms
_P95_COOLDOWN_KEY = "hs:spike:p95_drift:{route}:{day}"
_P95_COOLDOWN_TTL = 86400


def detect_p95_slow_trends(db: Session) -> int:
    """Placeholder — the Redis-based p95 snapshot capture pipeline is
    pending. Until then this detector returns 0 (no-op, no crash) so
    run_all_spike_detectors can invoke it unconditionally.

    See task A4 (observability_spikes p95 snapshot capture) — the
    capture path writes per-route rolling p95 samples to Redis and
    this function will query them. The detector logic (drift ratio,
    minimum sample thresholds, cooldown) is fully specified in the
    constants above — just the data source needs wiring.
    """
    # Acknowledge parameters so type-check / linters don't flag unused.
    _ = (db, _P95_WINDOW_HOURS, _P95_BASELINE_DAYS, _P95_DRIFT_RATIO,
         _P95_MIN_SAMPLES, _P95_MIN_ABS_MS, _P95_COOLDOWN_KEY,
         _P95_COOLDOWN_TTL)
    return 0


# ---------------------------------------------------------------------------
# Unified entrypoint — called from aggregation_worker cycle
# ---------------------------------------------------------------------------


def run_all_spike_detectors(db: Session) -> dict[str, int]:
    """Run every detector and return a count-per-type summary. Each
    detector is wrapped in its own try/except so a failure in one does
    NOT block the others."""
    results: dict[str, int] = {}
    for name, fn in (
        ("tracker_runtime_error_spike", detect_tracker_error_spikes),
        ("frontend_error_spike",        detect_frontend_error_spike),
        ("p95_slow_trend",              detect_p95_slow_trends),
    ):
        try:
            results[name] = fn(db)
        except Exception as exc:
            log.warning("observability_spike %s failed: %s", name, exc)
            results[name] = 0
    return results
