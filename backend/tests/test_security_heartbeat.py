"""Tests for security_heartbeat — synthetic self-attack probes.

Each probe fires a request that MUST be rejected. If the app accepts
it (200 OK), the probe fails and an ops_alert must be raised.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from app.models.ops_alert import OpsAlert
from app.services import security_heartbeat as sh


def _always_ok_client():
    """Mock httpx.Client that always returns 200 — simulates a regression
    where every endpoint accepts what it shouldn't."""
    class _FakeResp:
        def __init__(self, status=200, body=None):
            self.status_code = status
            self._body = body or {"status": "ok"}

        def json(self):
            return self._body

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return _FakeResp(200)

        def post(self, *a, **kw):
            return _FakeResp(200)

    return _FakeClient


def _always_rejecting_client():
    class _FakeResp:
        def __init__(self, status, body=None):
            self.status_code = status
            self._body = body or {}

        def json(self):
            return self._body

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            if "/auth/callback" in url:
                return _FakeResp(400)
            if "/ops/" in url:
                return _FakeResp(401)
            if "/merchant/export" in url:
                return _FakeResp(401)
            return _FakeResp(404)

        def post(self, url, **kw):
            if "/track" in url:
                return _FakeResp(200, {"status": "ignored", "reason": "consent_denied"})
            if "webhooks" in url:
                return _FakeResp(401)
            return _FakeResp(404)

    return _FakeClient


def test_all_probes_pass_when_app_rejects_correctly(db, monkeypatch):
    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _always_rejecting_client())
    report = sh.run_security_heartbeat(db)
    assert report["total"] == len(sh._PROBES)
    assert report["passed"] == report["total"]
    assert report["failed"] == 0


def test_probes_fail_and_emit_alerts_when_app_accepts(db, monkeypatch):
    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _always_ok_client())
    before = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()

    report = sh.run_security_heartbeat(db)

    assert report["failed"] >= 1
    after = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()
    assert after >= before + 1


def test_kill_switch_skips_run(db, monkeypatch):
    monkeypatch.setattr(sh, "_HEARTBEAT_PAUSED", True)
    report = sh.run_security_heartbeat(db)
    assert report["skipped"] is True
    assert report["total"] == 0


def test_self_rate_limit_skips_when_recent(db, monkeypatch):
    monkeypatch.setattr(sh, "_should_run", lambda: False)
    report = sh.run_security_heartbeat(db)
    assert report["skipped"] is True


def test_results_persist_to_redis(db, monkeypatch):
    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _always_rejecting_client())
    sh.run_security_heartbeat(db)
    stored = sh.get_last_results()
    if stored is None:
        pytest.skip("redis unavailable")
    assert "results" in stored
    assert len(stored["results"]) == len(sh._PROBES)
