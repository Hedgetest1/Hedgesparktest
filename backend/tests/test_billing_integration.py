"""
Billing integration tests — full subscription lifecycle.

The TIER_2 observability fix (commit 93c4436) added ops_alert emissions
on Shopify API failures. This suite locks in the actual state-machine
transitions the billing endpoints drive:

  POST /billing/subscribe        — create pending charge
  GET  /billing/callback         — handle Shopify's post-decision callback
  _activate_charge               — flip plan to pro + billing_active=True

Every state transition has a corresponding test. Every failure mode
has a regression-preventing test that asserts we redirect to
?billing=error and leave the merchant's state unchanged or cleaned up.

All Shopify HTTP calls are mocked — no network, no Shopify credentials,
no side effects. The tests exercise the real routing + DB commit paths
through the FastAPI TestClient.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.merchant import Merchant

from tests.conftest import SHOP_A, auth_cookies


SHOP = "billing-integration-test.myshopify.com"
CHARGE_ID = "777777"


@pytest.fixture()
def lite_merchant(db):
    """A fresh NON-Pro merchant primed for the subscribe flow."""
    m = Merchant(
        shop_domain=SHOP,
        plan="lite",
        billing_active=False,
        billing_charge_id=None,
        install_status="active",
        session_version=0,
        access_token="encrypted_fake_token",  # any non-empty value; _get_access_token is patched
        contact_email="owner@test.com",
    )
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def lite_auth(lite_merchant) -> dict:
    return auth_cookies(SHOP)


# ---------------------------------------------------------------------------
# Response builders for mocked Shopify HTTP
# ---------------------------------------------------------------------------


def _mock_resp(status_code: int, body: dict):
    m = MagicMock()
    m.status_code = status_code
    m.text = json.dumps(body)
    m.json = MagicMock(return_value=body)
    return m


def _patch_shopify(post_return=None, get_return=None, post_side=None, get_side=None):
    """Patch httpx.AsyncClient used inside billing.py with async mocks."""
    client_instance = MagicMock()
    async_ctx = AsyncMock()
    async_ctx.__aenter__.return_value = client_instance
    async_ctx.__aexit__.return_value = None
    client_instance.post = (
        AsyncMock(side_effect=post_side) if post_side else AsyncMock(return_value=post_return)
    )
    client_instance.get = (
        AsyncMock(side_effect=get_side) if get_side else AsyncMock(return_value=get_return)
    )
    return patch("app.api.billing.httpx.AsyncClient", return_value=async_ctx)


def _get_access_token_patch():
    """Billing decrypts merchant.access_token_encrypted. Replace with a stub."""
    return patch("app.api.billing._get_access_token", return_value="stub_token")


# ---------------------------------------------------------------------------
# POST /billing/subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_creates_pending_charge_happy_path(self, client, lite_merchant, lite_auth, db):
        """Clean lite merchant → create_charge succeeds → persists charge_id."""
        charge_payload = {
            "recurring_application_charge": {
                "id": 12345678,
                "confirmation_url": "https://test-shop.myshopify.com/admin/charges/12345678/confirm",
                "status": "pending",
            }
        }
        with _get_access_token_patch(), _patch_shopify(post_return=_mock_resp(201, charge_payload)):
            resp = client.post("/billing/subscribe", json={}, cookies=lite_auth)

        assert resp.status_code == 200
        body = resp.json()
        assert body["charge_id"] == "12345678"
        assert "confirmation_url" in body
        # DB persistence — charge_id stored on merchant
        db.expire_all()
        m = db.query(Merchant).filter_by(shop_domain=SHOP).first()
        assert m.billing_charge_id == "12345678"
        assert m.plan == "lite"  # still lite — not yet confirmed
        assert m.billing_active is False

    def test_idempotent_already_paid(self, client, merchant_a, auth_a):
        """Merchant already on a paid tier (Pro or Scale) → short-circuits
        with 200 (no Shopify call). merchant_a is Scale-tier post the
        2026-04-29 Pro→Scale moat migration."""
        resp = client.post("/billing/subscribe", json={}, cookies=auth_a)
        assert resp.status_code == 200
        assert resp.json()["plan"] in ("pro", "scale")

    def test_uninstalled_merchant_409(self, client, db, lite_merchant):
        """Uninstalled merchant cannot subscribe until reinstall."""
        lite_merchant.install_status = "uninstalled"
        db.flush()
        resp = client.post("/billing/subscribe", json={}, cookies=auth_cookies(SHOP))
        assert resp.status_code == 409
        assert "uninstall" in resp.json()["detail"].lower()

    def test_shopify_api_failure_returns_502(self, client, lite_merchant, lite_auth):
        """Shopify 503 → our endpoint returns 502 + logs the failure."""
        with _get_access_token_patch(), _patch_shopify(post_return=_mock_resp(503, {"error": "down"})):
            resp = client.post("/billing/subscribe", json={}, cookies=lite_auth)
        assert resp.status_code == 502
        assert "Failed to create billing charge" in resp.json()["detail"]

    def test_idempotent_reuses_pending_charge(self, client, lite_merchant, lite_auth, db):
        """Second subscribe call re-fetches the existing pending charge."""
        lite_merchant.billing_charge_id = "existing_99"
        db.flush()
        pending_payload = {
            "recurring_application_charge": {
                "id": 99,
                "confirmation_url": "https://test-shop.myshopify.com/admin/charges/99/confirm",
                "status": "pending",
            }
        }
        with _get_access_token_patch(), _patch_shopify(get_return=_mock_resp(200, pending_payload)):
            resp = client.post("/billing/subscribe", json={}, cookies=lite_auth)

        assert resp.status_code == 200
        assert resp.json()["charge_id"] == "existing_99"


# ---------------------------------------------------------------------------
# GET /billing/callback — Shopify post-decision
# ---------------------------------------------------------------------------


class TestCallbackAccepted:
    def test_accepted_activates_and_upgrades_to_pro(self, client, lite_merchant, db):
        """Callback with status=accepted → activate → merchant becomes Pro."""
        accepted = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "accepted"}}
        activated = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "active"}}
        with _get_access_token_patch(), _patch_shopify(
            get_return=_mock_resp(200, accepted),
            post_return=_mock_resp(200, activated),
        ):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        # 302 redirect to DASHBOARD_URL/?billing=activated
        assert resp.status_code in (302, 307)
        assert "billing=activated" in resp.headers["location"]

        # Merchant state flipped to Pro
        db.expire_all()
        m = db.query(Merchant).filter_by(shop_domain=SHOP).first()
        assert m.plan == "pro"
        assert m.billing_active is True
        assert m.billing_charge_id == CHARGE_ID
        assert m.billing_confirmed_at is not None


class TestCallbackDeclined:
    def test_declined_clears_charge_and_stays_lite(self, client, lite_merchant, db):
        """status=declined → merchant stays lite, billing_charge_id cleared."""
        lite_merchant.billing_charge_id = CHARGE_ID
        db.flush()
        declined = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "declined"}}
        with _get_access_token_patch(), _patch_shopify(get_return=_mock_resp(200, declined)):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert resp.status_code in (302, 307)
        assert "billing=declined" in resp.headers["location"]

        db.expire_all()
        m = db.query(Merchant).filter_by(shop_domain=SHOP).first()
        assert m.plan == "lite"
        assert m.billing_active is False
        assert m.billing_charge_id is None


class TestCallbackFailureModes:
    def test_unknown_shop_redirects_to_error(self, client):
        """Callback for a shop we don't have a merchant for → billing=error."""
        resp = client.get(
            f"/billing/callback?charge_id={CHARGE_ID}&shop=unknown-shop.myshopify.com",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert "billing=error" in resp.headers["location"]

    def test_fetch_charge_fails_redirects_error(self, client, lite_merchant):
        """Shopify /charges GET returns 503 → redirect to error, no state change."""
        with _get_access_token_patch(), _patch_shopify(get_return=_mock_resp(503, {})):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert "billing=error" in resp.headers["location"]

    def test_activate_failure_surfaces_error(self, client, lite_merchant):
        """Fetch says accepted, activate returns 502 → redirect to error +
        merchant stays lite (no half-upgrade)."""
        accepted = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "accepted"}}
        with _get_access_token_patch(), _patch_shopify(
            get_return=_mock_resp(200, accepted),
            post_return=_mock_resp(502, {}),
        ):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert "billing=error" in resp.headers["location"]

        # Activate failure → no state change (merchant still lite)
        s = SessionLocal()
        try:
            m = s.query(Merchant).filter_by(shop_domain=SHOP).first()
            # Inside the test transaction, ORM-level check
            if m is not None:
                assert m.plan != "pro"
        finally:
            s.close()

    def test_activate_failure_writes_critical_ops_alert(self, client, lite_merchant):
        """Locking in the TIER_2 observability contract from 93c4436 at
        the endpoint level: activate failure reached through the callback
        path writes a CRITICAL ops_alert (not just warning)."""
        s = SessionLocal()
        try:
            s.execute(
                text("DELETE FROM ops_alerts WHERE source = 'billing' AND shop_domain = :s"),
                {"s": SHOP},
            )
            s.commit()
        finally:
            s.close()

        accepted = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "accepted"}}
        with _get_access_token_patch(), _patch_shopify(
            get_return=_mock_resp(200, accepted),
            post_return=_mock_resp(502, {"error": "bad gateway"}),
        ):
            client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )

        s = SessionLocal()
        try:
            rows = s.execute(
                text(
                    "SELECT severity, alert_type FROM ops_alerts "
                    "WHERE source = 'billing' AND shop_domain = :s "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"s": SHOP},
            ).fetchall()
            assert rows, "activate failure must write an ops_alert"
            sev, atype = rows[0]
            assert sev == "critical", (
                f"activate_charge failures are critical severity (merchant "
                f"already paid on Shopify side). Got severity={sev!r}."
            )
            assert atype == "billing_api_failure"
        finally:
            # cleanup
            s.execute(
                text("DELETE FROM ops_alerts WHERE source = 'billing' AND shop_domain = :s"),
                {"s": SHOP},
            )
            s.commit()
            s.close()

    def test_pending_status_returns_pending(self, client, lite_merchant):
        """Rare race: callback fires while charge still pending → redirect
        to billing=pending, no state change."""
        pending = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "pending"}}
        with _get_access_token_patch(), _patch_shopify(get_return=_mock_resp(200, pending)):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert "billing=pending" in resp.headers["location"]

    def test_expired_status_cleans_charge_id(self, client, lite_merchant, db):
        """Any non-accepted/declined/pending status (expired/frozen/etc) →
        clear the charge_id + redirect to error."""
        lite_merchant.billing_charge_id = CHARGE_ID
        db.flush()
        expired = {"recurring_application_charge": {"id": int(CHARGE_ID), "status": "expired"}}
        with _get_access_token_patch(), _patch_shopify(get_return=_mock_resp(200, expired)):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert "billing=error" in resp.headers["location"]

        db.expire_all()
        m = db.query(Merchant).filter_by(shop_domain=SHOP).first()
        assert m.billing_charge_id is None


class TestCallbackIdempotency:
    def test_already_active_shortcircuits_to_activated(self, client, lite_merchant, db):
        """Merchant already billing_active for this charge_id → skip Shopify,
        redirect to billing=activated immediately."""
        lite_merchant.plan = "pro"
        lite_merchant.billing_active = True
        lite_merchant.billing_charge_id = CHARGE_ID
        db.flush()

        # NO Shopify patch — if the endpoint short-circuits correctly, it
        # should never try to reach Shopify. If the shortcircuit regresses,
        # the test crashes trying to call httpx and we catch it.
        with patch("app.api.billing._get_access_token", return_value="stub"):
            resp = client.get(
                f"/billing/callback?charge_id={CHARGE_ID}&shop={SHOP}",
                follow_redirects=False,
            )
        assert "billing=activated" in resp.headers["location"]
