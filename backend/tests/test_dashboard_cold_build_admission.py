"""
test_dashboard_cold_build_admission.py — locks the 4th-tier global
cold-build admission control (born 2026-05-16f).

CONTEXT: the ground-truth load rig MEASURED a real digest-herd /
post-deploy-flush cliff — the stampede lock is PER-SHOP, so N distinct
cold merchants spawn N builders, each pinning a pooled conn ~2s; 800
distinct vs PgBouncer pool=80 → 320 queued → 30s pool_timeout → 41%
500s (cl_waiting=83, broker-proven). The fix caps concurrent cold
builds < pool and sheds the excess to the existing sticky last-known-
good (0 errors, ≤24h-stale). These tests lock the two contract
properties; if a refactor removes the admission gate the storm cliff
returns silently.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import dashboard


def test_over_budget_sheds_to_sticky_and_does_NOT_build(monkeypatch):
    """Budget full + sticky present → return sticky, builder NEVER
    called. This is the entire point: a 41st+ concurrent cold builder
    must not pile a ~2s pooled-conn build onto the saturated pool."""
    monkeypatch.setattr(dashboard, "_acquire_dashboard_lock",
                        lambda k: True)
    monkeypatch.setattr(dashboard, "_cold_build_admit", lambda: None)

    def _cache_get(key):
        return {"ok": "sticky-payload"} if key == "STK" else None

    monkeypatch.setattr("app.core.redis_client.cache_get", _cache_get)

    def _builder():
        raise AssertionError(
            "builder ran while over budget — the admission gate is "
            "bypassed; the 2026-05-16f digest-herd pool cliff is back")

    out = dashboard._serve_dashboard_with_stampede_guard(
        "CK", "STK", "LK", _builder)
    assert out == {"ok": "sticky-payload"}


def test_admitted_path_builds_and_releases_the_slot(monkeypatch):
    """Under budget → build runs AND the slot token is released
    (a leaked slot would shrink the budget permanently)."""
    monkeypatch.setattr(dashboard, "_acquire_dashboard_lock",
                        lambda k: True)
    monkeypatch.setattr(dashboard, "_cold_build_admit", lambda: "tok-1")
    released: list = []
    monkeypatch.setattr(dashboard, "_cold_build_release",
                        lambda t: released.append(t))
    monkeypatch.setattr("app.core.redis_client.cache_get",
                        lambda k: None)

    out = dashboard._serve_dashboard_with_stampede_guard(
        "CK", "STK", "LK", lambda: {"built": True})
    assert out == {"built": True}
    assert released == ["tok-1"], "admitted slot must be released"


def test_release_is_called_even_when_builder_raises(monkeypatch):
    monkeypatch.setattr(dashboard, "_acquire_dashboard_lock",
                        lambda k: True)
    monkeypatch.setattr(dashboard, "_cold_build_admit", lambda: "tok-2")
    released: list = []
    monkeypatch.setattr(dashboard, "_cold_build_release",
                        lambda t: released.append(t))
    # No sticky → the builder exception must propagate, but the slot
    # must still be released (finally), else a crash leaks the budget.
    monkeypatch.setattr("app.core.redis_client.cache_get",
                        lambda k: None)

    def _boom():
        raise RuntimeError("build failed")

    try:
        dashboard._serve_dashboard_with_stampede_guard(
            "CK", "STK", "LK", _boom)
    except RuntimeError:
        pass
    assert released == ["tok-2"], "slot must release even on build error"


def test_redis_down_admission_is_BOUNDED_not_open(monkeypatch):
    """honest-residual #2 close. Redis down MUST NOT degrade OPEN
    (the old 'degraded' admit-all → the 41% pool-timeout cliff
    returned, because sticky is ALSO Redis so the caller fell through
    to an unbounded build). It must bound concurrent cold builds via
    the per-worker semaphore: ≤ budget get a 'local' token, the rest
    get None (shed) — NEVER admit-all."""
    import threading
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    # Fresh isolated semaphore (budget 3) so the assertion is
    # deterministic and does not leak module-global state.
    monkeypatch.setattr(dashboard, "_dashboard_cb_local_sem",
                        threading.BoundedSemaphore(3))

    tokens = [dashboard._cold_build_admit() for _ in range(10)]
    admitted = [t for t in tokens if t is not None]
    shed = [t for t in tokens if t is None]

    assert all(t == "local" for t in admitted)
    assert len(admitted) == 3, (
        f"Redis-down admits must be BOUNDED to the local budget (3), "
        f"got {len(admitted)} — degrade-open regression, the cliff is "
        f"back")
    assert len(shed) == 7, "over-budget admits must shed (None), not open"
    # NEVER the old unbounded sentinel.
    assert "degraded" not in tokens


def test_redis_down_release_refrees_the_local_slot(monkeypatch):
    """A released 'local' slot is re-acquirable — no permanent budget
    shrink under sustained Redis-down load."""
    import threading
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    monkeypatch.setattr(dashboard, "_dashboard_cb_local_sem",
                        threading.BoundedSemaphore(1))

    assert dashboard._cold_build_admit() == "local"      # slot taken
    assert dashboard._cold_build_admit() is None          # full → shed
    dashboard._cold_build_release("local")                # free it
    assert dashboard._cold_build_admit() == "local"       # re-acquirable
    # legacy + None tolerated without raising; over-release is benign.
    dashboard._cold_build_release("degraded")
    dashboard._cold_build_release(None)
    dashboard._cold_build_release("local")
    dashboard._cold_build_release("local")  # double-release guard (no raise)


def test_redis_down_storm_no_sticky_sheds_503_not_unbounded_build(monkeypatch):
    """End-to-end honest-residual #2: Redis down (sticky unavailable —
    sticky IS Redis) + local budget exhausted ⟹ the guard raises a
    fast warming-503 and the builder is NEVER called (an unbounded
    build here is the measured cliff). The SETNX lock is released so
    the shop is not wedged for the lock TTL."""
    import threading
    monkeypatch.setattr("app.core.redis_client._client",
                        lambda: None)                # Redis DOWN
    monkeypatch.setattr(dashboard, "_acquire_dashboard_lock",
                        lambda k: True)              # Redis-down → degrade-open
    monkeypatch.setattr("app.core.redis_client.cache_get",
                        lambda k: None)              # Redis down → no sticky
    monkeypatch.setattr(dashboard, "_wait_for_dashboard_cache",
                        lambda k: None)
    # Local budget already exhausted (semaphore at 0).
    sem = threading.BoundedSemaphore(1)
    sem.acquire()
    monkeypatch.setattr(dashboard, "_dashboard_cb_local_sem", sem)
    released_locks: list = []
    monkeypatch.setattr(dashboard, "_release_dashboard_lock",
                        lambda k: released_locks.append(k))

    def _builder():
        raise AssertionError(
            "builder ran under Redis-down storm with budget exhausted "
            "— the 41% pool-timeout cliff (honest-residual #2) is back")

    with pytest.raises(HTTPException) as ei:
        dashboard._serve_dashboard_with_stampede_guard(
            "CK", "STK", "LK", _builder)
    assert ei.value.status_code == 503
    assert ei.value.headers.get("Retry-After")
    assert released_locks == ["LK"], (
        "the warming-shed must release the SETNX lock so the shop is "
        "not wedged for the 30s lock TTL")


def test_lost_race_no_sticky_sheds_503_not_stampede_build(monkeypatch):
    """Lost the SETNX race, no sticky, holder still building, wait
    expired ⟹ warming-503, NOT our own stampede build (which under a
    storm is the cliff). We do not hold the lock → none released."""
    monkeypatch.setattr(dashboard, "_acquire_dashboard_lock",
                        lambda k: False)             # someone else builds
    monkeypatch.setattr("app.core.redis_client.cache_get",
                        lambda k: None)
    monkeypatch.setattr(dashboard, "_wait_for_dashboard_cache",
                        lambda k: None)

    def _builder():
        raise AssertionError("stampede build ran — the guard is bypassed")

    with pytest.raises(HTTPException) as ei:
        dashboard._serve_dashboard_with_stampede_guard(
            "CK", "STK", "LK", _builder)
    assert ei.value.status_code == 503
