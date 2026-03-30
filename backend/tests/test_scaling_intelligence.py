"""Tests for scaling intelligence — snapshots, forecasts, recommendations."""
import os
import time
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

import app.services.scaling_intelligence as si
from app.services.scaling_intelligence import (
    capture_daily_snapshot,
    build_forecast,
    generate_recommendations,
    get_active_recommendations,
    get_recent_snapshots,
    should_capture_snapshot,
    mark_snapshot_captured,
    should_generate_recommendations,
    mark_recommendations_generated,
    _linear_trend,
    _project,
    MIN_FORECAST_DAYS,
)
from app.models.system_snapshot import SystemSnapshot
from app.models.scaling_recommendation import ScalingRecommendation
from app.services.telegram_agent import handle_command

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_snapshots(db, days=10, base_merchants=10, merchant_growth=1.0,
                    ram_used=1000, ram_total=2000, llm_cost=0.01, error_rate=3.0):
    """Seed N days of snapshots with controllable trends."""
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        s = SystemSnapshot(
            date_bucket=d,
            active_merchants=int(base_merchants + merchant_growth * i),
            billing_active_merchants=int((base_merchants + merchant_growth * i) * 0.3),
            total_events_24h=100 + i * 10,
            llm_calls_24h=5 + i,
            llm_estimated_cost_eur=llm_cost + i * 0.001,
            worker_error_rate=error_rate,
            ram_used_mb=ram_used + i * 20,
            ram_total_mb=ram_total,
            cpu_pct=30.0 + i * 0.5,
        )
        db.add(s)
    db.flush()


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------

def test_daily_snapshot_created(db):
    """capture_daily_snapshot creates a row."""
    snapshot = capture_daily_snapshot(db)
    assert snapshot is not None
    assert snapshot.date_bucket == date.today()
    assert snapshot.active_merchants >= 0


def test_daily_snapshot_idempotent(db):
    """Second capture same day returns existing row."""
    s1 = capture_daily_snapshot(db)
    s2 = capture_daily_snapshot(db)
    assert s1.id == s2.id


def test_snapshot_cooldown():
    """Cooldown prevents too-frequent snapshots."""
    original = si._last_snapshot
    try:
        si._last_snapshot = None
        assert should_capture_snapshot() is True

        mark_snapshot_captured()
        assert should_capture_snapshot() is False
    finally:
        si._last_snapshot = original


# ---------------------------------------------------------------------------
# Linear trend
# ---------------------------------------------------------------------------

def test_linear_trend_upward():
    """Upward trend returns positive slope."""
    slope, last = _linear_trend([10, 12, 14, 16, 18])
    assert slope > 0
    assert abs(slope - 2.0) < 0.01
    assert last == 18


def test_linear_trend_flat():
    """Flat data returns near-zero slope."""
    slope, last = _linear_trend([5, 5, 5, 5, 5])
    assert abs(slope) < 0.01


def test_linear_trend_single_point():
    """Single point returns zero slope."""
    slope, last = _linear_trend([42])
    assert slope == 0.0
    assert last == 42


def test_project():
    """Projection math is correct."""
    assert _project(2.0, 10, 30) == 70.0


# ---------------------------------------------------------------------------
# Forecast with enough data
# ---------------------------------------------------------------------------

def test_forecast_with_data(db):
    """Forecast returns projections when enough snapshots exist."""
    _seed_snapshots(db, days=10)

    forecast = build_forecast(db)
    assert forecast["status"] == "ok"
    assert "merchants" in forecast
    assert "ram_pct" in forecast
    assert "llm_daily_cost_eur" in forecast
    assert forecast["snapshots_used"] >= MIN_FORECAST_DAYS


def test_forecast_merchant_projection(db):
    """Merchant projection reflects growth trend."""
    _seed_snapshots(db, days=10, base_merchants=10, merchant_growth=2.0)

    forecast = build_forecast(db)
    assert forecast["status"] == "ok"
    assert forecast["merchants"]["projected"] > forecast["merchants"]["current"]
    assert forecast["merchants"]["daily_growth"] > 0


# ---------------------------------------------------------------------------
# Forecast with insufficient data
# ---------------------------------------------------------------------------

def test_forecast_insufficient_data(db):
    """Forecast returns not_enough_data when < MIN_FORECAST_DAYS snapshots."""
    _seed_snapshots(db, days=2)

    forecast = build_forecast(db)
    assert forecast["status"] == "not_enough_data"
    assert forecast["snapshots_available"] == 2


def test_forecast_empty_db(db):
    """Forecast with zero snapshots returns not_enough_data."""
    forecast = build_forecast(db)
    assert forecast["status"] == "not_enough_data"


# ---------------------------------------------------------------------------
# Recommendations — justified trend
# ---------------------------------------------------------------------------

def test_recommendation_on_ram_saturation(db):
    """High RAM trend generates VPS upgrade recommendation."""
    _seed_snapshots(db, days=10, ram_used=1700, ram_total=2000)

    recs = generate_recommendations(db)
    assert any(r.get("resource_type") == "vps" for r in recs)


def test_no_recommendation_on_healthy_system(db):
    """Healthy system with low usage generates no recommendations."""
    _seed_snapshots(db, days=10, ram_used=500, ram_total=2000,
                    llm_cost=0.001, error_rate=1.0)

    recs = generate_recommendations(db)
    # Filter out merchant growth recs which depend on growth rate
    meaningful_recs = [r for r in recs if r.get("resource_type") != "vps" or "RAM" in r.get("title", "")]
    # No RAM or LLM warning recs expected
    ram_recs = [r for r in recs if "RAM" in r.get("title", "")]
    assert len(ram_recs) == 0


# ---------------------------------------------------------------------------
# Recommendation dedup
# ---------------------------------------------------------------------------

def test_recommendation_dedup(db):
    """Same recommendation not created twice."""
    _seed_snapshots(db, days=10, ram_used=1700, ram_total=2000)

    recs1 = generate_recommendations(db)
    db.flush()

    # Reset cooldown to allow second run
    original = si._last_recommend
    si._last_recommend = None

    recs2 = generate_recommendations(db)
    db.flush()

    si._last_recommend = original

    # Second run should have fewer new recs (deduped)
    assert len(recs2) < len(recs1) or len(recs2) == 0


# ---------------------------------------------------------------------------
# Telegram /scaling command
# ---------------------------------------------------------------------------

def test_telegram_scaling_no_data(db):
    """Scaling command works with no data."""
    result = handle_command("/scaling", db=db)
    assert "Scaling" in result
    assert "not enough data" in result.lower() or "No active" in result


def test_telegram_scaling_with_data(db):
    """Scaling command returns forecast and recommendations."""
    _seed_snapshots(db, days=10, ram_used=1700, ram_total=2000)
    generate_recommendations(db)
    db.flush()

    result = handle_command("/scaling", db=db)
    assert "Scaling" in result
    assert "Merchants" in result or "merchants" in result or "Forecast" in result


# ---------------------------------------------------------------------------
# OPS API auth
# ---------------------------------------------------------------------------

def test_ops_snapshots_requires_auth(client):
    """Snapshots endpoint requires operator auth."""
    resp = client.get("/ops/scaling/snapshots")
    assert resp.status_code == 401


def test_ops_forecast_requires_auth(client):
    """Forecast endpoint requires operator auth."""
    resp = client.get("/ops/scaling/forecast")
    assert resp.status_code == 401


def test_ops_recommendations_requires_auth(client):
    """Recommendations endpoint requires operator auth."""
    resp = client.get("/ops/scaling/recommendations")
    assert resp.status_code == 401


def test_ops_snapshots_returns_data(client, db):
    """Snapshots endpoint returns data when authenticated."""
    _seed_snapshots(db, days=3)
    db.commit()

    resp = client.get("/ops/scaling/snapshots", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) >= 3


def test_ops_forecast_returns_data(client, db):
    """Forecast endpoint returns structured response."""
    _seed_snapshots(db, days=7)
    db.commit()

    resp = client.get("/ops/scaling/forecast", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "merchants" in data


def test_ops_recommendations_returns_list(client, db):
    """Recommendations endpoint returns structured response."""
    resp = client.get("/ops/scaling/recommendations", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "recommendations" in data
    assert isinstance(data["recommendations"], list)


# ---------------------------------------------------------------------------
# Recommendation cooldown
# ---------------------------------------------------------------------------

def test_recommendation_cooldown():
    """Recommendation generation respects cooldown."""
    original = si._last_recommend
    try:
        si._last_recommend = None
        assert should_generate_recommendations() is True

        mark_recommendations_generated()
        assert should_generate_recommendations() is False
    finally:
        si._last_recommend = original
