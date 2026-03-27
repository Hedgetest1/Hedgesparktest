"""Tests for merchant session authentication (deps.py + merchant_session.py)."""
from tests.conftest import SHOP_A, auth_cookies


def test_authenticated_request_succeeds(client, auth_a):
    """Valid session cookie → 200 on authenticated endpoint."""
    resp = client.get("/merchant/me", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    assert data["shop_domain"] == SHOP_A


def test_no_cookie_returns_401(client, merchant_a):
    """Missing session cookie → 401."""
    resp = client.get("/merchant/me")
    assert resp.status_code == 401


def test_invalid_cookie_returns_401(client, merchant_a):
    """Garbage cookie value → 401."""
    resp = client.get("/merchant/me", cookies={"hs_session": "not-a-valid-jwt"})
    assert resp.status_code == 401


def test_expired_session_version_returns_401(client, merchant_a, db):
    """Token with old session_version (sv=0) rejected when merchant bumps to sv=1."""
    # Create token with sv=0
    cookies = auth_cookies(SHOP_A, session_version=0)
    # Bump merchant's session_version to 1
    merchant_a.session_version = 1
    db.flush()
    resp = client.get("/merchant/me", cookies=cookies)
    assert resp.status_code == 401
