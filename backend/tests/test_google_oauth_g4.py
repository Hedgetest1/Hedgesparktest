"""G4 — Google Sheets export OAuth tests (mocked).

Verifies the OAuth flow + status + export endpoints work end-to-end
without requiring real Google Cloud Console credentials. The actual
HTTP calls to oauth2.googleapis.com / sheets.googleapis.com are
mocked so this suite runs offline + deterministically.

Coverage:
  * status: configured=false when env missing → endpoint still 200
  * status: configured=true when env set, connected=false fresh
  * status: connected=true after store_oauth_tokens
  * /auth/google/start: 503 when not configured
  * /auth/google/start: 302 to accounts.google.com when configured
  * /auth/google/callback: missing params → redirect with reason
  * /auth/google/callback: state unknown → redirect error
  * /auth/google/callback: happy path → stores tokens, redirects success
  * /auth/google/disconnect: clears state
  * /analytics/export-to-sheets: 409 when not connected
  * /analytics/export-to-sheets: 503 when not configured
  * Tenant isolation
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant
from app.services import google_sheets as gs_svc
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


@pytest.fixture(autouse=True)
def reset_oauth_module_state():
    """Each test starts with no in-memory OAuth state + access cache."""
    gs_svc._oauth_state_map = {}  # type: ignore[attr-defined]  -- module-level dict
    gs_svc._access_token_cache = {}
    # Ensure env vars are absent unless test sets them.
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        os.environ.pop(k, None)
    yield


@pytest.fixture
def configured_oauth():
    """Inject fake OAuth env vars for the duration of the test."""
    os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "fake-client-id.apps.googleusercontent.com"
    os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "GOCSPX-fakesecret"
    yield
    os.environ.pop("GOOGLE_OAUTH_CLIENT_ID", None)
    os.environ.pop("GOOGLE_OAUTH_CLIENT_SECRET", None)


# ════════════════════════════════════════════════════════════════════
# /merchant/google/status
# ════════════════════════════════════════════════════════════════════


def test_status_configured_false_when_env_missing(client, merchant_a, auth_a):
    r = client.get("/merchant/google/status", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["connected"] is False


def test_status_configured_true_when_env_set(
    client, merchant_a, auth_a, configured_oauth,
):
    r = client.get("/merchant/google/status", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["connected"] is False


def test_status_connected_true_after_store(
    client, merchant_a, auth_a, configured_oauth, db,
):
    from app.services.google_sheets import store_oauth_tokens
    store_oauth_tokens(db, shop=SHOP_A, refresh_token="rt-123", email="founder@brand.com")
    db.flush()
    r = client.get("/merchant/google/status", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["connected"] is True
    assert body["email"] == "founder@brand.com"


def test_status_unauth_returns_401(client):
    r = client.get("/merchant/google/status")
    assert r.status_code == 401


# ════════════════════════════════════════════════════════════════════
# /auth/google/start
# ════════════════════════════════════════════════════════════════════


def test_start_returns_503_when_not_configured(client, merchant_a, auth_a):
    r = client.get("/auth/google/start", cookies=auth_a, follow_redirects=False)
    assert r.status_code == 503


def test_start_redirects_to_google_when_configured(
    client, merchant_a, auth_a, configured_oauth,
):
    r = client.get("/auth/google/start", cookies=auth_a, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers.get("location", "")
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "scope=https" in location and "drive.file" in location
    assert "access_type=offline" in location
    assert "prompt=consent" in location


# ════════════════════════════════════════════════════════════════════
# /auth/google/callback
# ════════════════════════════════════════════════════════════════════


def test_callback_missing_params_redirects_error(client, configured_oauth):
    r = client.get("/auth/google/callback", follow_redirects=False)
    assert r.status_code == 302
    assert "google=error" in r.headers["location"]
    assert "missing_params" in r.headers["location"]


def test_callback_state_unknown_redirects_error(client, configured_oauth):
    r = client.get(
        "/auth/google/callback?code=fake-code&state=unknown-state",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "state_unknown" in r.headers["location"]


def test_callback_user_denies_redirects_error(client, configured_oauth):
    r = client.get(
        "/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "google=error" in r.headers["location"]


def test_callback_happy_path_stores_tokens(
    client, merchant_a, auth_a, configured_oauth, db, monkeypatch,
):
    """Mock Google's token + userinfo endpoints, walk full callback."""
    # Step 1: hit /auth/google/start to populate the state map.
    r = client.get("/auth/google/start", cookies=auth_a, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    # Extract state from the redirect URL.
    from urllib.parse import urlparse, parse_qs
    state = parse_qs(urlparse(location).query)["state"][0]

    # Mock exchange_code_for_tokens + fetch_userinfo so the callback
    # doesn't need a real Google.
    monkeypatch.setattr(
        "app.api.google_oauth.exchange_code_for_tokens",
        lambda code: {
            "refresh_token": "fake-refresh-token-rt",
            "access_token": "fake-access-token-at",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        "app.api.google_oauth.fetch_userinfo",
        lambda access_token: {"email": "founder@example.com", "verified_email": True},
    )

    r = client.get(
        f"/auth/google/callback?code=fake-google-code&state={state}",
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "google=connected" in r.headers["location"]

    # Verify the merchant row was updated.
    db.expire_all()
    merchant = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    assert merchant is not None
    assert merchant.encrypted_google_refresh_token is not None
    assert merchant.google_oauth_email == "founder@example.com"
    assert merchant.google_oauth_connected_at is not None
    # The encrypted value round-trips back to the original via decrypt_token.
    assert decrypt_token(merchant.encrypted_google_refresh_token) == "fake-refresh-token-rt"


# ════════════════════════════════════════════════════════════════════
# /auth/google/disconnect
# ════════════════════════════════════════════════════════════════════


def test_disconnect_clears_state(client, merchant_a, auth_a, configured_oauth, db):
    from app.services.google_sheets import store_oauth_tokens
    store_oauth_tokens(db, shop=SHOP_A, refresh_token="rt", email="e@x.com")
    db.flush()
    r = client.post(
        "/auth/google/disconnect", cookies=auth_a,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    db.expire_all()
    merchant = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    assert merchant.encrypted_google_refresh_token is None
    assert merchant.google_oauth_email is None


# ════════════════════════════════════════════════════════════════════
# /analytics/export-to-sheets
# ════════════════════════════════════════════════════════════════════


def test_export_returns_503_when_not_configured(client, merchant_a, auth_a):
    payload = {"title": "Test", "headers": ["A", "B"], "rows": [["1", "2"]]}
    r = client.post(
        "/analytics/export-to-sheets",
        cookies=auth_a, json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 503


def test_export_returns_409_when_not_connected(
    client, merchant_a, auth_a, configured_oauth,
):
    payload = {"title": "Test", "headers": ["A"], "rows": [["1"]]}
    r = client.post(
        "/analytics/export-to-sheets",
        cookies=auth_a, json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 409, r.text
    assert "Connect Google Sheets" in r.text


def test_export_happy_path_returns_url(
    client, merchant_a, auth_a, configured_oauth, db, monkeypatch,
):
    """Mock create_export_sheet to bypass real Google API."""
    from app.services.google_sheets import store_oauth_tokens
    store_oauth_tokens(db, shop=SHOP_A, refresh_token="rt", email="e@x.com")
    db.flush()

    monkeypatch.setattr(
        "app.api.google_oauth.create_export_sheet",
        lambda db, *, shop, title, headers, rows: {
            "spreadsheet_id": "sheet-id-123",
            "url": "https://docs.google.com/spreadsheets/d/sheet-id-123",
            "title": title,
        },
    )

    payload = {
        "title": "Q4 Revenue Export",
        "headers": ["product", "revenue"],
        "rows": [["A", "100"], ["B", "200"]],
    }
    r = client.post(
        "/analytics/export-to-sheets",
        cookies=auth_a, json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spreadsheet_id"] == "sheet-id-123"
    assert "docs.google.com" in body["url"]
    assert body["title"] == "Q4 Revenue Export"


# ════════════════════════════════════════════════════════════════════
# Tenant isolation
# ════════════════════════════════════════════════════════════════════


def test_tenant_isolation_status(
    client, merchant_a, merchant_b, auth_a, auth_b, configured_oauth, db,
):
    """Connecting shop A must not surface as connected for shop B."""
    from app.services.google_sheets import store_oauth_tokens
    store_oauth_tokens(db, shop=SHOP_A, refresh_token="rt-a", email="a@x.com")
    db.flush()

    ra = client.get("/merchant/google/status", cookies=auth_a)
    rb = client.get("/merchant/google/status", cookies=auth_b)
    assert ra.json()["connected"] is True
    assert rb.json()["connected"] is False


def test_tenant_isolation_disconnect(
    client, merchant_a, merchant_b, auth_a, auth_b, configured_oauth, db,
):
    """Shop A's disconnect must not affect shop B's connection."""
    from app.services.google_sheets import store_oauth_tokens
    store_oauth_tokens(db, shop=SHOP_A, refresh_token="rt-a", email="a@x.com")
    store_oauth_tokens(db, shop=SHOP_B, refresh_token="rt-b", email="b@x.com")
    db.flush()

    client.post(
        "/auth/google/disconnect", cookies=auth_a,
        headers={"Content-Type": "application/json"},
    )
    db.expire_all()

    a = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    b = db.query(Merchant).filter(Merchant.shop_domain == SHOP_B).first()
    assert a.encrypted_google_refresh_token is None
    assert b.encrypted_google_refresh_token is not None
