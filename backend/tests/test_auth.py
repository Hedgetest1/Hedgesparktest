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


def test_unknown_shop_jwt_returns_401(client, db):
    """A JWT for a shop that does NOT exist in the merchants table is
    rejected at the auth gate. Prior to this test the path returned
    shop unconditionally on valid-signature, making auth permissive
    for uninstalled shops and forged-shop JWTs minted by an attacker
    with access to the signing secret."""
    from app.models.merchant import Merchant

    # Pick a shop domain that is guaranteed to not exist.
    bogus_shop = "does-not-exist-ever-12345.myshopify.com"
    db.query(Merchant).filter(Merchant.shop_domain == bogus_shop).delete()
    db.flush()

    cookies = auth_cookies(bogus_shop, session_version=0)
    resp = client.get("/merchant/me", cookies=cookies)
    assert resp.status_code == 401
    # Error body names the reinstall remediation — merchants who
    # reach this state need to reinstall, not just re-log-in.
    assert "reinstall" in resp.json().get("detail", "").lower()
