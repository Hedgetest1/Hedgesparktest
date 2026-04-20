"""
Session-persistence invariants for GET /auth/session.

Born 2026-04-20 after founder reported that returning to the dashboard
after a backend restart/deploy intermittently showed "No store
connected" — root cause was a combination of (a) missing retry logic
in the frontend and (b) the /auth/session redirect pointing at the
marketing landing ("/") instead of the dashboard ("/app"), adding an
extra client-side hop that amplified the flash.

This file nails both invariants as tests so a future refactor cannot
silently regress them:

    1. /auth/session?shop=<active> → 302 redirect to /app (NOT / or
       /?shop=) — destination must be dashboard-direct.
    2. The response must set the hs_session cookie.
    3. /auth/session?shop=<unknown> → 302 redirect to the install flow
       (unchanged safety behavior).

If /auth/session is ever refactored, these tests fail BEFORE the
change lands on main and the regression can't reach production.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import SessionLocal
from app.core.merchant_session import SESSION_COOKIE_NAME
from app.models.merchant import Merchant


_TEST_SHOP = "authtest-redirect-probe.myshopify.com"


def _ensure_test_merchant(db: Session) -> None:
    """Insert (or refresh) an active merchant for the redirect test."""
    row = db.query(Merchant).filter(Merchant.shop_domain == _TEST_SHOP).first()
    if row is None:
        row = Merchant(
            shop_domain=_TEST_SHOP,
            install_status="active",
            plan="lite",
            session_version=0,
            installed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(row)
    else:
        row.install_status = "active"
    db.commit()


def _cleanup_test_merchant(db: Session) -> None:
    db.query(Merchant).filter(Merchant.shop_domain == _TEST_SHOP).delete()
    db.commit()


def test_auth_session_redirects_to_app_route_for_active_merchant():
    """
    /auth/session must redirect directly to /app (dashboard), never to /
    (landing) — the landing detour is the root cause of the 2026-04-20
    session-persistence incident.
    """
    db = SessionLocal()
    try:
        _ensure_test_merchant(db)
        client = TestClient(app)
        r = client.get(
            f"/auth/session?shop={_TEST_SHOP}",
            follow_redirects=False,
        )
        assert r.status_code == 302, f"expected 302 redirect, got {r.status_code}"
        location = r.headers.get("location", "")
        assert location, "auth/session must emit a Location header"

        # The critical invariant: destination path must be /app, not /
        # (the marketing landing). Parse out path by stripping the base
        # URL if DASHBOARD_URL is set.
        dashboard_url = (os.getenv("DASHBOARD_URL") or "").rstrip("/")
        if dashboard_url and location.startswith(dashboard_url):
            path_and_query = location[len(dashboard_url):]
        else:
            path_and_query = location

        path = path_and_query.split("?", 1)[0]
        assert path == "/app", (
            f"auth/session must redirect to /app (dashboard), got {path!r}. "
            "Redirecting to / (landing) adds a client-side hop and breaks "
            "the session-recovery flow when the landing JS fails to run."
        )

        # Cookie must be set on the response.
        cookies = r.cookies
        assert SESSION_COOKIE_NAME in cookies, (
            f"auth/session must set the {SESSION_COOKIE_NAME} cookie, "
            f"got cookies={list(cookies.keys())}"
        )
    finally:
        _cleanup_test_merchant(db)
        db.close()


def test_auth_session_redirects_unknown_shop_to_install():
    """
    Unknown shops must be redirected to the install flow, not given a
    session. Preserves the pre-existing safety behavior.
    """
    client = TestClient(app)
    r = client.get(
        "/auth/session?shop=nonexistent-probe-do-not-insert.myshopify.com",
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers.get("location", "")
    assert "/auth/install" in location, (
        f"unknown shop must redirect to /auth/install, got {location!r}"
    )
    # Must NOT set a session cookie for an unknown shop.
    assert SESSION_COOKIE_NAME not in r.cookies


def test_auth_detect_is_fail_safe_disabled_by_default(monkeypatch):
    """
    `GET /auth/detect` MUST return 404 when `AUTO_DETECT_ENABLED` is unset
    OR set to anything other than "1"/"true"/"yes". This is the
    production-safety guarantee from
    `project_before_production_auto_detect_removal.md`: forgetting to
    remove the dev default-shop config in prod must be structurally
    harmless.

    Regression-blocker: if someone refactors the endpoint to "default
    on" or drops the env gate, this test fails BEFORE the change
    lands on main.
    """
    client = TestClient(app)

    # 1. Env completely unset → must 404, even if a default shop is
    # configured AND a merchant exists.
    monkeypatch.delenv("AUTO_DETECT_ENABLED", raising=False)
    r = client.get("/auth/detect")
    assert r.status_code == 404, (
        f"endpoint MUST 404 when AUTO_DETECT_ENABLED is unset; got {r.status_code}. "
        "This is the production-safety gate — see "
        "project_before_production_auto_detect_removal.md"
    )

    # 2. Env explicitly disabled → must 404.
    monkeypatch.setenv("AUTO_DETECT_ENABLED", "0")
    r = client.get("/auth/detect")
    assert r.status_code == 404

    monkeypatch.setenv("AUTO_DETECT_ENABLED", "false")
    r = client.get("/auth/detect")
    assert r.status_code == 404

    # 3. Garbage value → must 404 (fail closed).
    monkeypatch.setenv("AUTO_DETECT_ENABLED", "maybe")
    r = client.get("/auth/detect")
    assert r.status_code == 404
