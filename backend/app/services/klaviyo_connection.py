"""
klaviyo_connection.py — Single source of truth for merchant Klaviyo integration.

Responsibilities:
    - Save / read / delete merchant Klaviyo private key (encrypted at rest)
    - Verify key against Klaviyo API (lightweight, non-destructive)
    - Update connection state on the merchant row
    - Resolve merchant key for sync operations

All Klaviyo key access MUST go through this module.  No other code should
read encrypted_klaviyo_key directly or call token_crypto for Klaviyo keys.

Connection status values (AI-inspectable):
    not_connected  — no key saved
    connected      — key saved, last verification passed
    unverified     — key saved but not yet verified
    invalid_key    — last verification returned auth error
    error          — last verification failed (network/timeout/other)

Security invariants:
    - Key is NEVER returned in plaintext to any API response
    - Key is NEVER logged, even at DEBUG level
    - Decrypted key exists only in local variables, never stored in dicts/caches
    - On disconnect, key is nullified and status reset
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token, encrypt_token, is_encrypted
from app.models.merchant import Merchant

log = logging.getLogger(__name__)

# Klaviyo API endpoint for lightweight key validation
_KLAVIYO_ACCOUNTS_URL = "https://a.klaviyo.com/api/accounts/"
_VERIFY_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Valid connection status values
# ---------------------------------------------------------------------------
STATUS_NOT_CONNECTED = "not_connected"
STATUS_CONNECTED = "connected"
STATUS_UNVERIFIED = "unverified"
STATUS_INVALID_KEY = "invalid_key"
STATUS_ERROR = "error"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sanitize_error(msg: str) -> str:
    """Truncate and strip any potential secret content from error messages."""
    safe = msg[:250] if msg else ""
    # Strip anything that looks like an API key fragment
    for prefix in ("Klaviyo-API-Key ", "pk_", "sk_"):
        if prefix in safe:
            safe = safe.split(prefix)[0] + f"{prefix}[REDACTED]"
    return safe


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def save_klaviyo_key(
    db: Session,
    shop_domain: str,
    plaintext_key: str,
) -> dict:
    """
    Encrypt and save a Klaviyo private key for a merchant.

    Returns a status dict (never includes the key).
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant is None:
        return {"ok": False, "error": "Merchant not found"}

    if not plaintext_key or len(plaintext_key.strip()) < 8:
        return {"ok": False, "error": "Invalid key format"}

    encrypted = encrypt_token(plaintext_key.strip())

    # HARD GATE: Klaviyo keys must NEVER be stored as plaintext.
    # encrypt_token() falls back to plaintext when MERCHANT_TOKEN_ENCRYPTION_KEY
    # is missing (legacy compatibility for Shopify access tokens). That fallback
    # is not acceptable here — reject the save outright.
    if not is_encrypted(encrypted):
        log.error(
            "klaviyo_connection: refusing to store Klaviyo key as plaintext — "
            "MERCHANT_TOKEN_ENCRYPTION_KEY is not configured"
        )
        return {
            "ok": False,
            "error": "Encryption not available — contact support",
        }

    merchant.encrypted_klaviyo_key = encrypted
    merchant.klaviyo_connection_status = STATUS_UNVERIFIED
    merchant.klaviyo_last_error = None
    db.flush()

    log.info("klaviyo_connection: key saved shop=%s status=unverified", shop_domain)
    return {"ok": True, "status": STATUS_UNVERIFIED}


def disconnect_klaviyo(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    Remove Klaviyo key and reset connection state.
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant is None:
        return {"ok": False, "error": "Merchant not found"}

    merchant.encrypted_klaviyo_key = None
    merchant.klaviyo_connection_status = STATUS_NOT_CONNECTED
    merchant.klaviyo_last_verified_at = None
    merchant.klaviyo_last_error = None
    db.flush()

    log.info("klaviyo_connection: disconnected shop=%s", shop_domain)
    return {"ok": True, "status": STATUS_NOT_CONNECTED}


def get_connection_status(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    Return the current Klaviyo connection state for a merchant.

    Never includes the raw key — only masked hint and status fields.
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant is None:
        return {"status": STATUS_NOT_CONNECTED, "has_key": False}

    has_key = bool(merchant.encrypted_klaviyo_key)
    return {
        "status":              merchant.klaviyo_connection_status or STATUS_NOT_CONNECTED,
        "has_key":             has_key,
        "key_hint":            _mask_key(merchant.encrypted_klaviyo_key) if has_key else None,
        "last_verified_at":    _iso(merchant.klaviyo_last_verified_at),
        "last_error":          merchant.klaviyo_last_error,
        "last_sync_at":        _iso(merchant.klaviyo_last_sync_at),
        "last_sync_error":     merchant.klaviyo_last_sync_error,
    }


def _mask_key(encrypted_value: str | None) -> str | None:
    """Decrypt just enough to show last 4 chars as a hint. Returns 'pk_****abcd' style."""
    if not encrypted_value:
        return None
    try:
        plaintext = decrypt_token(encrypted_value)
        if plaintext and len(plaintext) >= 4:
            return f"****{plaintext[-4:]}"
        return "****"
    except Exception:
        return "****"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ---------------------------------------------------------------------------
# Key resolution (for sync operations)
# ---------------------------------------------------------------------------

def resolve_klaviyo_key(
    db: Session,
    shop_domain: str,
) -> Optional[str]:
    """
    Decrypt and return the merchant's Klaviyo private key.

    Returns None if:
        - No merchant found
        - No key saved
        - Decryption fails

    The caller MUST NOT log, cache, or include the return value in any
    API response.  Use it for a single Klaviyo API call, then discard.
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant is None or not merchant.encrypted_klaviyo_key:
        return None

    plaintext = decrypt_token(merchant.encrypted_klaviyo_key)
    if plaintext is None:
        log.error(
            "klaviyo_connection: key decryption failed shop=%s — "
            "encryption key may have changed",
            shop_domain,
        )
    return plaintext


# ---------------------------------------------------------------------------
# Connection verification
# ---------------------------------------------------------------------------

def verify_klaviyo_connection(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    Test the stored Klaviyo key against the Klaviyo Accounts API.

    This is a lightweight, read-only, non-destructive check.
    Updates connection status on the merchant row.

    Returns:
        {
            "status": "connected" | "invalid_key" | "error" | "not_connected",
            "detail": str,
        }
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant is None:
        return {"status": STATUS_NOT_CONNECTED, "detail": "Merchant not found"}

    if not merchant.encrypted_klaviyo_key:
        return {"status": STATUS_NOT_CONNECTED, "detail": "No Klaviyo key saved"}

    plaintext_key = decrypt_token(merchant.encrypted_klaviyo_key)
    if plaintext_key is None:
        merchant.klaviyo_connection_status = STATUS_ERROR
        merchant.klaviyo_last_error = "Key decryption failed — encryption key may have changed"
        db.flush()
        return {"status": STATUS_ERROR, "detail": "Key decryption failed"}

    # Call Klaviyo Accounts API — lightweight read-only endpoint
    # session-rollback: ok — sole caller is API endpoint /pro/integrations/klaviyo/verify (integrations.py:125) via Depends(get_db) = request-scoped FastAPI session. Cross-request poison impossible (get_db generator handles rollback at request exit). No worker callsite.
    try:
        resp = httpx.get(
            _KLAVIYO_ACCOUNTS_URL,
            headers={
                "Authorization": f"Klaviyo-API-Key {plaintext_key}",
                "revision": "2024-02-15",
            },
            timeout=_VERIFY_TIMEOUT,
        )

        if resp.status_code == 200:
            merchant.klaviyo_connection_status = STATUS_CONNECTED
            merchant.klaviyo_last_verified_at = _now()
            merchant.klaviyo_last_error = None
            db.flush()
            log.info("klaviyo_connection: verified shop=%s status=connected", shop_domain)
            return {"status": STATUS_CONNECTED, "detail": "Connected successfully"}

        if resp.status_code in (401, 403):
            merchant.klaviyo_connection_status = STATUS_INVALID_KEY
            merchant.klaviyo_last_error = f"Klaviyo returned {resp.status_code} — key may be revoked or invalid"
            db.flush()
            log.warning("klaviyo_connection: invalid key shop=%s http=%d", shop_domain, resp.status_code)
            return {"status": STATUS_INVALID_KEY, "detail": "Invalid or revoked API key"}

        if resp.status_code == 429:
            # Rate limited — don't change connection status, just report
            detail = "Klaviyo rate limit — try again in a few seconds"
            merchant.klaviyo_last_error = detail
            db.flush()
            return {"status": merchant.klaviyo_connection_status, "detail": detail}

        # Other HTTP errors
        detail = f"Klaviyo returned HTTP {resp.status_code}"
        merchant.klaviyo_connection_status = STATUS_ERROR
        merchant.klaviyo_last_error = _sanitize_error(detail)
        db.flush()
        return {"status": STATUS_ERROR, "detail": detail}

    except httpx.TimeoutException:
        detail = "Klaviyo API timed out — try again"
        merchant.klaviyo_connection_status = STATUS_ERROR
        merchant.klaviyo_last_error = detail
        db.flush()
        return {"status": STATUS_ERROR, "detail": detail}

    except Exception as exc:
        detail = _sanitize_error(f"Network error: {type(exc).__name__}")
        merchant.klaviyo_connection_status = STATUS_ERROR
        merchant.klaviyo_last_error = detail
        db.flush()
        log.error("klaviyo_connection: verify failed shop=%s: %s", shop_domain, type(exc).__name__)
        return {"status": STATUS_ERROR, "detail": detail}


# ---------------------------------------------------------------------------
# Sync state tracking (called by sync operations, not by merchants)
# ---------------------------------------------------------------------------

def record_sync_success(db: Session, shop_domain: str) -> None:
    """Record a successful Klaviyo sync attempt."""
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant:
        merchant.klaviyo_last_sync_at = _now()
        merchant.klaviyo_last_sync_error = None
        db.flush()


def record_sync_failure(db: Session, shop_domain: str, error: str) -> None:
    """Record a failed Klaviyo sync attempt."""
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if merchant:
        merchant.klaviyo_last_sync_at = _now()
        merchant.klaviyo_last_sync_error = _sanitize_error(error)
        db.flush()
