"""
Tests for detect_perf_network_layer_drift — the C-6 correlation
detector that catches edge-layer perf regressions by cross-referencing
rum_regression × lighthouse_regression_public alerts on the same route
within a 10-minute window.

Locks the contract that:
- Both source alerts present on same route → one diagnostic alert fires
- Only one source present → no diagnostic
- Different routes → no correlation (isolated)
- Cooldown dedups within the hour
- Unrelated alert types are ignored
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
def _reset_cooldowns():
    from app.services.observability_spikes import reset_test_cooldowns
    reset_test_cooldowns()
    yield


def _seed_alert(db, alert_type: str, route: str, source_suffix: str = ""):
    from app.services.alerting import write_alert
    write_alert(
        db,
        severity="warning",
        source=(
            f"lighthouse:public:{route}" if alert_type == "lighthouse_regression_public"
            else f"rum:{route}"
        ) + source_suffix,
        alert_type=alert_type,
        summary=f"{alert_type} on {route}",
        detail={"route": route, "regressions": [{"metric": "lcp", "delta": 200}]},
    )
    db.commit()


class TestPerfCorrelationDetector:

    def test_both_sources_on_same_route_fires_correlation(self, db):
        """rum + lh_public on same route in last 10min → diagnostic fires."""
        _seed_alert(db, "rum_regression", "/app")
        _seed_alert(db, "lighthouse_regression_public", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 1
        assert _count_alerts(db, "perf_network_layer_drift") == 1

    def test_only_rum_no_correlation(self, db):
        """rum alone must NOT emit the correlation — not enough signal
        to rule out app-code."""
        _seed_alert(db, "rum_regression", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 0
        assert _count_alerts(db, "perf_network_layer_drift") == 0

    def test_only_lighthouse_no_correlation(self, db):
        """lighthouse alone must NOT emit — synthetic can regress on
        local runs too."""
        _seed_alert(db, "lighthouse_regression_public", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 0

    def test_different_routes_no_correlation(self, db):
        """rum on /app + lh on /pricing → no correlation (different
        surface, could be two independent app-code regressions)."""
        _seed_alert(db, "rum_regression", "/app")
        _seed_alert(db, "lighthouse_regression_public", "/pricing")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 0

    def test_cooldown_dedups_within_hour(self, db):
        """Back-to-back detector calls should fire exactly once per
        route per hour."""
        _seed_alert(db, "rum_regression", "/app")
        _seed_alert(db, "lighthouse_regression_public", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        first = detect_perf_network_layer_drift(db)
        second = detect_perf_network_layer_drift(db)
        assert first == 1
        assert second == 0
        assert _count_alerts(db, "perf_network_layer_drift") == 1

    def test_two_separate_routes_each_fire(self, db):
        """Two legitimate edge-drift signals (on two routes) → two
        diagnostic alerts."""
        _seed_alert(db, "rum_regression", "/app")
        _seed_alert(db, "lighthouse_regression_public", "/app")
        _seed_alert(db, "rum_regression", "/pricing")
        _seed_alert(db, "lighthouse_regression_public", "/pricing")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 2

    def test_unrelated_alerts_ignored(self, db):
        """Other alert types in the same window must not pollute the
        correlation."""
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="whatever",
            alert_type="p95_slow_trend",
            summary="unrelated",
            detail={"route": "/app"},
        )
        db.commit()
        _seed_alert(db, "rum_regression", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        fired = detect_perf_network_layer_drift(db)
        assert fired == 0

    def test_detail_payload_carries_triage_hints(self, db):
        """The correlation alert must include the triage suggestions so
        an on-call can start at the right layer without reading code."""
        _seed_alert(db, "rum_regression", "/app")
        _seed_alert(db, "lighthouse_regression_public", "/app")
        from app.services.observability_spikes import detect_perf_network_layer_drift
        detect_perf_network_layer_drift(db)
        row = db.execute(text("""
            SELECT detail FROM ops_alerts
            WHERE alert_type = 'perf_network_layer_drift'
            ORDER BY created_at DESC LIMIT 1
        """)).mappings().first()
        assert row is not None
        import json
        d = row["detail"]
        if isinstance(d, str):
            d = json.loads(d)
        assert d["route"] == "/app"
        assert d["window_minutes"] == 10
        assert d["rum_count"] == 1
        assert d["lighthouse_count"] == 1
        assert "suggested_triage" in d
        assert any("Cloudflare" in hint for hint in d["suggested_triage"])

    def test_registered_in_pipeline_internal_alert_types(self):
        """The correlation alert MUST be in
        `_PIPELINE_INTERNAL_ALERT_TYPES` so Rule 7/8 skip it —
        otherwise the LLM-patch pipeline would spin on a non-code
        problem and waste budget."""
        from app.services.bugfix_pipeline import _PIPELINE_INTERNAL_ALERT_TYPES
        assert "perf_network_layer_drift" in _PIPELINE_INTERNAL_ALERT_TYPES

    def test_registered_in_detector_fan_out(self):
        """The detector must be wired into the fan-out used by
        aggregation_worker — otherwise the signal never fires in prod."""
        from app.services import observability_spikes as mod
        import inspect
        src = inspect.getsource(mod.run_all_spike_detectors)
        assert "detect_perf_network_layer_drift" in src
        assert "perf_network_layer_drift" in src

    def test_sql_failure_returns_zero_not_raise(self, db):
        """Transient DB failure must not crash the worker cycle."""
        with patch.object(db, "execute", side_effect=RuntimeError("boom")):
            from app.services.observability_spikes import detect_perf_network_layer_drift
            fired = detect_perf_network_layer_drift(db)
        assert fired == 0
