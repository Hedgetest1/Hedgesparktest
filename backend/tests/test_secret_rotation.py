"""Tests for secret rotation, operator key rotation, and delivery tracking."""
import os
from unittest.mock import patch

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Token encryption rotation (v1 → v2)
# ---------------------------------------------------------------------------

def test_v1_encrypted_decrypts_with_active_key():
    """v1-encrypted values still decrypt with the current active key."""
    from app.core.token_crypto import _KEY, _SCHEME_V1
    if _KEY is None:
        return  # skip if no key configured

    # Manually produce a v1 value with the active key
    import base64, secrets
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    ct = AESGCM(_KEY).encrypt(iv, b"shpat_test_v1", None)
    v1_stored = f"{_SCHEME_V1}{base64.b64encode(iv + ct).decode()}"

    from app.core.token_crypto import decrypt_token
    result = decrypt_token(v1_stored)
    assert result == "shpat_test_v1"


def test_v2_encrypted_decrypts():
    """v2-encrypted values (current scheme) decrypt correctly."""
    from app.core.token_crypto import encrypt_token, decrypt_token, _SCHEME_V2
    stored = encrypt_token("shpat_test_v2")
    assert stored.startswith(_SCHEME_V2)
    result = decrypt_token(stored)
    assert result == "shpat_test_v2"


def test_re_encrypt_upgrades_v1_to_v2():
    """re_encrypt converts v1 ciphertext to v2."""
    from app.core.token_crypto import _KEY, _SCHEME_V1, _SCHEME_V2, re_encrypt
    if _KEY is None:
        return

    import base64, secrets
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    ct = AESGCM(_KEY).encrypt(iv, b"shpat_rotate_me", None)
    v1_stored = f"{_SCHEME_V1}{base64.b64encode(iv + ct).decode()}"

    rotated = re_encrypt(v1_stored)
    assert rotated is not None
    assert rotated.startswith(_SCHEME_V2)

    from app.core.token_crypto import decrypt_token
    assert decrypt_token(rotated) == "shpat_rotate_me"


def test_decrypt_with_prev_key_fallback():
    """During rotation, v1 values encrypted with the old key still decrypt."""
    from app.core.token_crypto import _SCHEME_V1
    import base64, secrets
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    old_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)
    ct = AESGCM(old_key).encrypt(iv, b"shpat_old_key", None)
    v1_stored = f"{_SCHEME_V1}{base64.b64encode(iv + ct).decode()}"

    # Patch _KEY_PREV to the old key
    import app.core.token_crypto as tc
    original_prev = tc._KEY_PREV
    try:
        tc._KEY_PREV = old_key
        from app.core.token_crypto import decrypt_token
        result = decrypt_token(v1_stored)
        assert result == "shpat_old_key"
    finally:
        tc._KEY_PREV = original_prev


def test_plaintext_passthrough_unchanged():
    """Plaintext values still work during rotation."""
    from app.core.token_crypto import decrypt_token, re_encrypt
    assert decrypt_token("shpat_plain") == "shpat_plain"
    assert re_encrypt("shpat_plain") == "shpat_plain"


# ---------------------------------------------------------------------------
# Operator key rotation
# ---------------------------------------------------------------------------

def test_operator_primary_key_accepted(client):
    """Primary DASHBOARD_API_KEY works."""
    key = os.environ.get("DASHBOARD_API_KEY", "")
    if not key:
        return
    resp = client.get("/ops/alerts", headers={"X-API-Key": key})
    assert resp.status_code == 200


def test_operator_prev_key_accepted(client):
    """DASHBOARD_API_KEY_PREV accepted during rotation."""
    prev_key = "rotation-test-prev-key"
    import app.core.deps as deps
    original = deps._OPERATOR_KEY_PREV
    try:
        deps._OPERATOR_KEY_PREV = prev_key
        resp = client.get("/ops/alerts", headers={"X-API-Key": prev_key})
        assert resp.status_code == 200
    finally:
        deps._OPERATOR_KEY_PREV = original


def test_operator_wrong_key_still_rejected(client):
    """Random key rejected even with prev key configured."""
    import app.core.deps as deps
    original = deps._OPERATOR_KEY_PREV
    try:
        deps._OPERATOR_KEY_PREV = "some-prev-key"
        resp = client.get("/ops/alerts", headers={"X-API-Key": "totally-wrong"})
        assert resp.status_code == 401
    finally:
        deps._OPERATOR_KEY_PREV = original


# ---------------------------------------------------------------------------
# Alert delivery confirmation tracking
# ---------------------------------------------------------------------------

def test_alert_delivery_status_skipped_when_no_url(db):
    """Alert without Slack URL gets delivery_status='skipped'."""
    with patch.dict(os.environ, {"OPS_SLACK_WEBHOOK_URL": ""}, clear=False):
        from app.services.alerting import write_alert
        alert = write_alert(
            db, severity="warning", source="test",
            alert_type="test_delivery", summary="test",
        )
    assert alert.delivery_status == "skipped"
    assert alert.delivered_at is None


def test_alert_delivery_status_sent_on_success(db):
    """Successful delivery gets delivery_status='sent' + delivered_at."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock(status_code=200)
    with patch.dict(os.environ, {"OPS_SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}, clear=False), \
         patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", return_value=mock_resp):
        from app.services.alerting import write_alert
        alert = write_alert(
            db, severity="critical", source="test",
            alert_type="gdpr_failure", summary="test",
        )
    assert alert.delivery_status == "sent"
    assert alert.delivered_at is not None


def test_alert_delivery_status_failed_on_error(db):
    """Failed delivery gets delivery_status='failed'."""
    with patch.dict(os.environ, {"OPS_SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}, clear=False), \
         patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", side_effect=Exception("timeout")):
        from app.services.alerting import write_alert
        alert = write_alert(
            db, severity="critical", source="test",
            alert_type="gdpr_failure", summary="test",
        )
    # Alert still persisted
    assert alert.id is not None
    assert alert.delivery_status == "failed"
