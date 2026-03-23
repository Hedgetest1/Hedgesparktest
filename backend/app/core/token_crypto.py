"""
token_crypto.py — AES-256-GCM encryption for Shopify merchant access tokens.

Purpose
-------
Shopify access tokens are admin-level credentials.  A database compromise
without encryption exposes every merchant's store to full admin API access.
This module encrypts all tokens before storage and decrypts on read.

Encryption scheme
-----------------
Algorithm:  AES-256-GCM (authenticated encryption — provides both
            confidentiality and integrity)
Key size:   256 bits (32 bytes)
IV/Nonce:   96 bits (12 bytes), randomly generated per encryption
Tag:        128 bits (16 bytes), appended by AESGCM

Wire format (stored in merchants.access_token):
    enc:v1:<base64url(12-byte-nonce || ciphertext || 16-byte-tag)>

The "enc:v1:" prefix:
  - Allows transparent co-existence with legacy plaintext values
  - Makes encrypted vs plaintext rows visually obvious in DB
  - Enables future key rotation via "enc:v2:" scheme without a hard cut-over

Plaintext compatibility
-----------------------
decrypt_token() checks the prefix:
  - "enc:v1:" → decrypt with key
  - No prefix  → return as-is (legacy plaintext — still works)

Plaintext write behaviour:
  - If MERCHANT_TOKEN_ENCRYPTION_KEY is not set, encrypt_token() returns the
    plaintext and logs a WARNING.  Installation still succeeds but the token
    is stored unencrypted.  This is a degraded-security posture, not a crash.
  - Once the key is set, all new writes (install + reinstall) are encrypted.
  - Existing plaintext rows are transparently readable forever.
  - To migrate existing rows, run scripts/encrypt_existing_tokens.py (not yet
    implemented — see TODO below).

Key configuration
-----------------
MERCHANT_TOKEN_ENCRYPTION_KEY env var — accepted in two formats:
    hex:   64 lowercase hex characters representing 32 bytes
           Generate:  python3 -c "import os; print(os.urandom(32).hex())"
    base64: 44 standard base64 characters representing 32 bytes
           Generate:  python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"

The key is read once at module import.  Restart the process after changing it.

Key rotation
------------
Not yet automated.  Procedure:
  1. Add new key as MERCHANT_TOKEN_ENCRYPTION_KEY_NEW env var
  2. Run rotation script (decrypt with old key, encrypt with new key for all rows)
  3. Swap env var to new key
  4. Restart
  v2 prefix can distinguish v1 vs v2 encrypted rows during rotation window.

Security notes
--------------
- The key is NEVER logged, even at DEBUG level.
- Decrypted tokens are NEVER logged, even at DEBUG level.
- On decryption failure (wrong key, corrupted ciphertext), decrypt_token()
  returns None and logs a WARNING with no token content.
- Callers that receive None must treat the token as unavailable and fail safely.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheme identifiers
# ---------------------------------------------------------------------------

_SCHEME_V1 = "enc:v1:"


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def _load_key() -> Optional[bytes]:
    """
    Load the encryption key from MERCHANT_TOKEN_ENCRYPTION_KEY env var.

    Returns 32 bytes on success, None if the env var is absent or malformed.
    Logs clearly on misconfiguration.
    """
    raw = os.getenv("MERCHANT_TOKEN_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None

    # Try hex (64 chars)
    if len(raw) == 64:
        try:
            key = bytes.fromhex(raw)
            if len(key) == 32:
                return key
        except ValueError:
            pass

    # Try standard base64 (44 chars for 32 bytes)
    try:
        key = base64.b64decode(raw + "==")   # pad for safety
        if len(key) == 32:
            return key
    except Exception:
        pass

    log.error(
        "token_crypto: MERCHANT_TOKEN_ENCRYPTION_KEY is set but has unexpected "
        "format or length.  Expected: 64 hex chars or 44 base64 chars (32 bytes).  "
        "Token encryption is DISABLED until this is corrected."
    )
    return None


# Module-level key — read once at import.
# Changing the env var requires a process restart.
_KEY: Optional[bytes] = _load_key()

if _KEY is None:
    log.warning(
        "token_crypto: MERCHANT_TOKEN_ENCRYPTION_KEY is not set — "
        "merchant access tokens will be stored as PLAINTEXT.  "
        "Set this env var before public distribution or App Store submission.  "
        "Generate a key with:  python3 -c \"import os; print(os.urandom(32).hex())\""
    )
else:
    log.info("token_crypto: encryption key loaded — merchant tokens will be encrypted at rest.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a Shopify access token for storage.

    Returns the encrypted token in "enc:v1:<base64url>" format when the key
    is available.

    When no encryption key is configured, returns the plaintext unchanged
    and logs a WARNING.  Callers should not be concerned with which mode
    is active — the output is always safe to store in merchants.access_token.

    Parameters
    ----------
    plaintext   Raw Shopify access token string.

    Returns
    -------
    str — encrypted "enc:v1:..." value, or original plaintext as fallback.
    """
    if not plaintext:
        return plaintext

    if _KEY is None:
        log.warning(
            "token_crypto: storing access token as PLAINTEXT — "
            "set MERCHANT_TOKEN_ENCRYPTION_KEY to enable encryption."
        )
        return plaintext

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import secrets as _secrets

        iv = _secrets.token_bytes(12)          # 96-bit nonce
        aesgcm = AESGCM(_KEY)
        ciphertext_and_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
        # AESGCM.encrypt returns ciphertext + 16-byte tag concatenated
        payload = base64.b64encode(iv + ciphertext_and_tag).decode("ascii")
        return f"{_SCHEME_V1}{payload}"

    except Exception as exc:
        # Encryption failure is a hard security issue — log it clearly.
        # Fall back to plaintext rather than crashing the install flow.
        log.error(
            "token_crypto: encryption failed — storing token as PLAINTEXT "
            "(this should not happen): %s", exc
        )
        return plaintext


def decrypt_token(stored: str) -> Optional[str]:
    """
    Decrypt a stored access token value.

    Handles both encrypted ("enc:v1:...") and legacy plaintext values
    transparently.

    Parameters
    ----------
    stored  Value read from merchants.access_token.

    Returns
    -------
    str  — plaintext access token on success.
    None — when decryption fails (wrong key, corrupted data).
           Returns the value as-is when stored is plaintext (no prefix).
           Callers receiving None must treat the token as unavailable.
    """
    if not stored:
        return None

    # Legacy plaintext — return directly
    if not stored.startswith(_SCHEME_V1):
        return stored

    if _KEY is None:
        log.error(
            "token_crypto: cannot decrypt stored token — "
            "MERCHANT_TOKEN_ENCRYPTION_KEY is not set.  "
            "Set the key that was used to encrypt these tokens."
        )
        return None

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        payload_b64 = stored[len(_SCHEME_V1):]
        payload = base64.b64decode(payload_b64)

        if len(payload) < 28:   # 12 (iv) + 16 (tag minimum)
            log.error("token_crypto: decryption failed — payload too short")
            return None

        iv             = payload[:12]
        ciphertext_tag = payload[12:]

        aesgcm    = AESGCM(_KEY)
        plaintext = aesgcm.decrypt(iv, ciphertext_tag, None)
        return plaintext.decode("utf-8")

    except Exception as exc:
        # Do NOT include the stored value or key in logs
        log.error(
            "token_crypto: decryption failed — wrong key, corrupted data, or "
            "key rotation in progress: %s", type(exc).__name__
        )
        return None


def is_encrypted(stored: str) -> bool:
    """Return True if the stored value is in encrypted format."""
    return bool(stored and stored.startswith(_SCHEME_V1))
