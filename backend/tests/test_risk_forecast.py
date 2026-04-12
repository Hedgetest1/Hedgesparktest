"""Tests for H5 — risk forecast (future-facing RARS)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.risk_forecast import (
    _linear_regression,
    _load_history,
    _MIN_POINTS_FOR_FORECAST,
    get_risk_forecast,
    record_rars_snapshot,
)


def _shop(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}.myshopify.com"


def _seed_history(shop: str, values: list[float]) -> bool:
    """Inject synthetic history straight into Redis for deterministic forecast tests."""
    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        return False
    if rc is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    history = []
    for i, v in enumerate(values):
        ts = (now - timedelta(days=(len(values) - 1 - i))).isoformat()
        history.append({"ts": ts, "total_at_risk_eur": float(v)})
    rc.setex(f"hs:rars_history:v1:{shop}", 3600, json.dumps(history))
    return True


def test_linear_regression_perfect_line():
    points = [(0, 0), (1, 10), (2, 20), (3, 30)]
    slope, intercept, r2 = _linear_regression(points)
    assert slope == pytest.approx(10.0)
    assert intercept == pytest.approx(0.0)
    assert r2 == pytest.approx(1.0)


def test_linear_regression_flat_line():
    points = [(0, 5), (1, 5), (2, 5), (3, 5)]
    slope, intercept, r2 = _linear_regression(points)
    assert slope == pytest.approx(0.0)
    assert intercept == pytest.approx(5.0)


def test_record_and_load():
    shop = _shop("rforecast-record")
    record_rars_snapshot(shop, 100.0)
    history = _load_history(shop)
    if not history:
        pytest.skip("redis unavailable")
    assert len(history) == 1
    assert history[0]["total_at_risk_eur"] == 100.0


def test_record_dedupes_same_day():
    shop = _shop("rforecast-dedupe")
    record_rars_snapshot(shop, 100.0)
    record_rars_snapshot(shop, 150.0)
    record_rars_snapshot(shop, 200.0)
    history = _load_history(shop)
    if not history:
        pytest.skip("redis unavailable")
    assert len(history) == 1
    assert history[0]["total_at_risk_eur"] == 200.0


def test_forecast_insufficient_history():
    shop = _shop("rforecast-insufficient")
    record_rars_snapshot(shop, 100.0)
    result = get_risk_forecast(shop)
    if result.get("points_available", 0) == 0:
        pytest.skip("redis unavailable")
    assert result["status"] == "insufficient_history"


def test_forecast_rising_trend():
    shop = _shop("rforecast-rising")
    if not _seed_history(shop, [100, 120, 140, 160, 180, 200, 220]):
        pytest.skip("redis unavailable")
    result = get_risk_forecast(shop)
    assert result["status"] == "ok"
    assert result["direction"] == "rising"
    assert result["forecast_7d_eur"] > result["today_value_eur"]
    assert result["confidence"] in ("medium", "high")
    assert result["r_squared"] >= 0.9  # perfect linear


def test_forecast_falling_trend():
    shop = _shop("rforecast-falling")
    if not _seed_history(shop, [300, 260, 220, 180, 140, 100, 60]):
        pytest.skip("redis unavailable")
    result = get_risk_forecast(shop)
    assert result["status"] == "ok"
    assert result["direction"] == "falling"
    assert result["forecast_7d_eur"] < result["today_value_eur"]


def test_forecast_stable_trend():
    shop = _shop("rforecast-stable")
    if not _seed_history(shop, [100, 102, 99, 101, 100, 103, 98]):
        pytest.skip("redis unavailable")
    result = get_risk_forecast(shop)
    assert result["status"] == "ok"
    assert result["direction"] == "stable"


def test_forecast_never_goes_negative():
    shop = _shop("rforecast-nonneg")
    if not _seed_history(shop, [200, 150, 100, 50, 20, 10, 5]):
        pytest.skip("redis unavailable")
    result = get_risk_forecast(shop)
    assert result["status"] == "ok"
    assert result["forecast_7d_eur"] >= 0.0
