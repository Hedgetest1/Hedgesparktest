"""Test GET /ops/audit-telemetry — operator view on per-audit fire-rate
+ findings trend.

Populates `hs:audit_telemetry:*` keys via the primitive + synthetic
seeds, then asserts the endpoint aggregates correctly and enforces
the API-key gate.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from app.core.redis_client import _client
from app.services import audit_telemetry


_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
_PREFIX = "hs:audit_telemetry"


@pytest.fixture
def clean_telemetry():
    rc = _client()
    if rc is None:
        pytest.skip("redis unavailable")
    for k in rc.scan_iter(match=f"{_PREFIX}:*"):
        rc.delete(k)
    yield rc
    for k in rc.scan_iter(match=f"{_PREFIX}:*"):
        rc.delete(k)


def test_endpoint_returns_empty_when_no_telemetry(client, clean_telemetry):
    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_audits"] == 0
    assert data["audits_with_findings_in_window"] == 0
    assert data["total_runs"] == 0
    assert data["audits"] == {}
    assert data["window_days"] == 7


def test_endpoint_aggregates_seeded_telemetry(client, clean_telemetry):
    rc = clean_telemetry
    today = date.today().isoformat()
    y = (date.today() - timedelta(days=1)).isoformat()
    rc.hset(f"{_PREFIX}:audit_a", today, "3|2|warn")
    rc.hset(f"{_PREFIX}:audit_a", y, "5|0|info")
    rc.hset(f"{_PREFIX}:audit_b", today, "1|0|info")

    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_audits"] == 2
    assert data["audits_with_findings_in_window"] == 1
    assert data["total_runs"] == 9

    a = data["audits"]["audit_a"]
    assert a["runs"] == 8
    assert a["findings_total"] == 2
    assert a["last_severity"] == "warn"
    assert a["findings_last"] == 2


def test_endpoint_clamps_days_to_safe_range(client, clean_telemetry):
    rc = clean_telemetry
    rc.hset(f"{_PREFIX}:audit_a", date.today().isoformat(), "1|0|info")

    # days=0 → clamped to 1
    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY},
                      params={"days": 0})
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 1

    # days=365 → clamped to 90 (TTL envelope)
    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY},
                      params={"days": 365})
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 90


def test_endpoint_requires_api_key(client, clean_telemetry):
    resp = client.get("/ops/audit-telemetry")
    assert resp.status_code in (401, 403)

    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": "wrong-key"})
    assert resp.status_code in (401, 403)


def test_endpoint_excludes_stale_history_outside_window(client, clean_telemetry):
    rc = clean_telemetry
    stale_day = (date.today() - timedelta(days=30)).isoformat()
    rc.hset(f"{_PREFIX}:audit_stale", stale_day, "100|10|critical")

    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY},
                      params={"days": 7})
    assert resp.status_code == 200
    assert "audit_stale" not in resp.json()["audits"]


def test_endpoint_roundtrip_through_primitive(client, clean_telemetry):
    """E2E contract: record_run writes land in /ops/audit-telemetry."""
    audit_telemetry.record_run("audit_foo", findings=4, severity="warn")
    audit_telemetry.record_run("audit_foo", findings=4, severity="warn")

    resp = client.get("/ops/audit-telemetry",
                      headers={"X-API-Key": _OP_KEY})
    assert resp.status_code == 200
    a = resp.json()["audits"]["audit_foo"]
    assert a["runs"] == 2
    assert a["findings_last"] == 4
    assert a["last_severity"] == "warn"
