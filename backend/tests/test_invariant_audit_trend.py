"""Test invariant_monitor._check_audit_findings_trend.

Alerts when a wired audit's findings count trends up — signaling a
regression class that's accumulating without individually tripping
preflight. Edge cases: flat (no alert), spike (alert), jitter (no
alert), always-zero (no alert).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.redis_client import _client
from app.services import invariant_monitor
from app.core.wired_audits import WIRED_AUDITS


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


def _pick_audit() -> str:
    return sorted(WIRED_AUDITS)[0][:-3]  # strip .py


def _seed_history(rc, audit_name, days_ago_findings):
    """Given {days_ago: findings}, seed Redis HASH cells."""
    today = date.today()
    key = f"{_PREFIX}:{audit_name}"
    for days_ago, findings in days_ago_findings.items():
        day = (today - timedelta(days=days_ago)).isoformat()
        rc.hset(key, day, f"1|{findings}|info")


def test_no_alert_when_no_telemetry(clean_telemetry, db):
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["checked"] == 1
    assert summary["failed"] == 0


def test_no_alert_when_all_zero_findings(clean_telemetry, db):
    """Audit fires every day but with findings=0 — clean state, no trend."""
    audit = _pick_audit()
    # 14 days of 0 findings
    seeds = {d: 0 for d in range(14)}
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 0


def test_alert_on_clear_upward_trend(clean_telemetry, db):
    """First half: 0-1 findings/day. Second half: 5+ findings/day.
    Alert must fire."""
    audit = _pick_audit()
    seeds = {}
    # days 8-14: 0 findings (first half)
    for d in range(7, 14):
        seeds[d] = 0
    # days 0-6: 3 findings/day (second half, total 21)
    for d in range(7):
        seeds[d] = 3
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 1
    assert summary["alerts_written"] == 1


def test_no_alert_on_flat_steady_findings(clean_telemetry, db):
    """Audit consistently has 3 findings/day in both halves — not a
    trend, it's a steady-state issue that belongs elsewhere."""
    audit = _pick_audit()
    seeds = {d: 3 for d in range(14)}
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 0


def test_no_alert_on_small_spike_below_threshold(clean_telemetry, db):
    """Single spike of 2 findings against prior 0 — delta too small
    to be meaningful. Threshold is 5 findings absolute delta."""
    audit = _pick_audit()
    seeds = {d: 0 for d in range(7, 14)}
    seeds.update({d: 0 for d in range(7)})
    seeds[0] = 2  # today: 2 findings
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 0


def test_no_alert_on_downward_trend(clean_telemetry, db):
    """Findings dropped from 10/day → 0. Improvement, not regression.
    Delta is negative, so no alert."""
    audit = _pick_audit()
    seeds = {d: 10 for d in range(7, 14)}
    seeds.update({d: 0 for d in range(7)})
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 0


def test_trend_detection_aggregates_across_multiple_audits(clean_telemetry, db):
    """Two audits trending up simultaneously — one alert with both
    in the detail payload."""
    audits = sorted(WIRED_AUDITS)[:2]
    for a in audits:
        name = a[:-3]
        seeds = {d: 0 for d in range(7, 14)}
        seeds.update({d: 4 for d in range(7)})  # 28 findings in second half
        _seed_history(clean_telemetry, name, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    # Both trending → 1 alert with 2 audits in payload
    assert summary["failed"] == 1
    assert summary["alerts_written"] == 1


def test_threshold_env_overrides(monkeypatch, clean_telemetry, db):
    """AUDIT_TREND_MIN_ABSOLUTE_DELTA env lowers threshold — a previously
    ignored 2-finding spike now alerts."""
    # Reload module state — since thresholds are read at import time,
    # we patch the module attributes directly.
    monkeypatch.setattr(invariant_monitor, "_TREND_MIN_ABSOLUTE_DELTA", 1)
    monkeypatch.setattr(invariant_monitor, "_TREND_MIN_SECOND_HALF", 1)

    audit = _pick_audit()
    seeds = {d: 0 for d in range(7, 14)}
    seeds.update({d: 0 for d in range(1, 7)})
    seeds[0] = 2  # today: 2 findings
    _seed_history(clean_telemetry, audit, seeds)

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_audit_findings_trend(db, summary)
    assert summary["failed"] == 1
