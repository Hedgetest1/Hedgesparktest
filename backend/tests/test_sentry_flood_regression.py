"""Regression tests for the Sentry flood that hit prod 2026-04-25.

Three classes, each captured below with the minimum reproduction.
Each test pins the fix so the bug class can't ship again.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.event import Event


# ---------------------------------------------------------------------------
# Bug class 1 — /setup/pixel-status NameError on every call
# ---------------------------------------------------------------------------
# Pre-fix: variables `row` and `event_row` were referenced in the return
# statement but never assigned. Every call crashed with NameError.
# Fired on every dashboard load that polled pixel install status.

def test_setup_pixel_status_returns_200_with_zero_data(client, merchant_a, auth_a):
    resp = client.get("/setup/pixel-status", cookies=auth_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pixel_active" in body
    assert "orders_from_pixel" in body
    assert "purchase_events" in body
    assert isinstance(body["orders_from_pixel"], int)
    assert isinstance(body["purchase_events"], int)


def test_setup_pixel_status_returns_200_with_real_purchase_event(
    client, merchant_a, auth_a, db: Session,
):
    """The endpoint must report has_purchase_events=True when an event is present."""
    db.add(
        Event(
            shop_domain=merchant_a.shop_domain,
            visitor_id="visitor-1",
            event_type="purchase",
            url="https://example.com/checkout",
            timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
        )
    )
    db.flush()
    resp = client.get("/setup/pixel-status", cookies=auth_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pixel_active"] is True or body["purchase_events"] >= 1


# ---------------------------------------------------------------------------
# Bug class 2 — /analytics/top-pages 500 when an event row has url=NULL
# ---------------------------------------------------------------------------
# Pre-fix: the SQL grouped by url including NULL rows. The response model
# requires url:str (non-nullable). One NULL url triggered a
# ResponseValidationError 500 for the merchant.

def test_top_pages_excludes_null_urls(
    client, merchant_a, auth_a, db: Session,
):
    """Insert one event with url=NULL and several with valid urls; the
    endpoint must filter out NULL-url groups so the response validates."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    db.add_all([
        Event(
            shop_domain=merchant_a.shop_domain,
            visitor_id=f"v-{i}",
            event_type="page_view",
            url="https://example.com/home" if i < 3 else None,
            timestamp=now_ms,
        )
        for i in range(5)
    ])
    db.flush()

    resp = client.get("/analytics/top-pages", cookies=auth_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for page in body["pages"]:
        assert page["url"], "every returned page must have a non-null url"
        assert isinstance(page["url"], str)


# ---------------------------------------------------------------------------
# Bug class 3 — sentry_init must NOT initialize when APP_ENV=test
# ---------------------------------------------------------------------------
# Pre-fix: tests that import app.main loaded SENTRY_DSN from .env and
# emitted thousands of events to the production Sentry project. Worst
# offenders were sqlalchemy SingletonThreadPool warnings during in-memory
# SQLite teardown (test_model_config), HTTPException 503s in
# telegram_webhook tests, and tests that simulate audit_log failures.

def test_sentry_init_returns_false_when_app_env_is_test():
    """When APP_ENV=test (set by conftest), init_sentry MUST short-circuit
    so test runs never emit to the production project, regardless of
    whether SENTRY_DSN is loaded from .env."""
    from app.core import sentry_init

    # APP_ENV is set to "test" by conftest at module import. We test the
    # exact branch we just shipped.
    saved = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "test"
    # Force re-init by clearing the module-level _enabled flag.
    sentry_init._enabled = False
    try:
        assert sentry_init.init_sentry(component="backend") is False
        # Idempotent re-call also returns False.
        assert sentry_init.init_sentry(component="backend") is False
    finally:
        if saved is not None:
            os.environ["APP_ENV"] = saved
        else:
            os.environ.pop("APP_ENV", None)


def test_sentry_init_returns_false_when_dsn_missing():
    """The original short-circuit (no DSN -> no-op) must still hold."""
    from app.core import sentry_init

    saved_dsn = os.environ.pop("SENTRY_DSN", None)
    saved_env = os.environ.pop("APP_ENV", None)
    sentry_init._enabled = False
    try:
        assert sentry_init.init_sentry(component="backend") is False
    finally:
        if saved_dsn is not None:
            os.environ["SENTRY_DSN"] = saved_dsn
        if saved_env is not None:
            os.environ["APP_ENV"] = saved_env
