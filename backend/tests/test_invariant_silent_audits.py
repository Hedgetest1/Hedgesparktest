"""Test invariant_monitor._check_silent_audits — catches audits that
stopped emitting telemetry + audits never observed (with grace window).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.redis_client import _client
from app.services import audit_telemetry, invariant_monitor
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


def _pick_sample_audits(n: int) -> list[str]:
    # Return audit NAMES (without .py) for use in record_run.
    return [f[:-3] for f in sorted(WIRED_AUDITS)[:n]]


def test_no_alert_when_all_audits_fresh(clean_telemetry, db):
    """If every wired audit has emitted today, no alert fires."""
    today = date.today().isoformat()
    for audit in _pick_sample_audits(3):
        clean_telemetry.hset(f"{_PREFIX}:{audit}", today, "1|0|info")

    # We need ALL wired audits emitting today to avoid the never-observed
    # branch firing after the grace window. Seed every one.
    for f in WIRED_AUDITS:
        name = f[:-3]
        clean_telemetry.hset(f"{_PREFIX}:{name}", today, "1|0|info")

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_silent_audits(db, summary)
    assert summary["checked"] == 1
    assert summary["failed"] == 0
    assert summary["alerts_written"] == 0


def test_alert_when_audit_silent_beyond_threshold(clean_telemetry, db):
    """Audit with last emission 10 days ago triggers silent-with-history."""
    today = date.today()
    # Seed every wired audit with fresh data (to pass grace + avoid
    # never-observed noise), then ONE audit with stale data.
    for f in WIRED_AUDITS:
        name = f[:-3]
        clean_telemetry.hset(f"{_PREFIX}:{name}", today.isoformat(), "1|0|info")

    stale_audit = _pick_sample_audits(1)[0]
    stale_day = (today - timedelta(days=10)).isoformat()
    clean_telemetry.delete(f"{_PREFIX}:{stale_audit}")
    clean_telemetry.hset(f"{_PREFIX}:{stale_audit}", stale_day, "1|0|info")

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_silent_audits(db, summary)
    assert summary["failed"] == 1
    assert summary["alerts_written"] == 1


def test_never_observed_respects_grace_window(clean_telemetry, db):
    """A brand-new audit with no telemetry at all must NOT alert when
    the telemetry system has been running for less than the grace
    window — otherwise every first-run would page us."""
    # Seed ONE wired audit with a recent emission only — this means
    # oldest history is recent (less than INITIAL_GRACE_DAYS), so the
    # grace window has NOT passed. Never-observed audits should be
    # silently tolerated.
    today = date.today().isoformat()
    sample = _pick_sample_audits(1)[0]
    clean_telemetry.hset(f"{_PREFIX}:{sample}", today, "1|0|info")

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_silent_audits(db, summary)
    # No alert — grace window protects us.
    assert summary["failed"] == 0
    assert summary["alerts_written"] == 0


def test_never_observed_fires_after_grace_window(clean_telemetry, db):
    """Once the telemetry has accumulated days_seen >= INITIAL_GRACE_DAYS
    for any audit (proving the system has been live for that many
    distinct days), never-observed audits DO alert."""
    today = date.today()
    # Seed one audit with enough distinct days to pass the grace gate
    # (>= 14 days_seen). Seeding daily over the window simulates a
    # system that's been running for at least 2 weeks.
    sample = _pick_sample_audits(1)[0]
    key = f"{_PREFIX}:{sample}"
    for offset in range(15):
        day = (today - timedelta(days=offset)).isoformat()
        clean_telemetry.hset(key, day, "1|0|info")

    # Other audits have NEVER emitted.
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_silent_audits(db, summary)
    assert summary["failed"] == 1
    assert summary["alerts_written"] == 1


def test_handles_empty_redis_gracefully(clean_telemetry, db):
    """With zero telemetry keys, check must not crash and must not
    alert (grace window has not passed — nothing is observed yet)."""
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    invariant_monitor._check_silent_audits(db, summary)
    assert summary["checked"] == 1
    assert summary["failed"] == 0


def test_wired_audits_matches_coverage_pin():
    """Canonical list in app.core.wired_audits must match the set the
    coverage pin enforces — they are read from the same module, but
    pin this contract explicitly in case someone hardcodes a copy."""
    import importlib.util
    from pathlib import Path

    pin_path = Path("/opt/wishspark/backend/scripts/audit_audit_telemetry_coverage.py")
    spec = importlib.util.spec_from_file_location("pin", pin_path)
    import sys
    sys.modules["pin"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["pin"])

    assert sys.modules["pin"].WIRED_AUDITS == WIRED_AUDITS
