"""Tests for AES-256-GCM token encryption (token_crypto.py)."""
from app.core.token_crypto import encrypt_token, decrypt_token, is_encrypted, _SCHEME_V1, _SCHEME_V2


def test_encrypt_decrypt_roundtrip():
    plaintext = "shpat_abc123_test_token"
    encrypted = encrypt_token(plaintext)
    assert encrypted.startswith(_SCHEME_V2)  # new encryptions use v2
    assert is_encrypted(encrypted)
    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext


def test_is_encrypted_detects_prefix():
    assert is_encrypted("enc:v1:somepayload") is True
    assert is_encrypted("enc:v2:somepayload") is True
    assert is_encrypted("shpat_plaintext_token") is False
    assert is_encrypted("") is False
    assert is_encrypted(None) is False


def test_decrypt_plaintext_passthrough():
    """Legacy plaintext values are returned as-is."""
    result = decrypt_token("shpat_plaintext_legacy")
    assert result == "shpat_plaintext_legacy"


def test_decrypt_empty_returns_none():
    assert decrypt_token("") is None
    assert decrypt_token(None) is None


def test_encrypt_empty_returns_empty():
    assert encrypt_token("") == ""
    assert encrypt_token(None) is None
