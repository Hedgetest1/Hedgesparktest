"""
TIER_2 — modification requires explicit human approval (CLAUDE.md §10).

Shared FastAPI dependencies for HedgeSpark.

Available dependencies
----------------------
get_db                  — yields a request-scoped SQLAlchemy session (pool-safe)
require_shop            — extracts and validates shop_domain from the request
require_merchant_session — authenticates merchant via httpOnly session cookie
require_pro_session     — session auth + Pro plan enforcement

Auth model (post-hardening)
---------------------------
All dashboard endpoints use require_merchant_session, which:
  1. Reads the hs_session httpOnly cookie
  2. Verifies the JWT signature + expiry
  3. Checks session_version against the merchant row (forced logout support)
  4. Returns shop_domain from the verified token

There is NO API key fallback for browser requests.  The only non-cookie
auth path is ALLOW_INSECURE_DEV for local development (hard-fails in
production-like environments — see main.py startup audit).

Storefront-facing endpoints (/track, /nudges/active, /nudge/event,
/tracker.js, /webhooks/*) use require_shop or no auth — they serve
public storefront traffic and are rate-limited instead.
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

_ALLOW_INSECURE_DEV: bool = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"


def require_shop(
    shop: str | None = Query(default=None, alias="shop"),
    x_shop_domain: str | None = Header(default=None, alias="X-Shop-Domain"),
) -> str:
    """
    Return shop_domain from the ?shop= query param or X-Shop-Domain header.
    Raises 400 if missing or invalid.
    Used by storefront-facing endpoints only.
    """
    domain = shop or x_shop_domain
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Missing shop_domain. Pass ?shop=<domain> or X-Shop-Domain header.",
        )
    if not is_valid_shop_domain(domain):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com address.",
        )
    return domain


def require_merchant_session(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """
    Authenticate the merchant via session cookie.

    Reads the hs_session httpOnly cookie, verifies the JWT, then checks
    the session_version claim against the merchant row.  If the merchant
    has bumped their session_version (e.g. after a forced logout), all
    tokens with the old version are rejected.

    Returns shop_domain on success.  Raises 401 on failure.

    The ?shop= query param is IGNORED for authentication.  Shop identity
    comes exclusively from the signed, httpOnly cookie.
    """
    from app.core.merchant_session import SESSION_COOKIE_NAME, verify_session_token

    # Path 1: session cookie (only production path)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        payload = verify_session_token(session_token)
        if payload:
            shop = payload["shop"]
            token_sv = payload.get("sv", 0)

            # Existence + session_version gate via Redis cache. The
            # validation requires merchant.session_version and the
            # row's existence, both of which are stable on the
            # 30s-cache horizon (uninstall + sv bump invalidate the
            # key explicitly). Cache hit eliminates the per-request
            # DB query that was the auth-path bottleneck under load
            # (1000-merchant test 2026-05-04 surfaced 68% PoolTimeout
            # even with dashboard cache pre-warmed).
            #
            # Born 2026-05-04 (Item 7-bis Stage 2: 10k readiness).
            from app.core.redis_client import _client as _redis_client
            import json
            cache_key = f"hs:auth:msv:v1:{shop}"
            rc = _redis_client()
            cached_validation: dict | None = None
            if rc is not None:
                try:
                    raw = rc.get(cache_key)
                    if raw is not None:
                        cached_validation = json.loads(raw)
                except Exception:
                    pass  # SILENT-EXCEPT-OK: redis cache best-effort; fall through to DB on miss/error

            if cached_validation is None:
                from app.models.merchant import Merchant
                merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
                if merchant is None:
                    log.warning(
                        "deps: session rejected — no merchant row for shop=%s "
                        "(uninstalled, never-installed, or forged JWT)",
                        shop,
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Session invalid. Please reinstall HedgeSpark.",
                    )
                db_sv = int(getattr(merchant, "session_version", None) or 0)
                # Cache extended 2026-05-08 to include plan + billing_active
                # so require_pro_session / require_scale_session can read
                # tier from the same cache instead of issuing a duplicate
                # Merchant query per request. Empirical: dashboard's
                # Promise.allSettled fires 6 Pro endpoints simultaneously;
                # without this, each pays an extra DB hop in the auth chain
                # under PgBouncer pool contention (p95 measured 975ms on
                # /pro/cohorts/monthly with consistent traffic over 4h).
                # Mutation sites that MUST invalidate the cache (delete the
                # key): app/api/billing.py (Pro upgrade), app/api/webhooks.py
                # (uninstall + shop-redact), app/services/billing_sync.py
                # (charge deactivate). 30s TTL bounds stale-window if any
                # site forgets.
                cached_validation = {
                    "exists": True,
                    "sv": db_sv,
                    "plan": merchant.plan or "lite",
                    "billing_active": bool(merchant.billing_active),
                }
                if rc is not None:
                    try:
                        rc.setex(cache_key, 30, json.dumps(cached_validation))
                    except Exception:
                        pass  # SILENT-EXCEPT-OK: redis write best-effort; next request will repopulate

            db_sv = int(cached_validation["sv"])
            if token_sv < db_sv:
                log.warning(
                    "deps: session rejected — token sv=%d < merchant sv=%d for shop=%s",
                    token_sv, db_sv, shop,
                )
                raise HTTPException(
                    status_code=401,
                    detail="Session expired. Please log in again.",
                )
            return shop
        # Cookie exists but is invalid/expired

    # Path 2: insecure dev bypass (ONLY in dev, hard-killed in production by main.py)
    if _ALLOW_INSECURE_DEV:
        shop_param = request.query_params.get("shop")
        if shop_param and is_valid_shop_domain(shop_param):
            return shop_param

    raise HTTPException(status_code=401, detail="Authentication required.")


def _read_tier_from_auth_cache(shop_domain: str) -> tuple[str, bool]:
    """Read (plan, billing_active) from the auth cache populated by
    require_merchant_session. Returns ("lite", False) on cache miss /
    Redis down / corrupt cache / old format ({exists,sv}-only) so the
    caller falls back to a DB query.

    Born 2026-05-08 — tier-cache extension for require_pro/scale_session.
    """
    from app.core.redis_client import _client as _redis_client
    from app.core.silent_fallback import record_silent_return
    import json
    rc = _redis_client()
    if rc is None:
        record_silent_return("deps.tier_cache.no_client")
        return "lite", False
    try:
        raw = rc.get(f"hs:auth:msv:v1:{shop_domain}")
        if raw is None:
            return "lite", False
        cached = json.loads(raw)
        return cached.get("plan") or "lite", bool(cached.get("billing_active"))
    except Exception:
        record_silent_return("deps.tier_cache.exception")
        return "lite", False


def require_pro_session(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """
    Authenticate merchant session AND enforce Pro plan (or higher).

    Combines require_merchant_session + Pro/Scale plan check. Scale
    is a superset of Pro — Scale merchants pass the Pro gate too,
    matching the tier rank (lite < pro < scale).

    Returns shop_domain on success. Raises 401 or 403 on failure.

    Tier read is served from the auth cache (populated by
    require_merchant_session) when warm — eliminates the duplicate
    Merchant query that was the dashboard-burst bottleneck under
    PgBouncer pool contention. Defensive DB fallback covers cache
    miss / old format / Redis down — auth is NEVER bypassed; the
    cache only fast-paths the positive case.
    """
    shop = require_merchant_session(request, db)

    plan, billing_active = _read_tier_from_auth_cache(shop)
    if plan in ("pro", "scale") and billing_active:
        return shop

    # Cache miss / stale / Redis down → defensive DB fallback.
    # The DB fallback is the same query the pre-fix code did
    # unconditionally; with the cache, it runs only on the
    # negative / cold path, which is rare for Pro merchants
    # actively browsing the dashboard.
    from app.models.merchant import Merchant
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if row is None or row.plan not in ("pro", "scale") or not row.billing_active:
        raise HTTPException(status_code=403, detail="Pro plan required.")

    return shop


def require_scale_session(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """
    Authenticate merchant session AND enforce Scale plan.

    Sibling of require_pro_session, gating Scale-tier-only features
    (the Northbeam-class moats spostati 2026-04-29 per founder
    directive: features whose closest-competitor lives at $130+ band
    move to Scale €239 — Causal Lift, MTA Compare, Anomaly Fusion +
    Replay, Counterfactual Explorer, Competitor Playbook, Revenue
    Autopsy + Genome, Nudge DNA, Lift Report, Night Shift Agent).

    Returns shop_domain on success. Raises 401 or 403 on failure.

    Same tier-cache fast-path as require_pro_session — defensive DB
    fallback on miss / stale / Redis down.
    """
    shop = require_merchant_session(request, db)

    plan, billing_active = _read_tier_from_auth_cache(shop)
    if plan == "scale" and billing_active:
        return shop

    from app.models.merchant import Merchant
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if row is None or row.plan != "scale" or not row.billing_active:
        raise HTTPException(status_code=403, detail="Scale plan required.")

    return shop


# ---------------------------------------------------------------------------
# Operator access — internal API key auth for admin/ops endpoints
# ---------------------------------------------------------------------------

_OPERATOR_KEY: str = os.getenv("DASHBOARD_API_KEY", "").strip()
_OPERATOR_KEY_PREV: str = os.getenv("DASHBOARD_API_KEY_PREV", "").strip()


def require_operator(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """
    Authenticate operator access via X-API-Key header.

    Accepts DASHBOARD_API_KEY (primary) or DASHBOARD_API_KEY_PREV (rotation
    window). During key rotation, set the new key as primary and the old key
    as _PREV. After all clients are updated, remove _PREV.

    Returns True on success.  Raises 401 on failure.
    """
    if not _OPERATOR_KEY:
        raise HTTPException(status_code=503, detail="Operator access not configured.")
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid operator key.")
    # Timing-safe comparison (2026-04-11 security audit): `==` on strings
    # short-circuits at the first differing byte, leaking key length and
    # character positions to a timing attacker. `hmac.compare_digest` is
    # constant-time for equal-length inputs.
    import hmac as _hmac
    if _hmac.compare_digest(x_api_key, _OPERATOR_KEY):
        return True
    if _OPERATOR_KEY_PREV and _hmac.compare_digest(x_api_key, _OPERATOR_KEY_PREV):
        return True
    raise HTTPException(status_code=401, detail="Invalid operator key.")
