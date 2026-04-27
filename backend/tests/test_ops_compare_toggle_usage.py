"""Test GET /ops/compare-toggle-usage — operator view on adoption of
the "compare to previous period" toggle.

Populates `hs:compare_toggle_usage:v1` synthetically and asserts the
endpoint aggregates correctly + enforces the API-key gate.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from app.core.redis_client import _client


_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
_KEY = "hs:compare_toggle_usage:v1"


@pytest.fixture
def clean_compare_counter():
    rc = _client()
    if rc is None:
        pytest.skip("redis unavailable")
    rc.delete(_KEY)
    yield rc
    rc.delete(_KEY)


def test_endpoint_requires_operator_auth(client, clean_compare_counter):
    """No API key → 401/403."""
    resp = client.get("/ops/compare-toggle-usage")
    assert resp.status_code in (401, 403)


def test_endpoint_empty_state(client, clean_compare_counter):
    """No counter data → returns zeros gracefully."""
    resp = client.get("/ops/compare-toggle-usage",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_days"] == 30
    assert data["total_compare_requests"] == 0
    assert data["active_days"] == 0
    assert data["daily"] == []
    assert data["redis_available"] is True


def test_endpoint_aggregates_seeded_counter(client, clean_compare_counter):
    """Synthetic 3-day spread → endpoint sums + sorts correctly."""
    rc = clean_compare_counter
    today = date.today().isoformat()
    y = (date.today() - timedelta(days=1)).isoformat()
    d2 = (date.today() - timedelta(days=2)).isoformat()
    rc.hset(_KEY, today, "5")
    rc.hset(_KEY, y, "3")
    rc.hset(_KEY, d2, "7")

    resp = client.get("/ops/compare-toggle-usage?days=7",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_compare_requests"] == 15
    assert data["active_days"] == 3
    # Most recent first
    assert data["daily"][0]["day"] == today
    assert data["daily"][0]["count"] == 5
    assert data["daily"][-1]["day"] == d2


def test_endpoint_clamps_window_days(client, clean_compare_counter):
    """days param clamped to [1, 90]."""
    resp = client.get("/ops/compare-toggle-usage?days=0",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 1

    resp = client.get("/ops/compare-toggle-usage?days=999",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 90


def test_endpoint_filters_outside_window(client, clean_compare_counter):
    """Counter entries older than `days` window are excluded from totals."""
    rc = clean_compare_counter
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()
    rc.hset(_KEY, today, "10")
    rc.hset(_KEY, old, "999")  # outside 7-day window

    resp = client.get("/ops/compare-toggle-usage?days=7",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_compare_requests"] == 10  # 999 excluded
    assert data["active_days"] == 1
