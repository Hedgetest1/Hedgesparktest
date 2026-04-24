"""Test audit_telemetry primitive — per-audit fire-rate + coverage sink.

Uses Redis DB 15 (set by conftest.py) so tests don't collide with prod
Redis DB 0. Every test cleans its keys before starting via a local
fixture so runs are idempotent.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import audit_telemetry
from app.core.redis_client import _client


_PREFIX = "hs:audit_telemetry"


@pytest.fixture
def clean_telemetry():
    """Remove any audit_telemetry keys from test Redis before & after
    so each test sees a pristine namespace."""
    rc = _client()
    if rc is None:
        pytest.skip("redis unavailable")
    for k in rc.scan_iter(match=f"{_PREFIX}:*"):
        rc.delete(k)
    yield rc
    for k in rc.scan_iter(match=f"{_PREFIX}:*"):
        rc.delete(k)


def test_record_run_writes_today_cell(clean_telemetry):
    rc = clean_telemetry
    assert audit_telemetry.record_run("audit_x", findings=3, severity="warn")

    key = f"{_PREFIX}:audit_x"
    today = date.today().isoformat()
    cell = rc.hget(key, today)
    assert cell is not None
    runs, findings, severity = cell.split("|")
    assert runs == "1"
    assert findings == "3"
    assert severity == "warn"


def test_record_run_idempotent_on_same_day_increments_runs(clean_telemetry):
    rc = clean_telemetry
    audit_telemetry.record_run("audit_x", findings=5, severity="info")
    audit_telemetry.record_run("audit_x", findings=7, severity="warn")
    audit_telemetry.record_run("audit_x", findings=7, severity="warn")

    key = f"{_PREFIX}:audit_x"
    today = date.today().isoformat()
    cell = rc.hget(key, today)
    runs, findings, severity = cell.split("|")
    assert runs == "3"
    # Latest findings + severity win
    assert findings == "7"
    assert severity == "warn"


def test_record_run_rejects_empty_audit_name(clean_telemetry):
    assert audit_telemetry.record_run("", findings=1) is False


def test_record_run_coerces_negative_findings_to_zero(clean_telemetry):
    rc = clean_telemetry
    assert audit_telemetry.record_run("audit_x", findings=-5)
    today = date.today().isoformat()
    cell = rc.hget(f"{_PREFIX}:audit_x", today)
    _, findings, _ = cell.split("|")
    assert findings == "0"


def test_record_run_unknown_severity_falls_back_to_info(clean_telemetry):
    rc = clean_telemetry
    audit_telemetry.record_run("audit_x", findings=0, severity="whatever")
    today = date.today().isoformat()
    cell = rc.hget(f"{_PREFIX}:audit_x", today)
    _, _, severity = cell.split("|")
    assert severity == "info"


def test_record_run_sets_ttl(clean_telemetry):
    rc = clean_telemetry
    audit_telemetry.record_run("audit_x", findings=0)
    ttl = rc.ttl(f"{_PREFIX}:audit_x")
    # TTL should be close to 90d, minus a few seconds for test latency
    assert 89 * 86400 <= ttl <= 90 * 86400


def test_read_audit_history_returns_days_sorted(clean_telemetry):
    rc = clean_telemetry
    key = f"{_PREFIX}:audit_x"
    today = date.today()
    # Seed three days manually (bypassing record_run so we can place
    # historical cells without waiting for the calendar).
    for delta, findings in [(2, 3), (1, 7), (0, 0)]:
        day = (today - timedelta(days=delta)).isoformat()
        rc.hset(key, day, f"1|{findings}|info")

    history = audit_telemetry.read_audit_history("audit_x", days=7)
    assert len(history) == 3
    assert [r["day"] for r in history] == [
        (today - timedelta(days=2)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
    ]
    assert [r["findings"] for r in history] == [3, 7, 0]


def test_read_audit_history_respects_cutoff(clean_telemetry):
    rc = clean_telemetry
    key = f"{_PREFIX}:audit_x"
    today = date.today()
    # Seed one day 10 days ago + one today
    rc.hset(key, (today - timedelta(days=10)).isoformat(), "1|9|info")
    rc.hset(key, today.isoformat(), "1|1|info")

    history = audit_telemetry.read_audit_history("audit_x", days=3)
    assert len(history) == 1
    assert history[0]["findings"] == 1


def test_read_all_audits_aggregates_across_days(clean_telemetry):
    rc = clean_telemetry
    today = date.today()
    rc.hset(f"{_PREFIX}:audit_a",
            (today - timedelta(days=1)).isoformat(), "4|0|info")
    rc.hset(f"{_PREFIX}:audit_a", today.isoformat(), "2|3|warn")
    rc.hset(f"{_PREFIX}:audit_b", today.isoformat(), "1|0|info")

    summary = audit_telemetry.read_all_audits(days=7)
    assert "audit_a" in summary and "audit_b" in summary
    a = summary["audit_a"]
    assert a["runs"] == 6
    assert a["findings_total"] == 3
    assert a["findings_last"] == 3
    assert a["last_severity"] == "warn"
    assert a["last_day"] == today.isoformat()
    assert a["days_seen"] == 2

    b = summary["audit_b"]
    assert b["runs"] == 1
    assert b["findings_total"] == 0


def test_read_all_audits_excludes_audits_with_only_stale_history(clean_telemetry):
    rc = clean_telemetry
    stale_day = (date.today() - timedelta(days=20)).isoformat()
    rc.hset(f"{_PREFIX}:audit_stale", stale_day, "10|0|info")

    summary = audit_telemetry.read_all_audits(days=7)
    assert "audit_stale" not in summary


def test_read_all_audits_empty_when_no_keys(clean_telemetry):
    assert audit_telemetry.read_all_audits(days=7) == {}
