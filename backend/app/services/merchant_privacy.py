"""
merchant_privacy.py — Merchant-level privacy preference store.

Implements the data-subject rights that sit outside the "export" and
"erasure" surfaces:

  * Art. 16 — right to rectification (update contact_email)
  * Art. 21 — right to object to processing (opt_out_automated_targeting)
  * CCPA §1798.120 — right to opt-out of "sale" (same flag applies)

The opt-out flag is stored in Redis (`hs:merchant_opt_out:{shop}`) so we
avoid a migration. The flag is boolean-only and has no TTL: once a
merchant opts out, they stay opted out until they explicitly opt back
in. Absence of the key = still opted in.

Downstream consumers (scoring, nudge composition, LLM calls) check
`is_merchant_opted_out(shop)` and either skip the merchant entirely
or fall back to a deterministic-only path.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("merchant_privacy")

_OPT_OUT_KEY_PREFIX = "hs:merchant_opt_out"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _opt_out_key(shop_domain: str) -> str:
    return f"{_OPT_OUT_KEY_PREFIX}:{shop_domain}"


def is_merchant_opted_out(shop_domain: str | None) -> bool:
    """Return True when the merchant has requested no automated
    processing. Fail-SAFE semantics: Redis errors are treated as "not
    opted out" because falsely applying an opt-out could silently kill
    legitimate features for every merchant on a transient Redis blip."""
    if not shop_domain:
        return False
    rc = _redis()
    if rc is None:
        return False
    try:
        raw = rc.get(_opt_out_key(shop_domain))
        return bool(raw)
    except Exception:
        return False


def set_opt_out(shop_domain: str, opted_out: bool) -> None:
    rc = _redis()
    if rc is None:
        return
    try:
        key = _opt_out_key(shop_domain)
        if opted_out:
            rc.set(key, "1")
        else:
            rc.delete(key)
    except Exception as exc:
        log.warning("merchant_privacy: set_opt_out failed: %s", exc)


def update_contact_email(
    db: Session, *, shop_domain: str, new_email: str,
) -> dict[str, Any]:
    """Art. 16 right to rectification — merchant updates the contact
    email we store. Returns a report dict; never raises on validation."""
    from app.models.merchant import Merchant

    if not new_email or "@" not in new_email or len(new_email) > 254:
        return {"status": "invalid_email"}

    try:
        merchant = (
            db.query(Merchant)
            .filter(Merchant.shop_domain == shop_domain)
            .first()
        )
    except Exception as exc:
        log.warning("merchant_privacy: lookup failed: %s", exc)
        return {"status": "lookup_failed"}
    if merchant is None:
        return {"status": "not_found"}

    previous = merchant.contact_email
    merchant.contact_email = new_email.strip()
    try:
        db.flush()
    except Exception as exc:
        log.warning("merchant_privacy: rectify flush failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "write_failed"}

    return {
        "status": "updated",
        "previous_email_hash": _hash_email(previous or ""),
        "new_email_hash": _hash_email(new_email),
    }


def _hash_email(email: str) -> str:
    import hashlib
    return hashlib.sha256(email.encode()).hexdigest()[:16]


def get_privacy_preferences(shop_domain: str) -> dict[str, Any]:
    return {
        "shop_domain": shop_domain,
        "opt_out_automated_targeting": is_merchant_opted_out(shop_domain),
    }
