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


def _connect_error_client():
    """Mock httpx.Client whose every call raises ConnectError — simulates
    backend mid-restart or network blip."""
    import httpx

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

        def post(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

    return _FakeClient


def test_transport_error_does_not_emit_security_alert(db, monkeypatch):
    """ConnectError on every probe = backend unreachable = NOT a
    security regression. No alerts written, no stamp_run, no cascade.
    Born 2026-05-09 after 5 ConnectError-induced false-positive
    security_probe_failed alerts + 5 cascade breach_response_required
    accumulated unresolved over 24h."""
    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _connect_error_client())
    before = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()

    report = sh.run_security_heartbeat(db)

    assert report["skipped"] is True
    after = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()
    assert after == before  # no false-positive alerts emitted


def test_heal_resolves_prior_security_probe_failed(db, monkeypatch):
    """Successful run heals prior unresolved security_probe_failed
    alerts AND cascade-resolves matching breach_response_required."""
    # Seed: one stale security_probe_failed + its cascade.
    stale = OpsAlert(
        severity="critical",
        source="security_heartbeat:_probe_oauth_no_state",
        alert_type="security_probe_failed",
        summary="Security probe '_probe_oauth_no_state' FAILED",
        detail="Error: ConnectError",
        resolved=False,
    )
    db.add(stale)
    db.flush()
    cascade = OpsAlert(
        severity="critical",
        source=f"breach:{stale.id}",
        alert_type="breach_response_required",
        summary="BREACH RESPONSE REQUIRED",
        detail="cascade",
        resolved=False,
    )
    db.add(cascade)
    db.commit()

    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _always_rejecting_client())

    sh.run_security_heartbeat(db)

    db.refresh(stale)
    db.refresh(cascade)
    assert stale.resolved is True
    assert cascade.resolved is True


def test_mixed_transport_and_real_failure_only_alerts_on_real(db, monkeypatch):
    """If 1 probe hits ConnectError but another genuinely fails (200
    when expecting 401), only the real failure emits an alert."""
    import httpx

    class _MixedClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            if "/auth/callback" in url:
                raise httpx.ConnectError("flake")
            class _FakeResp:
                def __init__(self, s):
                    self.status_code = s
                def json(self):
                    return {}
            return _FakeResp(200)  # real failure: ops endpoint accepts bogus key

        def post(self, url, **kw):
            class _FakeResp:
                def __init__(self, s, body=None):
                    self.status_code = s
                    self._body = body or {}
                def json(self):
                    return self._body
            if "/track" in url:
                return _FakeResp(200, {"status": "ignored", "reason": "consent_denied"})
            return _FakeResp(401)

    monkeypatch.setattr(sh, "_should_run", lambda: True)
    monkeypatch.setattr(sh.httpx, "Client", _MixedClient)

    before = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()

    report = sh.run_security_heartbeat(db)

    assert report.get("skipped") is not True
    assert report["transport_errors"] >= 1
    assert report["failed"] >= 1
    after = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "security_probe_failed",
    ).count()
    # exactly the real-failure count emitted alerts (transport errors did NOT)
    assert after == before + report["failed"]
