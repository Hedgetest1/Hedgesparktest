"""Tests for Klaviyo integrations API (save, test, disconnect)."""
from app.core.token_crypto import is_encrypted
from app.models.merchant import Merchant
from tests.conftest import SHOP_A


def test_save_klaviyo_key_encrypts(client, auth_a, db):
    """PUT /merchant/integrations/klaviyo saves encrypted key."""
    resp = client.put(
        "/merchant/integrations/klaviyo",
        json={"klaviyo_private_key": "pk_test_abcdefghij123456"},
        cookies=auth_a,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "unverified"
    assert data["has_key"] is True
    # Key in DB must be encrypted
    m = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    assert is_encrypted(m.encrypted_klaviyo_key)


def test_save_key_returns_masked_hint(client, auth_a, db):
    """Saved key response contains masked hint, never raw key."""
    client.put(
        "/merchant/integrations/klaviyo",
        json={"klaviyo_private_key": "pk_test_xyz789abcdef"},
        cookies=auth_a,
    )
    resp = client.get("/merchant/integrations", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    hint = data["klaviyo"]["key_hint"]
    assert hint is not None
    assert hint.startswith("****")
    assert len(hint) <= 8
    assert "pk_test" not in hint  # raw key never in response


def test_disconnect_clears_key(client, auth_a, db):
    """DELETE clears key and resets status."""
    # First save a key
    client.put(
        "/merchant/integrations/klaviyo",
        json={"klaviyo_private_key": "pk_test_disconnect_me"},
        cookies=auth_a,
    )
    # Then disconnect (Content-Type required by CSRF middleware)
    resp = client.delete(
        "/merchant/integrations/klaviyo",
        cookies=auth_a,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_connected"
    assert data["has_key"] is False
    # DB must be cleared
    m = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    assert m.encrypted_klaviyo_key is None


def test_save_rejects_short_key(client, auth_a):
    """Key shorter than 8 chars → 400."""
    resp = client.put(
        "/merchant/integrations/klaviyo",
        json={"klaviyo_private_key": "short"},
        cookies=auth_a,
    )
    assert resp.status_code == 422  # Pydantic validation (min_length=8)


def test_unauthenticated_save_rejected(client, merchant_a):
    """No session cookie → 401."""
    resp = client.put(
        "/merchant/integrations/klaviyo",
        json={"klaviyo_private_key": "pk_test_no_auth_12345"},
    )
    assert resp.status_code == 401
