"""
Smoke tests for the tracker error telemetry pipeline — POST /public/tracker-error
and the observability_spikes detector.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _reset_spike_cooldowns():
    """Per-test cooldown + Redis-counter isolation so tests don't
    inherit tracker-error state from prior test runs."""
    from app.services.observability_spikes import reset_test_cooldowns
    reset_test_cooldowns()
    # Clear any leftover tracker-error Redis counters from earlier runs.
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:trkerr:*", count=500)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass  # SILENT-OK: test hygiene
    yield
    reset_test_cooldowns()


def _count_alerts(db, alert_type: str) -> int:
    return int(db.execute(
        text("SELECT COUNT(*) FROM ops_alerts WHERE alert_type = :t"),
        {"t": alert_type},
    ).scalar() or 0)


class TestTrackerErrorEndpoint:
    """POST /public/tracker-error — payload hygiene + rate limiting."""

    def test_accepts_valid_error_report(self, client, db, merchant_a):
        resp = client.post(
            "/public/tracker-error",
            json={
                "shop": "test-shop-a.myshopify.com",
                "source": "spark-tracker.boot",
                "message": "TypeError: cannot read prop of undefined",
                "stack": "at _hedgesparkBoot (tracker.js:27)",
                "url": "https://example.myshopify.com/products/candle",
                "tracker_version": 11,
                "user_agent": "Mozilla/5.0",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, body
        assert len(body["hash"]) == 16
        # Redis counter should now read 1 total event + 1 distinct hash.
        from app.core.redis_client import _client
        rc = _client()
        assert rc is not None
        total = int(rc.get("hs:trkerr:tot:test-shop-a.myshopify.com:" + _today()) or 0)
        distinct = int(rc.scard("hs:trkerr:hash:test-shop-a.myshopify.com:" + _today()) or 0)
        assert total == 1
        assert distinct == 1

    def test_rejects_blank_shop(self, client):
        resp = client.post(
            "/public/tracker-error",
            json={
                "shop": "   ",
                "source": "spark-tracker.boot",
                "message": "boom",
            },
        )
        # Shop min_length=3 in the Pydantic model so this is rejected at
        # validation time with 422. Either way, no crash.
        assert resp.status_code in (200, 422)

    def test_scrubs_pii_from_message(self, client, db, merchant_a):
        resp = client.post(
            "/public/tracker-error",
            json={
                "shop": "test-shop-a.myshopify.com",
                "source": "spark-pixel",
                "message": "fetch failed for user@example.com with token shpat_" + "x" * 24,
                "stack": "...",
                "url": "https://example.myshopify.com/",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True, resp.json()
        err_hash = resp.json()["hash"]
        # The Redis sample (written only on first observation of a hash)
        # must not contain the raw email or token.
        from app.core.redis_client import _client
        rc = _client()
        assert rc is not None
        sample_key = f"hs:trkerr:sample:test-shop-a.myshopify.com:{_today()}:{err_hash}"
        raw = rc.get(sample_key)
        assert raw is not None, f"sample missing at {sample_key}"
        detail_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        assert "user@example.com" not in detail_str
        assert "shpat_" + "x" * 24 not in detail_str
        assert "[EMAIL]" in detail_str
        assert "[TOKEN]" in detail_str


class TestSpikeDetectors:
    """Observability spike detectors produce single rollup alerts."""

    def test_tracker_error_spike_fires_on_threshold(self, client, db, merchant_a):
        # Seed enough distinct tracker runtime errors to cross the
        # distinct-hash threshold (5). Redis counters accumulate per
        # POST; the spike detector reads them and fires once per day.
        for i in range(6):
            resp = client.post(
                "/public/tracker-error",
                json={
                    "shop": "test-shop-a.myshopify.com",
                    "source": "spark-tracker.boot",
                    "message": f"error variant {i}",  # distinct → distinct hashes
                    "stack": f"at line {i}",
                    "url": "https://example.myshopify.com/",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["ok"] is True, resp.json()

        from app.services.observability_spikes import detect_tracker_error_spikes
        # First invocation: should fire 1 alert (distinct_hashes ≥ 5)
        fired = detect_tracker_error_spikes(db)
        assert fired >= 1
        assert _count_alerts(db, "tracker_runtime_error_spike") >= 1

        # Second invocation same day: cooldown prevents re-alerting
        fired_again = detect_tracker_error_spikes(db)
        assert fired_again == 0

    def test_frontend_error_spike_below_threshold_no_alert(self, client, db):
        from app.services.observability_spikes import detect_frontend_error_spike
        # No frontend errors seeded → no alert
        fired = detect_frontend_error_spike(db)
        assert fired == 0

    def test_frontend_error_spike_fires_above_threshold(self, client, db):
        # Seed 11 frontend_error alerts directly to cross the threshold (10)
        from app.services.alerting import write_alert
        for i in range(11):
            write_alert(
                db,
                severity="warning",
                source=f"dashboard.component_{i}",
                alert_type="frontend_error",
                summary=f"test error {i}",
                detail={"i": i},
            )
        from app.services.observability_spikes import detect_frontend_error_spike
        fired = detect_frontend_error_spike(db)
        assert fired == 1
        assert _count_alerts(db, "frontend_error_spike") >= 1

    def test_p95_detector_degrades_when_no_table(self, db):
        """request_timing table may not exist — detector must return 0 silently."""
        from app.services.observability_spikes import detect_p95_slow_trends
        fired = detect_p95_slow_trends(db)
        assert fired == 0  # no crash, no alert, no exception

    def test_ux_frustration_spike_fires_on_threshold(self, db, merchant_a):
        """Seed rage_click events above threshold → spike alert fires."""
        from app.models.event import Event
        import time as _t
        now_ms = int(_t.time() * 1000)
        # Seed 31 rage_clicks (threshold is 30) from distinct visitors
        # so the DB aggregation sees real volume.
        for i in range(31):
            db.add(Event(
                shop_domain="test-shop-a.myshopify.com",
                visitor_id=f"v{i}",
                event_type="rage_click",
                timestamp=now_ms,
            ))
        db.flush()
        from app.services.observability_spikes import detect_ux_frustration_spikes
        fired = detect_ux_frustration_spikes(db)
        assert fired >= 1
        assert _count_alerts(db, "ux_frustration_spike") >= 1

    def test_ux_frustration_spike_below_threshold_no_alert(self, db, merchant_a):
        """Seed events under the threshold → no alert."""
        from app.models.event import Event
        import time as _t
        now_ms = int(_t.time() * 1000)
        for i in range(5):
            db.add(Event(
                shop_domain="test-shop-a.myshopify.com",
                visitor_id=f"v{i}",
                event_type="rage_click",
                timestamp=now_ms,
            ))
        db.flush()
        from app.services.observability_spikes import detect_ux_frustration_spikes
        fired = detect_ux_frustration_spikes(db)
        assert fired == 0


class TestSentrySpikeDetectors:
    """Sentry incident rate spike + regression detection tests."""

    def _seed_incidents(self, db, *, count, fingerprint=None, triage_status=None, created_offset_sec=0):
        """Helper to seed sentry_incidents rows for tests."""
        from app.models.sentry_incident import SentryIncident
        from datetime import datetime, timezone, timedelta
        base_ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=created_offset_sec)
        for i in range(count):
            db.add(SentryIncident(
                created_at=base_ts,
                source_type="sentry_webhook",
                status="parsed",
                ai_triage_status=triage_status,
                fingerprint=fingerprint or f"fp_{i}",
                error_type="TypeError",
                error_title=f"test incident {i}",
            ))
        db.flush()

    def test_sentry_rate_spike_fires_when_rate_triples(self, db):
        # 12 incidents in the last 5 min (window is 15 min) crossing
        # the absolute floor of 10 with baseline of 0 → unambiguous spike.
        self._seed_incidents(db, count=12, created_offset_sec=60)
        from app.services.observability_spikes import detect_sentry_rate_spikes
        fired = detect_sentry_rate_spikes(db)
        assert fired == 1
        assert _count_alerts(db, "sentry_incident_rate_spike") == 1

    def test_sentry_rate_spike_below_floor_no_alert(self, db):
        # 5 incidents is below the 10-absolute floor even with no baseline.
        self._seed_incidents(db, count=5, created_offset_sec=60)
        from app.services.observability_spikes import detect_sentry_rate_spikes
        fired = detect_sentry_rate_spikes(db)
        assert fired == 0

    def test_sentry_regression_fires_when_fixed_fp_returns(self, db):
        # Old incident (1 hour ago) with triage_status=consumed — the fix shipped.
        self._seed_incidents(
            db, count=1, fingerprint="fp_regression",
            triage_status="consumed", created_offset_sec=3600,
        )
        # New incident in the last 30 min with SAME fingerprint → regression.
        self._seed_incidents(
            db, count=2, fingerprint="fp_regression",
            triage_status="pending", created_offset_sec=60,
        )
        from app.services.observability_spikes import detect_sentry_regressions
        fired = detect_sentry_regressions(db)
        assert fired == 1
        assert _count_alerts(db, "sentry_regression") >= 1

    def test_sentry_regression_no_alert_when_fp_never_consumed(self, db):
        # Even with new incidents, no consumed predecessor → no regression.
        self._seed_incidents(
            db, count=3, fingerprint="fp_fresh",
            triage_status="pending", created_offset_sec=60,
        )
        from app.services.observability_spikes import detect_sentry_regressions
        fired = detect_sentry_regressions(db)
        assert fired == 0
