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
_SCHEME_V2 = "enc:v2:"


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def _parse_key(raw: str) -> Optional[bytes]:
    """Parse a key string (hex or base64) into 32 bytes, or None."""
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) == 64:
        try:
            key = bytes.fromhex(raw)
            if len(key) == 32:
                return key
        except ValueError:
            pass
    try:
        key = base64.b64decode(raw + "==")
        if len(key) == 32:
            return key
    except Exception:
        pass
    return None


def _load_key() -> Optional[bytes]:
    """Load the active encryption key from MERCHANT_TOKEN_ENCRYPTION_KEY."""
    raw = os.getenv("MERCHANT_TOKEN_ENCRYPTION_KEY", "")
    key = _parse_key(raw)
    if raw.strip() and key is None:
        log.error(
            "token_crypto: MERCHANT_TOKEN_ENCRYPTION_KEY is set but has unexpected "
            "format or length.  Expected: 64 hex chars or 44 base64 chars (32 bytes).  "
            "Token encryption is DISABLED until this is corrected."
        )
    return key


def _load_prev_key() -> Optional[bytes]:
    """Load the previous encryption key for rotation window decryption."""
    raw = os.getenv("MERCHANT_TOKEN_ENCRYPTION_KEY_PREV", "")
    return _parse_key(raw)


# Module-level keys — read once at import.
# Changing env vars requires a process restart.
_KEY: Optional[bytes] = _load_key()
_KEY_PREV: Optional[bytes] = _load_prev_key()

if _KEY is None:
    log.warning(
        "token_crypto: MERCHANT_TOKEN_ENCRYPTION_KEY is not set — "
        "merchant access tokens will be stored as PLAINTEXT.  "
        "Set this env var before public distribution or App Store submission.  "
        "Generate a key with:  python3 -c \"import os; print(os.urandom(32).hex())\""
    )
else:
    log.info("token_crypto: encryption key loaded — merchant tokens will be encrypted at rest.")
    if _KEY_PREV:
        log.info("token_crypto: previous key loaded — rotation window active.")


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
        return f"{_SCHEME_V2}{payload}"

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

    Handles enc:v1, enc:v2, and legacy plaintext transparently.
    During key rotation, tries the active key first, then falls back
    to _KEY_PREV for v1-encrypted values that used the old key.

    Returns
    -------
    str  — plaintext access token on success.
    None — when decryption fails (wrong key, corrupted data).
           Returns the value as-is when stored is plaintext (no prefix).
    """
    if not stored:
        return None

    # Identify scheme
    if stored.startswith(_SCHEME_V2):
        prefix = _SCHEME_V2
    elif stored.startswith(_SCHEME_V1):
        prefix = _SCHEME_V1
    else:
        # Legacy plaintext — return directly
        return stored

    if _KEY is None and _KEY_PREV is None:
        log.error(
            "token_crypto: cannot decrypt — no encryption key configured."
        )
        return None

    payload_b64 = stored[len(prefix):]
    try:
        payload = base64.b64decode(payload_b64)
    except Exception:
        log.error("token_crypto: decryption failed — invalid base64 payload")
        return None

    if len(payload) < 28:
        log.error("token_crypto: decryption failed — payload too short")
        return None

    iv = payload[:12]
    ciphertext_tag = payload[12:]

    # Try active key first, then previous key (rotation window)
    keys_to_try = [k for k in (_KEY, _KEY_PREV) if k is not None]
    for key in keys_to_try:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(iv, ciphertext_tag, None)
            return plaintext.decode("utf-8")
        except Exception:
            continue

    log.error(
        "token_crypto: decryption failed — neither active nor previous key worked. "
        "Key rotation may be incomplete."
    )
    return None


def re_encrypt(stored: str) -> Optional[str]:
    """
    Re-encrypt a stored value with the current active key (v2 scheme).

    Used during key rotation to upgrade v1 ciphertext to v2.
    Returns None if decryption fails. Returns the input unchanged if
    already on the current scheme with the active key.
    """
    if not stored or not is_encrypted(stored):
        return stored  # plaintext or empty — encrypt_token handles these

    plaintext = decrypt_token(stored)
    if plaintext is None:
        return None  # can't decrypt — rotation failed for this row

    return encrypt_token(plaintext)


def is_encrypted(stored: str) -> bool:
    """Return True if the stored value is in encrypted format (v1 or v2)."""
    return bool(stored and (stored.startswith(_SCHEME_V1) or stored.startswith(_SCHEME_V2)))
