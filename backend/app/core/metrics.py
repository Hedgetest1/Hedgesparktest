"""
metrics.py — Lightweight Prometheus-compatible metrics for HedgeSpark.

Provides request latency, worker cycle time, DB query time, cache hit rate,
and error counters without adding a Prometheus client dependency.

Exports a plain-text /metrics endpoint in Prometheus exposition format.
This is intentionally a zero-dependency implementation — no prometheus_client
package required.  If you need histograms or push gateway, upgrade to the
official client later.

Thread-safe: all counters use threading.Lock.

Usage:
    from app.core.metrics import (
        track_request, track_worker_cycle, track_db_query,
        track_cache_hit, track_cache_miss, track_error, render_metrics,
    )

    # In middleware:
    with track_request(method, path, status_code):
        response = await call_next(request)

    # In workers:
    with track_worker_cycle("aggregation_worker"):
        run_cycle()

    # Endpoint:
    @app.get("/metrics")
    def metrics():
        return PlainTextResponse(render_metrics(), media_type="text/plain")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

log = logging.getLogger("metrics")
from collections import defaultdict
from contextlib import contextmanager
from typing import Generator

# ---------------------------------------------------------------------------
# Multi-worker fleet aggregation (2026-04-23)
#
# Under uvicorn --workers 4, each worker has its own module-level counters.
# A single /metrics scrape returns one worker's view — Prometheus sees 1/N
# of real fleet traffic unless we aggregate.
#
# Approach: each worker runs a daemon thread (see `start_background_pusher`,
# launched from the FastAPI lifespan) that force-pushes the local snapshot
# to Redis every 30s with a 60s TTL — guarantees every alive worker is in
# the aggregate within 30s of startup regardless of traffic. `render_metrics`
# ALSO force-pushes on scrape so the worker handling the scrape contributes
# zero-staleness data. The renderer reads all worker snapshots and sums them
# before emitting Prometheus text. A worker whose bg pusher has been dead
# for 60s decays from the aggregate — which is the signal the invariant
# monitor uses to detect a silently-crashed worker.
#
# multi-worker: redis-backed — aggregation via Redis scan+merge
# ---------------------------------------------------------------------------
_WORKER_PID = str(os.getpid())
_METRICS_REDIS_PREFIX = "hs:metrics:worker"
_METRICS_TTL_S = 60
_METRICS_PUSH_MIN_INTERVAL_S = 5.0  # throttle pushes to avoid Redis spam
_METRICS_BG_PUSH_INTERVAL_S = 30.0  # background keepalive (< TTL/2)
_last_push_ts: float = 0.0
_push_lock = threading.Lock()  # multi-worker: accept-degrade — per-process push throttle
_bg_pusher_started = False  # multi-worker: per-process — each worker starts its own pusher


class _Counter:
    """Thread-safe counter."""
    def __init__(self):
        self._value: float = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0):
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class _Histogram:
    """
    Lightweight histogram with fixed buckets.
    Tracks count and sum for Prometheus-compatible output.
    """
    def __init__(self, buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)):
        self._buckets = buckets
        self._bucket_counts: list[int] = [0] * len(buckets)
        self._count = 0
        self._sum = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float):
        with self._lock:
            self._count += 1
            self._sum += value
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._bucket_counts[i] += 1

    def snapshot(self) -> dict:
        with self._lock:
            cumulative = 0
            buckets = []
            for i, bound in enumerate(self._buckets):
                cumulative += self._bucket_counts[i]
                buckets.append((bound, cumulative))
            return {
                "buckets": buckets,
                "count": self._count,
                "sum": self._sum,
                "inf": self._count,
            }

    def percentile(self, pct: float) -> float | None:
        """Estimate the `pct` percentile (0.0-1.0) from bucket counts.

        Uses linear interpolation inside the bucket that contains the
        target rank — same approach as Prometheus histogram_quantile.
        Returns None when there are no samples. Upper-bound capped at
        the largest defined bucket boundary (i.e. sub-Infinity)."""
        with self._lock:
            total = self._count
            if total <= 0:
                return None
            target = total * pct
            cumulative = 0
            prev_bound = 0.0
            for i, bound in enumerate(self._buckets):
                cumulative += self._bucket_counts[i]
                if cumulative >= target:
                    # Linear interpolation inside the bucket.
                    bucket_count = self._bucket_counts[i]
                    if bucket_count <= 0:
                        return float(bound)
                    # Rank inside this bucket (0.0-1.0 of its count).
                    rank_in_bucket = (target - (cumulative - bucket_count)) / bucket_count
                    return float(prev_bound + rank_in_bucket * (bound - prev_bound))
                prev_bound = float(bound)
            # Beyond the largest bucket — cap at its upper bound.
            return float(self._buckets[-1])


# ---------------------------------------------------------------------------
# Global metric instances
# ---------------------------------------------------------------------------

# multi-worker: accept-degrade — metrics are per-process for monitoring
# visibility only. /metrics endpoint returns 1/N of fleet traffic under
# N uvicorn workers; aggregation across workers lives in the roadmap
# (Prometheus push-gateway or Redis-based collector).
_request_duration = defaultdict(_Histogram)  # key: (method, path_group)
_request_count = defaultdict(_Counter)       # multi-worker: accept-degrade — per-process counter

# Worker cycle duration
_worker_duration = defaultdict(lambda: _Histogram(
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
))
_worker_errors = defaultdict(_Counter)

# DB query duration
_db_duration = _Histogram(buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0))
_db_count = _Counter()

# Cache
_cache_hits = _Counter()
_cache_misses = _Counter()

# Errors
_error_count = defaultdict(_Counter)  # multi-worker: accept-degrade — per-process counter

_lock = threading.Lock()  # multi-worker: accept-degrade — protects per-process metrics only


# ---------------------------------------------------------------------------
# Path grouping — collapse dynamic segments to reduce cardinality
# ---------------------------------------------------------------------------

def _group_path(path: str) -> str:
    """Collapse path parameters to reduce metric cardinality."""
    parts = path.strip("/").split("/")
    grouped = []
    for i, part in enumerate(parts):
        # Collapse UUIDs and numeric IDs
        if len(part) > 8 and any(c.isdigit() for c in part):
            grouped.append(":id")
        elif part.isdigit():
            grouped.append(":id")
        else:
            grouped.append(part)
    return "/" + "/".join(grouped[:3])  # max 3 segments


# ---------------------------------------------------------------------------
# Public tracking API
# ---------------------------------------------------------------------------

@contextmanager
def track_request(method: str, path: str) -> Generator[dict, None, None]:
    """
    Track request latency.  Usage:

        ctx = {}
        with track_request("GET", "/dashboard/overview") as ctx:
            response = process_request()
            ctx["status"] = response.status_code
    """
    ctx: dict = {"status": 200}
    start = time.monotonic()
    try:
        yield ctx
    finally:
        duration = time.monotonic() - start
        group = _group_path(path)
        _request_duration[(method, group)].observe(duration)
        _request_count[(method, group, ctx.get("status", 500))].inc()


@contextmanager
def track_worker_cycle(worker_name: str) -> Generator[None, None, None]:
    """Track worker cycle duration and errors."""
    start = time.monotonic()
    try:
        yield
    except Exception as exc:
        log.warning("metrics: worker cycle error for %s: %s", worker_name, exc)
        _worker_errors[worker_name].inc()
        raise
    finally:
        duration = time.monotonic() - start
        _worker_duration[worker_name].observe(duration)


def track_db_query(duration: float):
    """Record a DB query duration."""
    _db_duration.observe(duration)
    _db_count.inc()


def track_cache_hit():
    _cache_hits.inc()


def track_cache_miss():
    _cache_misses.inc()


def track_error(error_type: str):
    _error_count[error_type].inc()


def compute_p95_per_route() -> dict[str, dict]:
    """Return a dict of {path_group: {p95_ms, count}} across all recorded
    request histograms. Used by the p95 snapshot flusher to write
    per-(route, hour) rolling samples into Redis for slow-trend detection.

    Values are in MILLISECONDS (the internal histogram stores seconds)."""
    out: dict[str, dict] = {}
    # Merge across methods per path — a route's p95 is what users feel
    # regardless of GET vs POST. Most of our paths are single-method anyway.
    per_path: dict[str, _Histogram] = {}
    for (method, path), hist in list(_request_duration.items()):
        # Merge: create a synthetic histogram with combined bucket counts.
        existing = per_path.get(path)
        if existing is None:
            per_path[path] = hist
        else:
            # We can't merge the two histogram instances in place (their
            # internal locks are independent). Instead, emit the larger
            # one — p95 of a route dominated by one method is close enough.
            if hist._count > existing._count:  # type: ignore[attr-defined]
                per_path[path] = hist
    for path, hist in per_path.items():
        p95_seconds = hist.percentile(0.95)
        if p95_seconds is None:
            continue
        out[path] = {
            "p95_ms": round(p95_seconds * 1000.0, 2),
            "count": hist._count,  # type: ignore[attr-defined]
        }
    return out


# ---------------------------------------------------------------------------
# Prometheus exposition format renderer
# ---------------------------------------------------------------------------

def _local_snapshot() -> dict:
    """Serialize this worker's local counter + histogram state."""
    return {
        "pid": _WORKER_PID,
        "ts": time.time(),
        "request_count": {f"{k[0]}\t{k[1]}\t{k[2]}": v.value
                          for k, v in _request_count.items()},
        "request_duration": {f"{k[0]}\t{k[1]}": v.snapshot()
                             for k, v in _request_duration.items()},
        "worker_duration": {k: v.snapshot() for k, v in _worker_duration.items()},
        "worker_errors": {k: v.value for k, v in _worker_errors.items()},
        "db_duration": _db_duration.snapshot(),
        "db_count": _db_count.value,
        "cache_hits": _cache_hits.value,
        "cache_misses": _cache_misses.value,
        "error_count": {k: v.value for k, v in _error_count.items()},
    }


def _push_snapshot_to_redis(force: bool = False) -> None:
    """Push local snapshot to Redis, throttled to once per _METRICS_PUSH_MIN_INTERVAL_S."""
    global _last_push_ts
    now = time.monotonic()
    with _push_lock:
        if not force and (now - _last_push_ts) < _METRICS_PUSH_MIN_INTERVAL_S:
            return
        _last_push_ts = now
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("metrics.push_snapshot.no_redis")
            return
        rc.setex(
            f"{_METRICS_REDIS_PREFIX}:{_WORKER_PID}",
            _METRICS_TTL_S,
            json.dumps(_local_snapshot(), default=str),
        )
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("metrics.push_snapshot.redis_error")


def start_background_pusher() -> None:
    """Start a daemon thread that keeps this worker's snapshot fresh in Redis.

    Without this, idle workers never push — a fleet under no traffic looks
    like a 1-worker fleet to /metrics readers. Called once per process from
    the FastAPI lifespan startup hook. Idempotent.
    """
    global _bg_pusher_started
    if _bg_pusher_started:
        return
    _bg_pusher_started = True

    def _loop() -> None:
        # First push immediately so the worker appears in the aggregate
        # before the first _METRICS_BG_PUSH_INTERVAL_S elapses.
        while True:
            try:
                _push_snapshot_to_redis(force=True)
            except Exception as exc:
                # Never allow the pusher thread to die — that would silently
                # drop this worker from the fleet gauge forever. Log so the
                # failure mode is visible instead of merely survived.
                log.warning("metrics bg pusher: push failed: %s", exc)
            time.sleep(_METRICS_BG_PUSH_INTERVAL_S)

    t = threading.Thread(
        target=_loop,
        name=f"metrics-bg-pusher-{_WORKER_PID}",
        daemon=True,
    )
    t.start()


def _read_fleet_snapshots() -> list[dict]:
    """Read all worker snapshots from Redis (active in last 60s)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("metrics.read_fleet.no_redis")
            return []
        snaps: list[dict] = []
        for key in rc.scan_iter(match=f"{_METRICS_REDIS_PREFIX}:*", count=50):
            raw = rc.get(key)
            if raw:
                try:
                    snaps.append(json.loads(raw))
                except Exception:
                    continue  # SILENT-EXCEPT-OK: one corrupt snapshot ignored, others still merged
        return snaps
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("metrics.read_fleet.redis_error")
        return []


def _merge_counters(snaps: list[dict], key: str) -> dict:
    """Sum counter values across worker snapshots."""
    out: dict[str, float] = {}
    for s in snaps:
        for k, v in (s.get(key) or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def _merge_histograms(snaps: list[dict], key: str) -> dict:
    """Merge histogram snapshots element-wise across workers."""
    out: dict[str, dict] = {}
    for s in snaps:
        for k, h in (s.get(key) or {}).items():
            agg = out.setdefault(k, {"count": 0, "sum": 0.0, "buckets": None, "inf": 0})
            agg["count"] += h.get("count", 0)
            agg["sum"] += h.get("sum", 0)
            agg["inf"] += h.get("inf", 0)
            buckets = h.get("buckets") or []
            if agg["buckets"] is None and buckets:
                agg["buckets"] = [[b[0], b[1]] for b in buckets]
            elif buckets and agg["buckets"] and len(agg["buckets"]) == len(buckets):
                for i, (_, cnt) in enumerate(buckets):
                    agg["buckets"][i][1] += cnt
    return out


def _merge_single_histogram(snaps: list[dict], key: str) -> dict:
    """Merge a single (non-keyed) histogram across workers."""
    agg = {"count": 0, "sum": 0.0, "buckets": None, "inf": 0}
    for s in snaps:
        h = s.get(key) or {}
        agg["count"] += h.get("count", 0)
        agg["sum"] += h.get("sum", 0)
        agg["inf"] += h.get("inf", 0)
        buckets = h.get("buckets") or []
        if agg["buckets"] is None and buckets:
            agg["buckets"] = [[b[0], b[1]] for b in buckets]
        elif buckets and agg["buckets"] and len(agg["buckets"]) == len(buckets):
            for i, (_, cnt) in enumerate(buckets):
                agg["buckets"][i][1] += cnt
    return agg


def _sum_scalar(snaps: list[dict], key: str) -> float:
    total = 0.0
    for s in snaps:
        v = s.get(key, 0)
        if isinstance(v, (int, float)):
            total += v
    return total


def render_metrics() -> str:
    """Render all metrics in Prometheus text exposition format.

    Fleet-aggregated across uvicorn workers via Redis. Local counters
    are pushed on each render (throttled) and read back merged with
    every other worker's recent snapshot.
    """
    # Push THIS worker's snapshot first (force=True so the scrape sees fresh data).
    _push_snapshot_to_redis(force=True)
    snaps = _read_fleet_snapshots()

    lines: list[str] = []

    # Fleet size line — operator visibility into how many workers are reporting.
    lines.append("# HELP hs_fleet_workers_reporting Uvicorn workers reporting to /metrics in last 60s")
    lines.append("# TYPE hs_fleet_workers_reporting gauge")
    lines.append(f"hs_fleet_workers_reporting {len(snaps)}")

    # If Redis is down or no snapshots, fall through to local-only rendering
    # (preserves single-worker + Redis-outage behaviour).
    if not snaps:
        snaps = [_local_snapshot()]

    merged_req_count = _merge_counters(snaps, "request_count")
    merged_req_duration = _merge_histograms(snaps, "request_duration")
    merged_worker_dur = _merge_histograms(snaps, "worker_duration")
    merged_worker_err = _merge_counters(snaps, "worker_errors")
    merged_err_count = _merge_counters(snaps, "error_count")
    merged_db_hist = _merge_single_histogram(snaps, "db_duration")
    merged_db_count = _sum_scalar(snaps, "db_count")
    merged_cache_hits = _sum_scalar(snaps, "cache_hits")
    merged_cache_misses = _sum_scalar(snaps, "cache_misses")

    # Request duration histogram (fleet-aggregated)
    lines.append("# HELP hs_request_duration_seconds HTTP request duration (fleet)")
    lines.append("# TYPE hs_request_duration_seconds histogram")
    for k in sorted(merged_req_duration):
        method, path = k.split("\t", 1)
        agg = merged_req_duration[k]
        labels = f'method="{method}",path="{path}"'
        for bound, count in (agg.get("buckets") or []):
            lines.append(f'hs_request_duration_seconds_bucket{{{labels},le="{bound}"}} {count}')
        lines.append(f'hs_request_duration_seconds_bucket{{{labels},le="+Inf"}} {agg["inf"]}')
        lines.append(f"hs_request_duration_seconds_sum{{{labels}}} {agg['sum']:.6f}")
        lines.append(f"hs_request_duration_seconds_count{{{labels}}} {agg['count']}")

    # Request count by status (fleet-aggregated)
    lines.append("# HELP hs_requests_total HTTP requests total (fleet)")
    lines.append("# TYPE hs_requests_total counter")
    for k in sorted(merged_req_count):
        parts = k.split("\t")
        if len(parts) != 3:
            continue
        method, path, status = parts
        lines.append(
            f'hs_requests_total{{method="{method}",path="{path}",status="{status}"}} '
            f"{merged_req_count[k]}"
        )

    # Worker cycle duration (fleet-aggregated — but note workers are singletons,
    # so this is effectively identical to local, just via Redis round-trip)
    lines.append("# HELP hs_worker_cycle_seconds Worker cycle duration")
    lines.append("# TYPE hs_worker_cycle_seconds histogram")
    for name in sorted(merged_worker_dur):
        agg = merged_worker_dur[name]
        for bound, count in (agg.get("buckets") or []):
            lines.append(f'hs_worker_cycle_seconds_bucket{{worker="{name}",le="{bound}"}} {count}')
        lines.append(f'hs_worker_cycle_seconds_bucket{{worker="{name}",le="+Inf"}} {agg["inf"]}')
        lines.append(f'hs_worker_cycle_seconds_sum{{worker="{name}"}} {agg["sum"]:.6f}')
        lines.append(f'hs_worker_cycle_seconds_count{{worker="{name}"}} {agg["count"]}')

    # Worker errors
    lines.append("# HELP hs_worker_errors_total Worker cycle errors")
    lines.append("# TYPE hs_worker_errors_total counter")
    for name in sorted(merged_worker_err):
        lines.append(f'hs_worker_errors_total{{worker="{name}"}} {merged_worker_err[name]}')

    # DB query duration (fleet-aggregated)
    lines.append("# HELP hs_db_query_seconds Database query duration (fleet)")
    lines.append("# TYPE hs_db_query_seconds histogram")
    for bound, count in (merged_db_hist.get("buckets") or []):
        lines.append(f'hs_db_query_seconds_bucket{{le="{bound}"}} {count}')
    lines.append(f'hs_db_query_seconds_bucket{{le="+Inf"}} {merged_db_hist["inf"]}')
    lines.append(f"hs_db_query_seconds_sum {merged_db_hist['sum']:.6f}")
    lines.append(f"hs_db_query_seconds_count {merged_db_hist['count']}")

    # Cache (fleet-aggregated)
    lines.append("# HELP hs_cache_hits_total Cache hits (fleet)")
    lines.append("# TYPE hs_cache_hits_total counter")
    lines.append(f"hs_cache_hits_total {merged_cache_hits}")
    lines.append("# HELP hs_cache_misses_total Cache misses (fleet)")
    lines.append("# TYPE hs_cache_misses_total counter")
    lines.append(f"hs_cache_misses_total {merged_cache_misses}")

    # DB query count (fleet-aggregated)
    lines.append("# HELP hs_db_queries_total Database queries total (fleet)")
    lines.append("# TYPE hs_db_queries_total counter")
    lines.append(f"hs_db_queries_total {merged_db_count}")

    # Error counts (fleet-aggregated)
    lines.append("# HELP hs_errors_total Application errors by type (fleet)")
    lines.append("# TYPE hs_errors_total counter")
    for error_type in sorted(merged_err_count):
        lines.append(f'hs_errors_total{{type="{error_type}"}} {merged_err_count[error_type]}')

    # DB connection pool gauges
    try:
        from app.core.database import engine
        pool = engine.pool
        lines.append("# HELP hs_db_pool_size Configured pool size")
        lines.append("# TYPE hs_db_pool_size gauge")
        lines.append(f"hs_db_pool_size {pool.size()}")
        lines.append("# HELP hs_db_pool_checkedout Connections currently checked out")
        lines.append("# TYPE hs_db_pool_checkedout gauge")
        lines.append(f"hs_db_pool_checkedout {pool.checkedout()}")
        lines.append("# HELP hs_db_pool_overflow Current overflow connections")
        lines.append("# TYPE hs_db_pool_overflow gauge")
        lines.append(f"hs_db_pool_overflow {pool.overflow()}")
        lines.append("# HELP hs_db_pool_checkedin Idle connections in pool")
        lines.append("# TYPE hs_db_pool_checkedin gauge")
        lines.append(f"hs_db_pool_checkedin {pool.checkedin()}")
    except Exception as exc:
        log.warning("metrics: db pool metrics collection failed: %s", exc)

    return "\n".join(lines) + "\n"
