"""
Tests for dashboard_asset_probe_task.

The probe catches the "stale Next.js in-memory manifest" class of
silent bugs where the served HTML references a CSS/JS chunk that
returns non-200 (usually because `next build` ran mid-process-lifetime
and the pm2 dashboard process kept its old manifest). HTTP 200 on `/`
alone doesn't reveal the bug — only fetching the referenced chunks
does.

Tests use httpx's MockTransport so we can drive every failure mode
deterministically without requiring a running dashboard.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.models.ops_alert import OpsAlert
from app.workers.tasks import dashboard_asset_probe_task as probe


HTML_OK = (
    b'<!DOCTYPE html><html><head>'
    b'<link rel="stylesheet" href="/_next/static/chunks/a1b2c3.css"/>'
    b'<script src="/_next/static/chunks/d4e5f6.js"></script>'
    b'</head><body>ok</body></html>'
)

HTML_WITH_MISSING_CSS = (
    b'<!DOCTYPE html><html><head>'
    b'<link rel="stylesheet" href="/_next/static/chunks/MISSING.css"/>'
    b'<script src="/_next/static/chunks/d4e5f6.js"></script>'
    b'</head><body>broken</body></html>'
)


_REAL_HTTPX_CLIENT = httpx.Client


def _make_client(handler):
    """Return a real httpx.Client wired to a MockTransport driven by handler.
    Bypasses any monkeypatch on httpx.Client so we don't recurse into
    ourselves when patching the probe's httpx module reference."""
    transport = httpx.MockTransport(handler)
    return _REAL_HTTPX_CLIENT(transport=transport, follow_redirects=False)


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """Each test gets a clean slate for the in-process cooldown set."""
    from app.services.observability_spikes import reset_test_cooldowns
    reset_test_cooldowns()
    yield
    reset_test_cooldowns()


def test_happy_path_no_alert(db, monkeypatch):
    """All probed pages return 200, all chunks return 200 → no alert."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path in ("/", "/app", "/pricing"):
            return httpx.Response(200, content=HTML_OK)
        if request.method == "HEAD" and "/_next/" in request.url.path:
            return httpx.Response(200)
        return httpx.Response(404)

    monkeypatch.setattr(probe.httpx, "Client", lambda **_: _make_client(handler))
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)

    probe.run()

    alerts = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == probe._ALERT_TYPE)
        .all()
    )
    assert len(alerts) == 0


def test_missing_chunk_raises_alert(db, monkeypatch):
    """HTML references a chunk that HEAD returns 404 → critical alert."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=HTML_WITH_MISSING_CSS)
        if request.method == "HEAD":
            if "MISSING" in request.url.path:
                return httpx.Response(500)
            return httpx.Response(200)
        return httpx.Response(404)

    monkeypatch.setattr(probe.httpx, "Client", lambda **_: _make_client(handler))
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)

    probe.run()

    alerts = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == probe._ALERT_TYPE)
        .all()
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "critical"
    assert a.source == "dashboard_asset_probe"
    # Detail payload contains the list of failures and the remedy string.
    import json as _json
    detail = _json.loads(a.detail) if isinstance(a.detail, str) else a.detail
    assert detail["failure_count"] >= 1
    assert any("MISSING.css" in f for f in detail["failures"])
    assert "deploy.sh" in detail["remedy"]


def test_cooldown_dedups_within_hour(db, monkeypatch):
    """Two consecutive failing probes in the same hour emit only one alert."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=HTML_WITH_MISSING_CSS)
        return httpx.Response(500 if "MISSING" in request.url.path else 200)

    monkeypatch.setattr(probe.httpx, "Client", lambda **_: _make_client(handler))
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)

    probe.run()
    probe.run()  # should dedup

    alerts = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == probe._ALERT_TYPE)
        .all()
    )
    assert len(alerts) == 1


def test_dashboard_unreachable_silent_skip(db, monkeypatch):
    """If the dashboard itself is down, skip silently (no alert)."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Simulate service-down: every GET returns 503
        return httpx.Response(503)

    monkeypatch.setattr(probe.httpx, "Client", lambda **_: _make_client(handler))
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)

    probe.run()

    alerts = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == probe._ALERT_TYPE)
        .all()
    )
    assert len(alerts) == 0


def test_alert_type_excluded_from_bugfix_pipeline():
    """dashboard_asset_drift must be in the bugfix_pipeline exclusion list —
    the remedy is `pm2 restart`, NOT a code patch."""
    from app.services.bugfix_pipeline import _PIPELINE_INTERNAL_ALERT_TYPES
    assert "dashboard_asset_drift" in _PIPELINE_INTERNAL_ALERT_TYPES


def test_should_run_respects_interval(monkeypatch):
    """The 5-min cooldown prevents the probe from running every cycle."""
    probe._last_run = None
    assert probe.should_run() is True
    probe.mark_done()
    assert probe.should_run() is False
    # Fast-forward: set _last_run into the past beyond the interval
    probe._last_run = probe.time.monotonic() - probe._INTERVAL_S - 1
    assert probe.should_run() is True
    probe._last_run = None  # clean up for other tests
