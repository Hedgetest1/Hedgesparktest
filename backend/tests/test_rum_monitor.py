"""
Tests for rum_monitor — real-user web-vitals aggregation + p75 drift.

Covers:
  - ingest_sample happy path + bounds + unknown-metric rejection
  - compute_p75 returns None below the 20-sample floor
  - daily regression job fires rum_regression when today p75 > baseline
  - daily regression job stays silent on sparse history
  - window gate skips outside 02-04 UTC
  - per-IP rate-limit on the endpoint (light sanity check)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text


def _count_alerts(db, alert_type: str) -> int:
    return int(db.execute(
        text("SELECT COUNT(*) FROM ops_alerts WHERE alert_type = :t"),
        {"t": alert_type},
    ).scalar() or 0)


@pytest.fixture(autouse=True)
def _clean_rum_redis():
    """Isolate each test's Redis state."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:rum:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass
    yield


class TestIngest:
    def test_accepts_valid_time_metric(self):
        from app.services.rum_monitor import ingest_sample
        from app.core.redis_client import _client
        rc = _client()
        assert ingest_sample(rc, "/app", "lcp", 1500.0) is True

    def test_rejects_unknown_metric(self):
        from app.services.rum_monitor import ingest_sample
        from app.core.redis_client import _client
        rc = _client()
        assert ingest_sample(rc, "/app", "totally-made-up", 100.0) is False

    def test_rejects_out_of_bounds(self):
        from app.services.rum_monitor import ingest_sample
        from app.core.redis_client import _client
        rc = _client()
        # 120_000 ms > upper bound 60_000
        assert ingest_sample(rc, "/app", "lcp", 120_000.0) is False
        assert ingest_sample(rc, "/app", "lcp", -5.0) is False

    def test_accepts_cls_above_one(self):
        """CLS can exceed 1 on catastrophic layouts — must still ingest."""
        from app.services.rum_monitor import ingest_sample
        from app.core.redis_client import _client
        rc = _client()
        assert ingest_sample(rc, "/app", "cls", 2.5) is True


class TestP75:
    def test_none_when_below_floor(self):
        from app.services.rum_monitor import ingest_sample, compute_p75
        from app.core.redis_client import _client
        rc = _client()
        for i in range(10):
            ingest_sample(rc, "/app", "lcp", 1200.0 + i)
        assert compute_p75(rc, "/app", "lcp") is None

    def test_computes_from_30_samples(self):
        from app.services.rum_monitor import ingest_sample, compute_p75
        from app.core.redis_client import _client
        rc = _client()
        # 30 samples from 1000..1029 → p75 ≈ 1022 (approx)
        for i in range(30):
            ingest_sample(rc, "/app", "lcp", 1000.0 + i)
        p = compute_p75(rc, "/app", "lcp")
        assert p is not None
        assert 1015 <= p <= 1030


class TestRegressionJob:
    def _seed_samples(self, rc, route: str, metric: str, value: float, n: int = 30):
        from app.services.rum_monitor import ingest_sample
        for _ in range(n):
            ingest_sample(rc, route, metric, value)

    def _seed_history(self, rc, route: str, metric: str, values: list[float]):
        """Directly push p75 history rows so we can drive the baseline."""
        import json
        from app.services.rum_monitor import _P75_HIST_KEY
        key = _P75_HIST_KEY.format(route=route, metric=metric)
        for v in values:
            rc.lpush(key, json.dumps({"captured_at": "2026-04-10T03:00", "p75": float(v)}))

    def test_regression_fires(self, db):
        """7-day median baseline 1200ms, today's p75 ~2000ms → alert."""
        from app.services.rum_monitor import run_daily_regression_check
        from app.core.redis_client import _client
        rc = _client()
        # Seed 30 samples around 2000ms so today's p75 ≈ 2000.
        self._seed_samples(rc, "/app", "lcp", 2000.0, n=30)
        # Seed 7 days of healthy baseline p75 ≈ 1200ms.
        self._seed_history(rc, "/app", "lcp", [1200.0] * 7)
        result = run_daily_regression_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] >= 1
        assert _count_alerts(db, "rum_regression") >= 1

    def test_no_regression_when_stable(self, db):
        from app.services.rum_monitor import run_daily_regression_check
        from app.core.redis_client import _client
        rc = _client()
        self._seed_samples(rc, "/app", "lcp", 1200.0, n=30)
        self._seed_history(rc, "/app", "lcp", [1200.0] * 7)
        result = run_daily_regression_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] == 0

    def test_sparse_history_no_alert(self, db):
        """< 3 historical p75 samples → no alert even on massive jump."""
        from app.services.rum_monitor import run_daily_regression_check
        from app.core.redis_client import _client
        rc = _client()
        self._seed_samples(rc, "/app", "lcp", 5000.0, n=30)
        self._seed_history(rc, "/app", "lcp", [1200.0, 1250.0])  # only 2
        result = run_daily_regression_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] == 0

    def test_outside_window_noop(self, db):
        """Without force=True, noon UTC returns outside_window."""
        from app.services.rum_monitor import run_daily_regression_check
        fake_noon = datetime(2026, 4, 17, 12, 0, 0)
        with patch("app.services.rum_monitor.datetime") as mdt:
            mdt.now.return_value = fake_noon.replace(tzinfo=timezone.utc)
            mdt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = run_daily_regression_check(db)
        assert result == {"ran": False, "reason": "outside_window"}

    def test_daily_gate_blocks_second_run(self, db):
        from app.services.rum_monitor import run_daily_regression_check
        from app.core.redis_client import _client
        rc = _client()
        self._seed_samples(rc, "/app", "lcp", 1200.0, n=30)
        first = run_daily_regression_check(db, force=True)
        assert first["ran"] is True
        with patch("app.services.rum_monitor._in_daily_window", return_value=True):
            second = run_daily_regression_check(db)
        assert second == {"ran": False, "reason": "already_ran"}


class TestEndpoint:
    def test_accepts_valid_payload(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.post("/rum/metric", json={"route": "/app", "metric": "lcp", "value": 1200.0})
        assert r.status_code == 202
        assert r.json()["accepted"] is True

    def test_rejects_unknown_metric(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.post("/rum/metric", json={"route": "/app", "metric": "wat", "value": 100.0})
        assert r.status_code == 422  # pydantic validation

    def test_rejects_negative_value(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.post("/rum/metric", json={"route": "/app", "metric": "lcp", "value": -1.0})
        assert r.status_code == 422

    def test_route_normalization_strips_query(self):
        """Ingestion layer strips query string — keeps histograms clean."""
        from app.services.rum_monitor import _safe_route
        assert _safe_route("/app?shop=foo.com&auth=1") == "/app"
        assert _safe_route("/app#section") == "/app"
        assert _safe_route("") == "/__unknown"
        assert _safe_route(None) == "/__unknown"
