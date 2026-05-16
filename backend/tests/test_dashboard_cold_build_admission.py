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


def test_admit_degrades_open_when_redis_down(monkeypatch):
    """Redis down → admit ('degraded'), same contract as the stampede
    lock: better to build than to wedge every dashboard."""
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    assert dashboard._cold_build_admit() == "degraded"
    # release tolerates the sentinel + None without raising
    dashboard._cold_build_release("degraded")
    dashboard._cold_build_release(None)
