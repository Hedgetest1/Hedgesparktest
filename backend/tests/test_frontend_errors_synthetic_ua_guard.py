"""Lock the 2026-05-07 synthetic-UA guard on /frontend-errors.

Closes #124389 ChunkLoadError class: a headless Chrome / playwright /
puppeteer test hits a stale chunk → fires ops_alert → critical noise.
Real merchants unaffected (audit_dashboard_live verified clean
during alert).

Pre-2026-05-13 this file constructed its own `TestClient(app)` which
bypassed the conftest `client` fixture's `dependency_overrides[get_db]`,
so the real-browser-UA test (which DOES write an ops_alert) leaked a
row to production on every preflight pytest cycle (alert id 137253 +
137512 observed in prod). Switched to the conftest `client` fixture
which wraps every `get_db()` in the test's SAVEPOINT — rolls back at
teardown.
"""
from __future__ import annotations


_PAYLOAD = {
    "error_type": "ChunkLoadError",
    "message": "Failed to load chunk /_next/static/chunks/x.js",
    "url": "http://127.0.0.1:3000/app/lite",
    "component": "lite",
}


def _post(client, ua: str) -> dict:
    r = client.post("/ops/frontend-errors", json={**_PAYLOAD, "user_agent": ua})
    return r.json()


def test_real_browser_ua_creates_alert(client):
    """Real merchant browsers MUST still ingest."""
    real_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    out = _post(client, real_ua)
    assert out.get("accepted") is True
    assert out.get("source", "").startswith("fe:")  # writer path


def test_headless_chrome_suppressed(client):
    out = _post(client, "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/147.0.7727.15")
    assert out.get("accepted") is True
    assert out.get("source") == "synthetic_ua"


def test_playwright_suppressed(client):
    out = _post(client, "playwright-test/1.0")
    assert out.get("source") == "synthetic_ua"


def test_puppeteer_suppressed(client):
    out = _post(client, "Mozilla/5.0 ... Puppeteer ...")
    assert out.get("source") == "synthetic_ua"
