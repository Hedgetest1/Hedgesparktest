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


def test_force_logout_bumps_session_version(client, merchant_a, db):
    """POST /ops/force-logout?shop=X increments merchants.session_version
    and the response body reports both old and new values. This is the
    admin trigger for S14 — session invalidation in response to a
    security incident or token leak."""
    import os

    from app.models.merchant import Merchant

    initial_sv = merchant_a.session_version or 0
    ops_key = os.environ.get("DASHBOARD_API_KEY", "")
    assert ops_key, "DASHBOARD_API_KEY must be set in test env"

    resp = client.post(
        f"/ops/force-logout?shop={SHOP_A}",
        headers={"X-API-Key": ops_key, "Content-Type": "application/json"},
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shop_domain"] == SHOP_A
    assert body["previous_session_version"] == initial_sv
    assert body["new_session_version"] == initial_sv + 1

    # DB row reflects the bump
    db.refresh(merchant_a)
    assert merchant_a.session_version == initial_sv + 1


def test_force_logout_invalidates_existing_session(client, merchant_a, db):
    """After /ops/force-logout, a cookie minted BEFORE the bump is
    rejected with 401. This is the end-to-end path S14 asserts in
    E2E, verified at the backend level here so a regression shows
    up in unit tests too."""
    import os

    # Mint a cookie at current sv
    initial_sv = merchant_a.session_version or 0
    cookies = auth_cookies(SHOP_A, session_version=initial_sv)
    r_before = client.get("/merchant/me", cookies=cookies)
    assert r_before.status_code == 200

    # Force-logout
    ops_key = os.environ["DASHBOARD_API_KEY"]
    r_logout = client.post(
        f"/ops/force-logout?shop={SHOP_A}",
        headers={"X-API-Key": ops_key, "Content-Type": "application/json"},
        json={},
    )
    assert r_logout.status_code == 200

    # Same pre-logout cookie → now 401
    r_after = client.get("/merchant/me", cookies=cookies)
    assert r_after.status_code == 401, r_after.text


def test_force_logout_unknown_shop_returns_404(client):
    """Force-logout on a shop that does not exist returns 404, not 500.
    Keeps the admin tool honest about which shops it affected."""
    import os
    ops_key = os.environ["DASHBOARD_API_KEY"]
    r = client.post(
        "/ops/force-logout?shop=does-not-exist-ever-9999.myshopify.com",
        headers={"X-API-Key": ops_key, "Content-Type": "application/json"},
        json={},
    )
    assert r.status_code == 404


def test_force_logout_requires_operator_key(client, merchant_a):
    """Force-logout without X-API-Key returns 401, not 500."""
    r = client.post(
        f"/ops/force-logout?shop={SHOP_A}",
        headers={"Content-Type": "application/json"},
        json={},
    )
    assert r.status_code == 401


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
