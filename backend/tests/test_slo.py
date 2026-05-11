"""SLO observability tests."""
from __future__ import annotations

from unittest.mock import patch

from app.core import slo


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.counters = {}
    def pipeline(self):
        return FakePipeline(self)
    def zrange(self, key, start, stop):
        return list(self.store.get(key, {}).keys())
    def get(self, key):
        v = self.counters.get(key, 0)
        return str(v).encode()

class FakePipeline:
    def __init__(self, rc):
        self.rc = rc
        self.ops = []
    def zadd(self, key, mapping):
        store = self.rc.store.setdefault(key, {})
        store.update(mapping)
        return self
    def zremrangebyscore(self, key, lo, hi):
        return self
    def expire(self, key, ttl):
        return self
    def incr(self, key):
        self.rc.counters[key] = self.rc.counters.get(key, 0) + 1
        return self
    def execute(self):
        return self.ops


def test_quantile_edge_cases():
    assert slo._quantile([], 0.95) == 0.0
    assert slo._quantile([100.0], 0.95) == 100.0
    # Monotonic values
    values = [float(i) for i in range(1, 101)]
    assert slo._quantile(values, 0.5) == 50.5
    assert slo._quantile(values, 0.95) == 95.05


def test_record_timing_never_raises_without_redis():
    with patch("app.core.slo._redis", return_value=None):
        slo.record_timing("/test", "GET", 200, 42.5)  # should not raise


def test_route_stats_uses_redis():
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for i in range(10):
            slo.record_timing("/pro/rars", "GET", 200, 100 + i * 10)
        stats = slo.route_stats("/pro/rars", "GET", "5m")

    assert stats["observations"] >= 1
    assert stats["p50_ms"] > 0
    assert stats["p95_ms"] > 0


def test_slo_report_classifies_insufficient_data():
    """With no observations, every SLO should be insufficient_data."""
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        report = slo.slo_report()
    for entry in report:
        assert entry["health"] == "insufficient_data"
        assert entry["observations"] == 0


def test_slo_report_demotes_latency_breach_when_obs_under_30():
    """2026-05-11 fix: low-obs p95 is dominated by single outliers (a
    single 1000ms request among 19 fast ones makes p95=1000ms). For
    sample sizes < 30, demote any latency_breach to latency_warning
    so the downstream `detect_slo_breaches` does NOT fire a CRITICAL
    alert for what is statistical noise. Availability + burn-rate
    classifications are unaffected (they're per-request, not
    percentile-based)."""
    # Build a fake Redis with 20 observations: 18 fast (10ms) + 2 slow
    # (1500ms) for /orders/summary. Target p95 = 600ms; with 2 slow
    # values at indices 18+19 of sorted, p95 (95th percentile via
    # linear interp at index 0.95*19=18.05) interpolates between 18
    # (=1500) and 19 (=1500) → 1500ms > 600*1.5=900ms → would be
    # latency_breach with obs>=30, but should be demoted to
    # latency_warning at obs<30.
    fake = FakeRedis()
    route = "/orders/summary"
    method = "GET"
    key_tm = f"hs:slo:tm:60m:{method}:{route}"
    bucket = {}
    base_ns = 1_000_000_000_000
    for i in range(18):
        bucket[f"{base_ns + i}:10.0"] = base_ns + i
    for j, ns_off in enumerate([18, 19]):
        bucket[f"{base_ns + ns_off}:1500.0"] = base_ns + ns_off
    fake.store[key_tm] = bucket
    fake.counters[f"hs:slo:ok:60m:{method}:{route}"] = 20
    fake.counters[f"hs:slo:err:60m:{method}:{route}"] = 0

    with patch("app.core.slo._redis", return_value=fake):
        report = slo.slo_report()
    target = next(e for e in report if e["route"] == route)
    assert target["observations"] == 20
    assert target["p95_ms"] > 600 * 1.5  # would-be latency_breach
    # KEY assertion: with obs<30, demote to warning, NOT breach
    assert target["health"] == "latency_warning"


def test_slo_report_keeps_latency_breach_when_obs_at_or_above_30():
    """Counterpart: at obs>=30, the latency_breach classification
    stands (high-traffic routes have statistically meaningful p95)."""
    fake = FakeRedis()
    route = "/orders/summary"
    method = "GET"
    key_tm = f"hs:slo:tm:60m:{method}:{route}"
    bucket = {}
    base_ns = 1_000_000_000_000
    # 35 observations: 32 fast + 3 slow. p95 at 0.95*34=32.3 → indices
    # 32-33 are slow (1500ms) → p95 ≈ 1500 > 900 → latency_breach.
    for i in range(32):
        bucket[f"{base_ns + i}:10.0"] = base_ns + i
    for off in [32, 33, 34]:
        bucket[f"{base_ns + off}:1500.0"] = base_ns + off
    fake.store[key_tm] = bucket
    fake.counters[f"hs:slo:ok:60m:{method}:{route}"] = 35
    fake.counters[f"hs:slo:err:60m:{method}:{route}"] = 0

    with patch("app.core.slo._redis", return_value=fake):
        report = slo.slo_report()
    target = next(e for e in report if e["route"] == route)
    assert target["observations"] == 35
    # p95 of 35 values with one at 1500 → p95 is ~1500
    assert target["p95_ms"] > 600 * 1.5
    assert target["health"] == "latency_breach"


def test_catalogue_routes_resolve_to_real_handlers():
    """C-2 preventer (2026-05-06): every SLO route must correspond to a
    real FastAPI handler. A typo in the catalogue would silently
    produce 'insufficient_data' forever (no observations on a
    non-existent route) — masking SLO drift on the actual route.

    Loaded from app.main to walk the full router tree."""
    from app.main import app
    registered = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and methods:
            for m in methods:
                if m in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    registered.add((path, m))

    missing: list[tuple[str, str, str]] = []
    for entry in slo.CATALOGUE:
        if (entry.route, entry.method) not in registered:
            missing.append((entry.name, entry.route, entry.method))
    assert not missing, (
        f"SLO catalogue references {len(missing)} unregistered route(s) — "
        f"typo or removed handler? {missing}"
    )


def test_record_timing_identical_duration_same_ms_does_not_coalesce():
    """Regression: 20 observations with identical duration fired in a
    tight loop must produce 20 distinct ZSET members, not 1.

    Prior implementation used `{now_ms}:{duration_ms}` as the member
    key, collapsing observations within the same millisecond into one
    entry. Under production load (10k+ merchants) or a hot-loop test,
    this trips the `obs < 10 → insufficient_data` gate in slo_report
    and suppresses legitimate breach alerts.

    Fix uses nanosecond precision on the member side (score stays ms
    for cheap zremrangebyscore windowing).
    """
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for _ in range(20):
            slo.record_timing("/hot", "POST", 200, 500.0)
    # Both windows should have 20 distinct members.
    for window in ("5m", "60m"):
        key = f"hs:slo:tm:{window}:POST:/hot"
        members = fake.store.get(key, {})
        assert len(members) == 20, (
            f"window={window} coalesced {20 - len(members)} observations; "
            f"members={list(members.keys())[:3]}..."
        )


def test_route_stats_parses_ns_prefixed_members():
    """route_stats must correctly extract duration from the
    nanosecond-prefixed member format `{ns}:{duration}`."""
    fake = FakeRedis()
    with patch("app.core.slo._redis", return_value=fake):
        for _ in range(15):
            slo.record_timing("/parse-check", "GET", 200, 300.0)
        stats = slo.route_stats("/parse-check", "GET", "5m")
    assert stats["observations"] == 15
    assert stats["p95_ms"] == 300.0
    assert stats["p50_ms"] == 300.0
