"""Lock the 2026-05-13 p95_slow_trend heal-detection + MIN_SAMPLES bump.

Bug class: /live/visitors fired 22.8× drift (975ms vs 43ms) on n=50
samples, dominated by ~5 cold-start outliers from 8 PM2 auto-deploy
reloads. The 50-sample threshold permitted single-request noise to
dominate per-hour bucket medians. Bumped to 100. Heal-detection added
so prior alerts auto-resolve when ratio falls back below threshold.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import text as _sql_text

from app.services import observability_spikes
from app.services.alerting import write_alert


def test_min_samples_bumped_to_100():
    """Threshold floor for sample count is now 100 not 50."""
    assert observability_spikes._P95_MIN_SAMPLES == 100


def test_heal_fires_even_below_min_samples(db, monkeypatch):
    """Born 2026-05-13 (Agent audit). The heal logic was previously
    gated by _P95_MIN_SAMPLES floor — meaning a route whose traffic
    dropped below the floor (e.g. after a MIN_SAMPLES bump) would
    have its stale alert persist forever. The heal must fire when
    samples are insufficient too — premise: low traffic + stale
    alert = no regression we can verify, drop the alert."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    route = "/test/p95/low-traffic-route"
    source = f"p95_drift:{route}"

    write_alert(
        db,
        severity="warning",
        source=source,
        alert_type="p95_slow_trend",
        summary="stale alert from when route had traffic",
        detail={"route": route, "ratio": 5.0},
    )
    db.flush()
    assert db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE source=:s AND resolved=false"
    ), {"s": source}).scalar() >= 1

    # Mock Redis: route has TOO FEW samples (50, below floor 100)
    class FakeRC:
        def get(self, key):
            return json.dumps({"p95_ms": 500.0, "count": 50}).encode()

    def fake_iter(rc, pattern="hs:p95:*"):
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        recent_hour = now.strftime("%Y-%m-%dT%H")
        baseline_hour = (now - _dt.timedelta(days=2)).strftime("%Y-%m-%dT%H")
        yield f"hs:p95:{route}:{recent_hour}"
        yield f"hs:p95:{route}:{baseline_hour}"

    with patch("app.core.redis_client._client", lambda: FakeRC()), \
         patch("app.services.p95_snapshot.iter_bucket_keys", fake_iter):
        observability_spikes.detect_p95_slow_trends(db)
    db.flush()

    after = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE source=:s AND resolved=false"
    ), {"s": source}).scalar()
    assert after == 0, (
        "heal must fire even when sample count < MIN_SAMPLES — "
        "low traffic can't verify regression, drop the stale alert"
    )


def test_heal_auto_resolves_when_ratio_falls_below_threshold(db, monkeypatch):
    """A route that previously alerted but is now back to healthy
    ratio should auto-resolve the prior alert in same cycle."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    route = "/test/p95/heal-route"
    source = f"p95_drift:{route}"

    # Seed an unresolved alert for the route
    write_alert(
        db,
        severity="warning",
        source=source,
        alert_type="p95_slow_trend",
        summary="prior alert that should heal",
        detail={"route": route, "ratio": 5.0},
    )
    db.flush()
    unresolved_before = db.execute(
        _sql_text("SELECT COUNT(*) FROM ops_alerts WHERE source=:s AND resolved=false"),
        {"s": source},
    ).scalar()
    assert unresolved_before >= 1

    # Mock Redis buckets: route has 200 samples baseline + recent, both
    # showing 100ms p95 — ratio = 1.0 < threshold (1.5) → heal fires.
    class FakeRC:
        def get(self, key):
            # All buckets report p95=100ms, count=100
            return json.dumps({"p95_ms": 100.0, "count": 100}).encode()

    def fake_iter(rc, pattern="hs:p95:*"):
        # Yield a recent bucket and a baseline bucket so MIN_SAMPLES is met
        # 100 + 100 = 200 samples total in each window
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        recent_hour = now.strftime("%Y-%m-%dT%H")
        baseline_hour = (now - _dt.timedelta(days=2)).strftime("%Y-%m-%dT%H")
        yield f"hs:p95:{route}:{recent_hour}"
        yield f"hs:p95:{route}:{baseline_hour}"

    with patch("app.core.redis_client._client", lambda: FakeRC()), \
         patch("app.services.p95_snapshot.iter_bucket_keys", fake_iter):
        observability_spikes.detect_p95_slow_trends(db)
    db.flush()

    # Heal should have fired: prior alert resolved.
    unresolved_after = db.execute(
        _sql_text("SELECT COUNT(*) FROM ops_alerts WHERE source=:s AND resolved=false"),
        {"s": source},
    ).scalar()
    assert unresolved_after == 0, (
        "heal must auto-resolve prior unresolved p95_slow_trend alert "
        "when ratio falls below threshold"
    )
