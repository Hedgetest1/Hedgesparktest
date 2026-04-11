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

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Generator


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


# ---------------------------------------------------------------------------
# Global metric instances
# ---------------------------------------------------------------------------

# Request latency by method + path
_request_duration = defaultdict(_Histogram)  # key: (method, path_group)
_request_count = defaultdict(_Counter)       # key: (method, path_group, status)

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
_error_count = defaultdict(_Counter)  # key: error_type

_lock = threading.Lock()


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
    except Exception:
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


# ---------------------------------------------------------------------------
# Prometheus exposition format renderer
# ---------------------------------------------------------------------------

def render_metrics() -> str:
    """Render all metrics in Prometheus text exposition format."""
    lines: list[str] = []

    # Request duration histogram
    lines.append("# HELP hs_request_duration_seconds HTTP request duration")
    lines.append("# TYPE hs_request_duration_seconds histogram")
    for (method, path), hist in sorted(_request_duration.items()):
        snap = hist.snapshot()
        labels = f'method="{method}",path="{path}"'
        for bound, count in snap["buckets"]:
            lines.append(f'hs_request_duration_seconds_bucket{{{labels},le="{bound}"}} {count}')
        lines.append(f'hs_request_duration_seconds_bucket{{{labels},le="+Inf"}} {snap["inf"]}')
        lines.append(f"hs_request_duration_seconds_sum{{{labels}}} {snap['sum']:.6f}")
        lines.append(f"hs_request_duration_seconds_count{{{labels}}} {snap['count']}")

    # Request count by status
    lines.append("# HELP hs_requests_total HTTP requests total")
    lines.append("# TYPE hs_requests_total counter")
    for (method, path, status), counter in sorted(_request_count.items()):
        lines.append(
            f'hs_requests_total{{method="{method}",path="{path}",status="{status}"}} '
            f"{counter.value}"
        )

    # Worker cycle duration
    lines.append("# HELP hs_worker_cycle_seconds Worker cycle duration")
    lines.append("# TYPE hs_worker_cycle_seconds histogram")
    for name, hist in sorted(_worker_duration.items()):
        snap = hist.snapshot()
        for bound, count in snap["buckets"]:
            lines.append(f'hs_worker_cycle_seconds_bucket{{worker="{name}",le="{bound}"}} {count}')
        lines.append(f'hs_worker_cycle_seconds_bucket{{worker="{name}",le="+Inf"}} {snap["inf"]}')
        lines.append(f'hs_worker_cycle_seconds_sum{{worker="{name}"}} {snap["sum"]:.6f}')
        lines.append(f'hs_worker_cycle_seconds_count{{worker="{name}"}} {snap["count"]}')

    # Worker errors
    lines.append("# HELP hs_worker_errors_total Worker cycle errors")
    lines.append("# TYPE hs_worker_errors_total counter")
    for name, counter in sorted(_worker_errors.items()):
        lines.append(f'hs_worker_errors_total{{worker="{name}"}} {counter.value}')

    # DB query duration
    lines.append("# HELP hs_db_query_seconds Database query duration")
    lines.append("# TYPE hs_db_query_seconds histogram")
    snap = _db_duration.snapshot()
    for bound, count in snap["buckets"]:
        lines.append(f'hs_db_query_seconds_bucket{{le="{bound}"}} {count}')
    lines.append(f'hs_db_query_seconds_bucket{{le="+Inf"}} {snap["inf"]}')
    lines.append(f"hs_db_query_seconds_sum {snap['sum']:.6f}")
    lines.append(f"hs_db_query_seconds_count {snap['count']}")

    # Cache
    lines.append("# HELP hs_cache_hits_total Cache hits")
    lines.append("# TYPE hs_cache_hits_total counter")
    lines.append(f"hs_cache_hits_total {_cache_hits.value}")
    lines.append("# HELP hs_cache_misses_total Cache misses")
    lines.append("# TYPE hs_cache_misses_total counter")
    lines.append(f"hs_cache_misses_total {_cache_misses.value}")

    # DB query count
    lines.append("# HELP hs_db_queries_total Database queries total")
    lines.append("# TYPE hs_db_queries_total counter")
    lines.append(f"hs_db_queries_total {_db_count.value}")

    # Error counts
    lines.append("# HELP hs_errors_total Application errors by type")
    lines.append("# TYPE hs_errors_total counter")
    for error_type, counter in sorted(_error_count.items()):
        lines.append(f'hs_errors_total{{type="{error_type}"}} {counter.value}')

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
    except Exception:
        pass  # Pool metrics are best-effort

    return "\n".join(lines) + "\n"
