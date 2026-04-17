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
    """Scan Redis per-(route, hour) p95 buckets and alert when the
    last-24h median p95 for a route exceeds the prior 7-day median by
    `_P95_DRIFT_RATIO` AND the absolute value is ≥ `_P95_MIN_ABS_MS`.

    Data source: `app/services/p95_snapshot.py` flushes the in-process
    request histograms to Redis every 5 min via the backend middleware.
    Buckets live at `hs:p95:{route}:{hour_iso}` with 8d TTL.

    Returns the number of routes for which a regression alert fired."""
    import json
    import statistics
    from app.services.p95_snapshot import iter_bucket_keys

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_key = now.strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("observability_spikes.p95_scan.no_client")
            return 0
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("observability_spikes.p95_scan.exception")
        return 0

    # Group buckets by route — bucket key layout is hs:p95:{route}:{hour}
    # where route itself may contain colons. Split from the right to
    # isolate the hour segment first.
    per_route_buckets: dict[str, list[tuple[str, dict]]] = {}
    try:
        for key in iter_bucket_keys(rc, pattern="hs:p95:*"):
            # Filter out meta keys like hs:p95:last_flush_ts / hs:p95:flush_lock
            if key in ("hs:p95:last_flush_ts", "hs:p95:flush_lock"):
                continue
            parts = key.split(":")
            if len(parts) < 4:
                continue
            hour = parts[-1]
            route = ":".join(parts[2:-1])
            try:
                raw = rc.get(key)
                if not raw:
                    continue
                bucket = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except Exception:
                continue
            per_route_buckets.setdefault(route, []).append((hour, bucket))
    except Exception as exc:
        log.warning("p95 bucket scan failed: %s", exc)
        return 0

    # For each route, partition buckets into recent-24h vs prior baseline.
    recent_threshold = (now - timedelta(hours=_P95_WINDOW_HOURS)).strftime("%Y-%m-%dT%H")
    baseline_threshold = (now - timedelta(days=_P95_BASELINE_DAYS)).strftime("%Y-%m-%dT%H")

    fired = 0
    for route, buckets in per_route_buckets.items():
        recent = []
        baseline = []
        recent_count = 0
        baseline_count = 0
        for hour, bucket in buckets:
            if hour >= recent_threshold:
                p95 = float(bucket.get("p95_ms") or 0)
                if p95 > 0:
                    recent.append(p95)
                    recent_count += int(bucket.get("count") or 0)
            elif hour >= baseline_threshold:
                p95 = float(bucket.get("p95_ms") or 0)
                if p95 > 0:
                    baseline.append(p95)
                    baseline_count += int(bucket.get("count") or 0)

        if recent_count < _P95_MIN_SAMPLES or baseline_count < _P95_MIN_SAMPLES:
            continue
        if not recent or not baseline:
            continue

        recent_p95 = statistics.median(recent)
        baseline_p95 = statistics.median(baseline)

        if recent_p95 < _P95_MIN_ABS_MS:
            continue
        if baseline_p95 <= 0:
            continue
        ratio = recent_p95 / baseline_p95
        if ratio < _P95_DRIFT_RATIO:
            continue

        key = _P95_COOLDOWN_KEY.format(route=route, day=day_key)
        if not _cooldown_ok(key, _P95_COOLDOWN_TTL):
            continue

        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source=f"p95_drift:{route}"[:64],
                alert_type="p95_slow_trend",
                summary=(
                    f"p95 drift on {route}: "
                    f"{recent_p95:.0f}ms (last 24h, n={recent_count}) vs "
                    f"{baseline_p95:.0f}ms (prior 7d, n={baseline_count}) — "
                    f"{ratio:.2f}× slower"
                ),
                detail={
                    "route": route,
                    "recent_p95_ms": round(recent_p95, 2),
                    "baseline_p95_ms": round(baseline_p95, 2),
                    "ratio": round(ratio, 3),
                    "recent_samples": recent_count,
                    "baseline_samples": baseline_count,
                    "threshold_ratio": _P95_DRIFT_RATIO,
                },
            )
            fired += 1
        except Exception as exc:
            log.warning("p95 drift alert write failed route=%s: %s", route, exc)

    return fired


# ---------------------------------------------------------------------------
# Unified entrypoint — called from aggregation_worker cycle
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UX frustration spike — rage_click + pogo_stick volume per shop
# ---------------------------------------------------------------------------
# Thresholds are intentionally conservative. For a normal shop, a
# single rage_click event is noise (one confused visitor). When a layout
# bug or broken button hits, you see 50+ rage_clicks on the same target
# in a day — THAT is actionable signal.
_UX_FRUSTRATION_RAGE_THRESHOLD = 30   # rage_clicks per shop per day
_UX_FRUSTRATION_POGO_THRESHOLD = 80   # pogo_stick events per shop per day
_UX_FRUSTRATION_COOLDOWN_KEY = "hs:spike:ux_frustration:{shop}:{day}"
_UX_FRUSTRATION_COOLDOWN_TTL = 86400


def detect_ux_frustration_spikes(db: Session) -> int:
    """Scan events last 24h for rage_click + pogo_stick volume per shop.
    Emit `ux_frustration_spike` when a shop crosses either threshold.

    Uses the existing `events` table (tracker-emitted) rather than a
    new Redis sink — event volume for these signals is bounded by the
    tracker's per-page self-limit (1 per page max), so the write load
    is tiny and DB aggregation is cheap."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day = now.strftime("%Y-%m-%d")
    cutoff_ms = int((now - timedelta(hours=24)).timestamp() * 1000)

    try:
        rows = db.execute(
            sql_text(
                """
                SELECT shop_domain,
                       SUM(CASE WHEN event_type = 'rage_click'  THEN 1 ELSE 0 END) AS rage,
                       SUM(CASE WHEN event_type = 'pogo_stick'  THEN 1 ELSE 0 END) AS pogo
                FROM events
                WHERE event_type IN ('rage_click', 'pogo_stick')
                  AND timestamp >= :cutoff_ms
                GROUP BY shop_domain
                HAVING SUM(CASE WHEN event_type = 'rage_click' THEN 1 ELSE 0 END) >= :rage_t
                    OR SUM(CASE WHEN event_type = 'pogo_stick' THEN 1 ELSE 0 END) >= :pogo_t
                """
            ),
            {
                "cutoff_ms": cutoff_ms,
                "rage_t": _UX_FRUSTRATION_RAGE_THRESHOLD,
                "pogo_t": _UX_FRUSTRATION_POGO_THRESHOLD,
            },
        ).fetchall()
    except Exception as exc:
        log.warning("ux frustration spike scan failed: %s", exc)
        return 0

    fired = 0
    for shop, rage, pogo in rows:
        if not shop:
            continue
        rage = int(rage or 0)
        pogo = int(pogo or 0)
        key = _UX_FRUSTRATION_COOLDOWN_KEY.format(shop=shop, day=day)
        if not _cooldown_ok(key, _UX_FRUSTRATION_COOLDOWN_TTL):
            continue
        try:
            from app.services.alerting import write_alert
            dominant = "rage_click" if rage >= pogo else "pogo_stick"
            write_alert(
                db,
                severity="warning",
                source=f"ux_frustration:{shop}"[:64],
                alert_type="ux_frustration_spike",
                summary=(
                    f"UX frustration spike on {shop}: "
                    f"{rage} rage clicks, {pogo} pogo-sticks in 24h "
                    f"(dominant={dominant})"
                ),
                detail={
                    "shop_domain": shop,
                    "rage_clicks_24h": rage,
                    "pogo_sticks_24h": pogo,
                    "dominant_signal": dominant,
                    "threshold_rage": _UX_FRUSTRATION_RAGE_THRESHOLD,
                    "threshold_pogo": _UX_FRUSTRATION_POGO_THRESHOLD,
                },
            )
            fired += 1
        except Exception as exc:
            log.warning("ux frustration spike alert write failed shop=%s: %s", shop, exc)
    return fired


# ---------------------------------------------------------------------------
# Sentry incident rate spike
# ---------------------------------------------------------------------------
# The Sentry → webhook → sentry_triage → bugfix_pipeline pipeline is
# already fully wired (40+ real incidents ingested). But the pipeline
# is PER-INCIDENT: it triages each issue individually and only acts
# when a fingerprint crosses the recurrence threshold. That misses
# the "everything broke at once" scenario — 100 incidents in 10
# minutes across different fingerprints is a big deploy regression,
# not 100 separate issues. This detector emits a single rate-spike
# alert when the 15-min incident rate exceeds 3× the trailing-24h
# baseline (minimum 10 incidents to avoid sparse-traffic noise).
_SENTRY_SPIKE_WINDOW_MIN = 15
_SENTRY_SPIKE_BASELINE_HOURS = 24
_SENTRY_SPIKE_MIN_ABSOLUTE = 10
_SENTRY_SPIKE_RATIO = 3.0
_SENTRY_SPIKE_COOLDOWN_KEY = "hs:spike:sentry_rate:{hour}"
_SENTRY_SPIKE_COOLDOWN_TTL = 3600  # 1/hour max


def detect_sentry_rate_spikes(db: Session) -> int:
    """Scan sentry_incidents last 15 min for volume that spikes above
    the trailing 24h baseline. Catches the "big deploy regression"
    that's invisible to the per-fingerprint triage threshold.

    Alert emitted with severity=critical because a Sentry rate spike
    almost always correlates with a just-deployed change that broke
    multiple paths at once."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hour = now.strftime("%Y-%m-%dT%H")
    recent_cutoff = now - timedelta(minutes=_SENTRY_SPIKE_WINDOW_MIN)
    baseline_cutoff = now - timedelta(hours=_SENTRY_SPIKE_BASELINE_HOURS)

    try:
        row = db.execute(
            sql_text(
                """
                WITH recent AS (
                    SELECT COUNT(*) AS n,
                           COUNT(DISTINCT fingerprint) AS distinct_fp
                    FROM sentry_incidents
                    WHERE created_at >= :recent_cutoff
                ),
                baseline AS (
                    SELECT COUNT(*) AS n
                    FROM sentry_incidents
                    WHERE created_at >= :baseline_cutoff
                      AND created_at <  :recent_cutoff
                )
                SELECT recent.n::int         AS recent_n,
                       recent.distinct_fp::int AS recent_fp,
                       baseline.n::int       AS baseline_n
                FROM recent, baseline
                """
            ),
            {
                "recent_cutoff": recent_cutoff,
                "baseline_cutoff": baseline_cutoff,
            },
        ).fetchone()
    except Exception as exc:
        log.warning("sentry rate spike query failed: %s", exc)
        return 0

    if not row:
        return 0
    recent_n = int(row[0] or 0)
    recent_fp = int(row[1] or 0)
    baseline_n = int(row[2] or 0)

    if recent_n < _SENTRY_SPIKE_MIN_ABSOLUTE:
        return 0

    # Baseline is 24h; window is 15min. Rate-normalize: expected
    # incidents in a 15-min window = baseline_n × (15/1440).
    expected = baseline_n * (_SENTRY_SPIKE_WINDOW_MIN / (_SENTRY_SPIKE_BASELINE_HOURS * 60))
    # Avoid divide-by-zero; if baseline is empty, any 10+ recent incidents
    # is unambiguously a spike.
    if expected > 0 and recent_n / expected < _SENTRY_SPIKE_RATIO:
        return 0

    key = _SENTRY_SPIKE_COOLDOWN_KEY.format(hour=hour)
    if not _cooldown_ok(key, _SENTRY_SPIKE_COOLDOWN_TTL):
        return 0

    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="critical",
            source="sentry_rate_spike",
            alert_type="sentry_incident_rate_spike",
            summary=(
                f"Sentry incident rate spike: {recent_n} incidents / "
                f"{recent_fp} distinct fingerprints in last "
                f"{_SENTRY_SPIKE_WINDOW_MIN}min "
                f"(baseline 24h: {baseline_n})"
            ),
            detail={
                "window_minutes": _SENTRY_SPIKE_WINDOW_MIN,
                "recent_incidents": recent_n,
                "recent_distinct_fingerprints": recent_fp,
                "baseline_24h_incidents": baseline_n,
                "expected_in_window": round(expected, 2),
                "ratio": round(recent_n / expected, 2) if expected > 0 else None,
                "threshold_ratio": _SENTRY_SPIKE_RATIO,
            },
        )
        return 1
    except Exception as exc:
        log.warning("sentry rate spike alert write failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Sentry fingerprint regression — a previously-resolved issue returns
# ---------------------------------------------------------------------------
# When an incident's fingerprint matches one that was linked to a
# `consumed` (= bugfix shipped) candidate, it's a regression — the
# fix failed. This deserves an immediate ops_alert regardless of
# recurrence threshold so ops can rollback.


def detect_sentry_regressions(db: Session) -> int:
    """Emit `sentry_regression` alert when an incident in the last 30
    min matches a fingerprint that has a consumed bugfix candidate.

    `consumed` means the bugfix pipeline took action on this
    fingerprint. If the same fingerprint fires NEW incidents after
    that, the fix didn't hold. This is always critical."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recent_cutoff = now - timedelta(minutes=30)

    try:
        rows = db.execute(
            sql_text(
                """
                SELECT DISTINCT si_new.fingerprint,
                                COUNT(*) AS n,
                                MAX(si_new.id) AS latest_id
                FROM sentry_incidents si_new
                WHERE si_new.created_at >= :cutoff
                  AND si_new.fingerprint IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM sentry_incidents si_old
                      WHERE si_old.fingerprint = si_new.fingerprint
                        AND si_old.ai_triage_status = 'consumed'
                        AND si_old.created_at < si_new.created_at
                  )
                GROUP BY si_new.fingerprint
                """
            ),
            {"cutoff": recent_cutoff},
        ).fetchall()
    except Exception as exc:
        log.warning("sentry regression scan failed: %s", exc)
        return 0

    fired = 0
    for fingerprint, n, latest_id in rows:
        if not fingerprint:
            continue
        # Cooldown per fingerprint per hour
        hour = now.strftime("%Y-%m-%dT%H")
        cooldown_key = f"hs:spike:sentry_regression:{fingerprint}:{hour}"
        if not _cooldown_ok(cooldown_key, 3600):
            continue
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="critical",
                source=f"sentry_regression:{fingerprint[:32]}",
                alert_type="sentry_regression",
                summary=(
                    f"Sentry regression: fingerprint={fingerprint[:20]} "
                    f"returned ({int(n)} new incidents in 30min) after "
                    f"bugfix was marked consumed"
                ),
                detail={
                    "fingerprint": fingerprint,
                    "recent_count_30min": int(n),
                    "latest_incident_id": int(latest_id),
                },
            )
            fired += 1
        except Exception as exc:
            log.warning("sentry regression alert write failed fp=%s: %s", fingerprint, exc)
    return fired


# ---------------------------------------------------------------------------
# Unified entrypoint — called from aggregation_worker cycle
# ---------------------------------------------------------------------------


def run_all_spike_detectors(db: Session) -> dict[str, int]:
    """Run every detector and return a count-per-type summary. Each
    detector is wrapped in its own try/except so a failure in one does
    NOT block the others."""
    results: dict[str, int] = {}
    for name, fn in (
        ("tracker_runtime_error_spike",  detect_tracker_error_spikes),
        ("frontend_error_spike",         detect_frontend_error_spike),
        ("p95_slow_trend",               detect_p95_slow_trends),
        ("ux_frustration_spike",         detect_ux_frustration_spikes),
        ("sentry_incident_rate_spike",   detect_sentry_rate_spikes),
        ("sentry_regression",            detect_sentry_regressions),
    ):
        try:
            results[name] = fn(db)
        except Exception as exc:
            log.warning("observability_spike %s failed: %s", name, exc)
            results[name] = 0
    return results
