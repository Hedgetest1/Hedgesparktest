"""
Tests for dashboard_auto_remediation.

Design: the module is a deterministic handler for dashboard_asset_drift
alerts. It runs `pm2 restart wishspark-dashboard --update-env`, waits
briefly, re-probes the dashboard, and resolves/escalates based on the
outcome. Tests stub subprocess + probe + Redis so the paths are
exercised without actually restarting pm2 or hitting the network.
"""
from __future__ import annotations

import json

import pytest

from app.models.ops_alert import OpsAlert
from app.models.audit_log import AuditLog
from app.services import dashboard_auto_remediation as remed


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """The module sleeps ~8s post-restart for Next.js warmup — skip it."""
    monkeypatch.setattr(remed.time, "sleep", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Default: Redis returns None so rate limit is NOT triggered in tests.
    Individual tests can override for the rate-limit path."""
    class _NoRedis:
        @staticmethod
        def _client():
            return None
    monkeypatch.setattr(remed, "_rate_limited", lambda: False)
    monkeypatch.setattr(remed, "_record_attempt", lambda: None)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTO_REMEDIATION_ENABLED", "1")


def _make_drift_alert(db, *, summary="asset drift"):
    from app.services.alerting import write_alert
    a = write_alert(
        db,
        severity="critical",
        source="dashboard_asset_probe",
        alert_type=remed._ALERT_TYPE,
        summary=summary,
        detail={"failures": ["/: asset /_next/static/chunks/X.css returned HTTP 500"]},
    )
    db.commit()
    return a


def test_skipped_when_disabled(db, monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTO_REMEDIATION_ENABLED", "0")
    _make_drift_alert(db)
    report = remed.attempt(db)
    assert report["action"] == "skipped_disabled"


def test_skipped_when_no_alert(db):
    report = remed.attempt(db)
    assert report["action"] == "skipped_no_alert"
    assert report["alert_id"] is None


def test_happy_path_remediated(db, monkeypatch):
    origin = _make_drift_alert(db)
    origin_id = origin.id

    monkeypatch.setattr(remed, "_pm2_restart", lambda: (True, ""))
    monkeypatch.setattr(remed, "_probe_after_restart", lambda: [])

    report = remed.attempt(db)

    assert report["action"] == "remediated"
    assert report["alert_id"] == origin_id
    assert report["restart_ok"] is True

    # Origin alert must be resolved
    db.expire_all()
    refreshed = db.get(OpsAlert, origin_id)
    assert refreshed.resolved is True
    assert refreshed.resolved_at is not None

    # Follow-up info alert must exist
    followups = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == remed._FOLLOWUP_OK)
        .all()
    )
    assert len(followups) == 1
    assert followups[0].severity == "info"

    # Audit log row must exist with status=completed
    audits = (
        db.query(AuditLog)
        .filter(
            AuditLog.action_type == remed._AUDIT_ACTION,
            AuditLog.target_id == str(origin_id),
        )
        .all()
    )
    assert len(audits) == 1
    assert audits[0].status == "completed"


def test_restart_failure_escalates(db, monkeypatch):
    origin = _make_drift_alert(db)
    origin_id = origin.id

    monkeypatch.setattr(remed, "_pm2_restart", lambda: (False, "pm2 binary not found"))
    # Should not even be called, but stub it defensively
    monkeypatch.setattr(remed, "_probe_after_restart", lambda: [])

    report = remed.attempt(db)

    assert report["action"] == "escalated"
    assert report["restart_ok"] is False

    # Origin alert still unresolved
    db.expire_all()
    refreshed = db.get(OpsAlert, origin_id)
    assert refreshed.resolved is False

    # Critical failure follow-up exists
    failures = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == remed._FOLLOWUP_FAIL)
        .all()
    )
    assert len(failures) == 1
    f = failures[0]
    assert f.severity == "critical"
    detail = json.loads(f.detail) if isinstance(f.detail, str) else f.detail
    assert detail["reason"] == "pm2_restart_failed"
    assert "pm2 binary not found" in (detail.get("restart_error") or "")

    # Audit status=failed
    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.action_type == remed._AUDIT_ACTION,
            AuditLog.target_id == str(origin_id),
        )
        .first()
    )
    assert audit is not None
    assert audit.status == "failed"


def test_probe_still_failing_escalates(db, monkeypatch):
    """Restart succeeds but the dashboard is still serving broken chunks.
    Must escalate (not resolve origin, not claim success)."""
    origin = _make_drift_alert(db)
    origin_id = origin.id

    monkeypatch.setattr(remed, "_pm2_restart", lambda: (True, ""))
    monkeypatch.setattr(
        remed,
        "_probe_after_restart",
        lambda: ["/: asset /_next/static/chunks/Y.css returned HTTP 500"],
    )

    report = remed.attempt(db)

    assert report["action"] == "escalated"
    assert report["restart_ok"] is True
    assert report["post_probe_failures"] is not None

    db.expire_all()
    assert db.get(OpsAlert, origin_id).resolved is False

    failure = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == remed._FOLLOWUP_FAIL)
        .order_by(OpsAlert.id.desc())
        .first()
    )
    assert failure is not None
    detail = json.loads(failure.detail) if isinstance(failure.detail, str) else failure.detail
    assert detail["reason"] == "probe_still_failing"
    assert any("Y.css" in x for x in detail["post_restart_failures"])


def test_rate_limited_escalates_without_restart(db, monkeypatch):
    origin = _make_drift_alert(db)
    origin_id = origin.id

    monkeypatch.setattr(remed, "_rate_limited", lambda: True)

    called = {"restart": False, "probe": False}
    def _restart():
        called["restart"] = True
        return True, ""
    def _probe():
        called["probe"] = True
        return []
    monkeypatch.setattr(remed, "_pm2_restart", _restart)
    monkeypatch.setattr(remed, "_probe_after_restart", _probe)

    report = remed.attempt(db)

    assert report["action"] == "skipped_rate_limited"
    assert called["restart"] is False
    assert called["probe"] is False

    failures = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == remed._FOLLOWUP_FAIL)
        .all()
    )
    assert len(failures) == 1
    detail = json.loads(failures[0].detail) if isinstance(failures[0].detail, str) else failures[0].detail
    assert detail["reason"] == "rate_limited"

    # Origin stays unresolved.
    db.expire_all()
    assert db.get(OpsAlert, origin_id).resolved is False


def test_idempotent_skips_already_remediated_alert(db, monkeypatch):
    """A second attempt on the same origin alert should NOT re-trigger
    the remediation — the audit_log filter excludes alerts that already
    have a matching remediation row."""
    origin = _make_drift_alert(db)

    monkeypatch.setattr(remed, "_pm2_restart", lambda: (True, ""))
    monkeypatch.setattr(remed, "_probe_after_restart", lambda: [])

    report1 = remed.attempt(db)
    assert report1["action"] == "remediated"

    # Second cycle — origin is now resolved AND audit row exists.
    # Target-alert query filters both conditions; should find nothing.
    report2 = remed.attempt(db)
    assert report2["action"] == "skipped_no_alert"


def test_followup_types_excluded_from_bugfix_pipeline():
    """Both follow-up alert types must be in _PIPELINE_INTERNAL_ALERT_TYPES
    so the LLM-patch path never triages them."""
    from app.services.bugfix_pipeline import _PIPELINE_INTERNAL_ALERT_TYPES
    assert remed._FOLLOWUP_OK in _PIPELINE_INTERNAL_ALERT_TYPES
    assert remed._FOLLOWUP_FAIL in _PIPELINE_INTERNAL_ALERT_TYPES
