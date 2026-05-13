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

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("merchant_privacy")

_OPT_OUT_KEY_PREFIX = "hs:merchant_opt_out"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("merchant_privacy: _redis failed: %s", exc)
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
        record_silent_return("merchant_privacy.opt_out_read")
        return False
    try:
        raw = rc.get(_opt_out_key(shop_domain))
        return bool(raw)
    except Exception as exc:
        # Fail-safe semantics: return False on Redis failure (won't
        # silently kill features on a transient blip). The except path
        # was previously unobserved — surface as a silent_return so a
        # spike in Redis errors is visible to ops instead of hiding
        # behind silent false-negatives on the opt-out check.
        record_silent_return("merchant_privacy.opt_out_read_failed")
        log.warning("merchant_privacy: is_merchant_opted_out failed: %s", exc)
        return False


def set_opt_out(shop_domain: str, opted_out: bool) -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("merchant_privacy.opt_out_write")
        return
    try:
        key = _opt_out_key(shop_domain)
        if opted_out:
            # REDIS-PERSIST-OK: GDPR Art. 21 opt-out is a permanent legal
            # choice — it clears only on explicit re-opt-in, not on TTL.
            rc.set(key, "1")
            # Invalidate per-shop derived caches that could surface
            # data linked to this shop even briefly. Each cache has its
            # own TTL (typically 5-30min) that would expire on its own,
            # but Art. 21 says "without undue delay" — immediate purge
            # closes the latency window.
            _purge_derived_caches_for_shop(shop_domain, rc)
            # Invalidate the cross_shop_aggregator 6h claim so the next
            # aggregator tick recomputes immediately — closes the TOCTOU
            # window where this shop's outcomes could remain in a
            # cross_shop_patterns aggregate until the next 6h cycle.
            # Born 2026-05-11 Senior+++ close (audit finding #1).
            try:
                from app.services.cross_shop_aggregator import NEXT_RUN_KEY
                rc.delete(NEXT_RUN_KEY)
            except Exception as exc:
                log.warning(
                    "merchant_privacy: cross_shop claim invalidation "
                    "failed (non-fatal): %s", exc,
                )
        else:
            rc.delete(key)
    except Exception as exc:
        # A Redis write failure on the opt-out flag is a GDPR Art. 21
        # availability hole — the endpoint would return 200 but the
        # opt-out never persisted. Surfaced via audit_silent_returns
        # so a Redis-flake spike turns visible instead of hiding.
        record_silent_return("merchant_privacy.opt_out_write_failed")
        log.warning("merchant_privacy: set_opt_out failed: %s", exc)


def _purge_derived_caches_for_shop(shop_domain: str, rc) -> None:
    """Invalidate per-shop derived cache keys on Art. 21 opt-out.

    Scope: the set of `<prefix>:{md5(shop)[:16]}` and `<prefix>:{shop}`
    keys that feed Pro endpoints. Best-effort — any single key delete
    failure does not block the others.
    """
    import hashlib as _h
    md5 = _h.md5(shop_domain.encode("utf-8")).hexdigest()[:16]
    # Plan-parametric prefixes iterated over known tier names; keeping
    # the audit_claude_md_redis_keys probe matched against a single
    # `hs:rars:v1` entry instead of per-tier literals.
    targets = [
        # Pro Sprint #2 — Recurring Buyers
        f"hs:recurring_buyers:v1:{md5}",
        # /pro/store-profile cache (mirrors what the merchant sees)
        f"hs:storeprofile:v1:{md5}",
        # Action candidates + intent + opportunities derived state
        f"hs:vint:v1:{md5}",
        f"hs:liveopps:v1:{md5}",
        f"hs:action_candidates:v1:{md5}",
        # Pro Sprint #1 — KPI Goals (exact-shop key)
        f"hs:goals:v1:{shop_domain}",
    ]
    # RARS report cache — both Lite and Pro tier variants.
    # TODO(scale-tier): see gdpr_processor.py::_RARS_TIER_PLANS — when
    # scale-tier rars caching ships, add "scale" here AND update the
    # central tuple. Keep these two sites in sync.
    for plan in ("lite", "pro"):
        targets.append(f"hs:rars:v1:{plan}:{md5}")
    try:
        rc.delete(*targets)
    except Exception as exc:
        # SILENT-EXCEPT-OK: opt-out flag write already succeeded above;
        # cache invalidation is best-effort defense-in-depth. Each
        # cache key has its own TTL (5-30min) that will expire on its
        # own — the bound on stale-data exposure is bounded by the
        # longest cache TTL, not unbounded. log.warning so a recurring
        # Redis incident is observable.
        log.warning(
            "merchant_privacy: derived cache purge failed for %s: %s",
            shop_domain, exc,
        )


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
        except Exception as exc:
            log.warning("merchant_privacy: update_contact_email failed: %s", exc)
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
