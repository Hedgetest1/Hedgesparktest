"""Lock the 2026-05-07 synthetic-UA guard on /frontend-errors.

Closes #124389 ChunkLoadError class: a headless Chrome / playwright /
puppeteer test hits a stale chunk → fires ops_alert → critical noise.
Real merchants unaffected (audit_dashboard_live verified clean
during alert).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


_PAYLOAD = {
    "error_type": "ChunkLoadError",
    "message": "Failed to load chunk /_next/static/chunks/x.js",
    "url": "http://127.0.0.1:3000/app/lite",
    "component": "lite",
}


def _post(ua: str) -> dict:
    with TestClient(app) as c:
        r = c.post("/ops/frontend-errors", json={**_PAYLOAD, "user_agent": ua})
        return r.json()


def test_real_browser_ua_creates_alert():
    """Real merchant browsers MUST still ingest."""
    real_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    out = _post(real_ua)
    assert out.get("accepted") is True
    assert out.get("source", "").startswith("fe:")  # writer path


def test_headless_chrome_suppressed():
    out = _post("Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/147.0.7727.15")
    assert out.get("accepted") is True
    assert out.get("source") == "synthetic_ua"


def test_playwright_suppressed():
    out = _post("playwright-test/1.0")
    assert out.get("source") == "synthetic_ua"


def test_puppeteer_suppressed():
    out = _post("Mozilla/5.0 ... Puppeteer ...")
    assert out.get("source") == "synthetic_ua"
