"""
Tests for detect_slo_breaches — locks the push-alert contract on the SLO
catalogue so a latent availability or p95 breach cannot go unnoticed.

The existing slo_report() surface was only used by ops-pull endpoints
and staged_rollout gating; before 2026-04-18 there was no push alert.
These tests seed synthetic latency/error data into Redis via the SLO
record_timing entry point, invoke the new detector, and assert the
right alert class + severity fires (or does not) against the catalogue.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text


def _count_alerts(db, alert_type: str) -> int:
    return int(db.execute(
        text("SELECT COUNT(*) FROM ops_alerts WHERE alert_type = :t"),
        {"t": alert_type},
    ).scalar() or 0)


@pytest.fixture(autouse=True)
def _clean_slo_redis():
    """Isolate each test's Redis SLO state — prior suites may have left
    observations that would pull real-app health toward insufficient_data
    or healthy and mask the synthetic signal under test.

    Also reset the in-process cooldown set used under APP_ENV=test by
    _cooldown_ok. Redis scan alone is insufficient because the test
    harness keeps cooldowns in memory (see observability_spikes._cooldown_ok
    test-mode branch) and Redis cleanup doesn't touch that set.
    """
    try:
        from app.services.observability_spikes import reset_test_cooldowns
        reset_test_cooldowns()
    except Exception:
        pass
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:slo:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
            # Also flush any hs:spike:slo:* cooldown keys the detector
            # may have written under non-test mode.
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:spike:slo:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass
    yield


def _seed(route: str, method: str, status: int, duration_ms: float, n: int = 1) -> None:
    """Seed an observation. 2026-05-14 batched-flush change: record_timing
    buffers in-process; force a synchronous flush so the immediate
    `detect_slo_breaches` read sees the data."""
    from app.core.slo import record_timing, _flush_buffer
    for _ in range(n):
        record_timing(route, method, status, duration_ms)
    _flush_buffer()


class TestSloBreachDetector:

    def test_latency_breach_fires_critical_slo_breach(self, db):
        """p95 above 1.5× target (latency_breach) → slo_breach critical."""
        # /track has latency_p95_target_ms=200. Seed durations around 500ms
        # → p95 ≈ 500 > 300 (1.5× target) → latency_breach classification.
        # n=35 to clear the obs < 30 insufficient_data gate (bumped from
        # 10 → 30 on 2026-05-13 per Agent audit for p95 stat-significance).
        for _ in range(35):
            _seed("/track", "POST", 200, 500.0)
        from app.services.observability_spikes import detect_slo_breaches
        fired = detect_slo_breaches(db)
        assert fired >= 1
        assert _count_alerts(db, "slo_breach") >= 1

    def test_latency_warning_fires_slo_burn_warning(self, db):
        """p95 above target but below 1.5× target → latency_warning → warning."""
        # /track target 200ms → seed around 250ms (above but not 1.5×).
        for _ in range(35):
            _seed("/track", "POST", 200, 250.0)
        from app.services.observability_spikes import detect_slo_breaches
        fired = detect_slo_breaches(db)
        assert fired >= 1
        assert _count_alerts(db, "slo_burn_warning") >= 1
        assert _count_alerts(db, "slo_breach") == 0

    def test_healthy_route_no_alert(self, db):
        """Fast + all-ok observations → healthy → silent."""
        for _ in range(35):
            _seed("/track", "POST", 200, 50.0)  # well under 200ms target
        from app.services.observability_spikes import detect_slo_breaches
        fired = detect_slo_breaches(db)
        assert fired == 0

    def test_insufficient_data_no_alert(self, db):
        """Fewer than 30 observations → insufficient_data → no alert even
        if the few observations are ugly. Prevents cold-start noise.
        (obs floor bumped 10 → 30 on 2026-05-13 per Agent audit.)"""
        for _ in range(3):
            _seed("/track", "POST", 200, 5000.0)
        from app.services.observability_spikes import detect_slo_breaches
        fired = detect_slo_breaches(db)
        assert fired == 0

    def test_cooldown_deduplicates_within_hour(self, db):
        """Same breach in back-to-back calls should fire exactly once."""
        for _ in range(35):
            _seed("/track", "POST", 200, 500.0)
        from app.services.observability_spikes import detect_slo_breaches
        first = detect_slo_breaches(db)
        second = detect_slo_breaches(db)
        assert first >= 1
        assert second == 0, "cooldown must suppress the second call"
        # Alert count stays at the first call's count.
        assert _count_alerts(db, "slo_breach") == first

    def test_catalogue_route_isolation(self, db):
        """Seeding /track must not create alerts for /webhooks/shopify."""
        for _ in range(35):
            _seed("/track", "POST", 200, 500.0)
        from app.services.observability_spikes import detect_slo_breaches
        detect_slo_breaches(db)
        # /webhooks/shopify has no data → insufficient_data → silent.
        rows = db.execute(
            text("SELECT source FROM ops_alerts WHERE alert_type IN ('slo_breach', 'slo_burn_warning')")
        ).fetchall()
        sources = {r[0] for r in rows}
        assert any("slo:track" in s for s in sources)
        assert not any("slo:webhooks" in s for s in sources)

    def test_slo_report_import_failure_returns_zero(self, db):
        """If slo_report itself raises, detector returns 0 and never raises."""
        with patch(
            "app.core.slo.slo_report", side_effect=RuntimeError("simulated")
        ):
            from app.services.observability_spikes import detect_slo_breaches
            fired = detect_slo_breaches(db)
        assert fired == 0
