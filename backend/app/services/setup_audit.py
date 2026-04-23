"""
setup_audit.py — Activation audit service.

Computes the truthful setup/readiness state of a merchant's WishSpark
installation by inspecting both local DB state and live Shopify API state.

Two audit modes
---------------
fast (synchronous)
    Reads only from the DB.  Uses stored webhook_id / script_tag_id as proxies
    for "registered" — does not call Shopify API.  Suitable for dashboard page
    load, background monitoring, and any path where speed matters.

    Limitation: if a webhook/script_tag was manually deleted from the Shopify
    admin, the fast audit will still report ok until a deep audit is run.

deep (async)
    Calls Shopify's webhooks.json and script_tags.json to verify actual
    registration.  Use this for the repair flow, post-repair confirmation,
    and periodic operator health checks.

    Updates merchant.webhook_id / script_tag_id if the live state differs
    from what is stored (auto-heals stale DB state without touching Shopify).

Readiness states
----------------
degraded        — critical failure: merchant not found, install_status !=
                  "active", or access token fails to decrypt.  Nothing works.

needs_repair    — token ok, but one or both of webhook / script_tag are
                  missing from Shopify.  Repairable via POST /setup/repair/*.

lite_ready      — token + webhook + tracker all confirmed present.  Full Lite
                  plan value is available.  Pro upgrade not yet active.

pro_active      — everything above PLUS billing_active=True and plan="pro".

Public API
----------
    compute_audit_fast(db, shop_domain) -> SetupAudit
    compute_audit_deep(db, shop_domain) -> SetupAudit  (async)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token, is_encrypted
from app.models.merchant import Merchant

log = logging.getLogger(__name__)

_SHOPIFY_API_VERSION = "2024-01"
_DEEP_TIMEOUT        = 10.0  # seconds per Shopify API call


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _app_url() -> str:
    return os.getenv("APP_URL", "").rstrip("/")


def _tracker_url() -> str:
    override = os.getenv("TRACKER_SCRIPT_URL", "").strip()
    base     = _app_url()
    return override if override else (f"{base}/tracker.js" if base else "")


def _orders_webhook_url() -> str:
    """Target URL for the app/uninstalled lifecycle webhook."""
    return f"{_app_url()}/webhooks/shopify/app-uninstalled"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SetupChecks:
    """
    Individual check results.  False means the feature is absent or broken.
    None means the check was skipped (not applicable or audit_mode="fast"
    with no stored ID to reference).
    """
    merchant_exists:        bool          = False
    install_active:         bool          = False
    token_ok:               bool          = False
    token_encrypted:        bool          = False
    # webhook: True = confirmed present on Shopify (deep) or ID stored (fast)
    webhook_ok:             bool          = False
    webhook_id:             Optional[str] = None
    # tracker: same semantics
    tracker_ok:             bool          = False
    tracker_id:             Optional[str] = None
    # billing
    billing_active:         bool          = False
    billing_plan:           str           = "lite"
    billing_charge_pending: bool          = False


@dataclass
class SetupAudit:
    shop_domain:      str
    computed_at:      datetime
    audit_mode:       str          # "fast" | "deep"
    checks:           SetupChecks  = field(default_factory=SetupChecks)
    # setup_complete: token + webhook + tracker all ok (Lite plan is fully usable)
    setup_complete:   bool         = False
    # readiness: single state for dashboard / support use
    readiness:        str          = "degraded"
    degraded_reasons: list[str]    = field(default_factory=list)

    def to_dict(self) -> dict:
        c = self.checks
        return {
            "shop_domain":    self.shop_domain,
            "computed_at":    self.computed_at.isoformat(),
            "audit_mode":     self.audit_mode,
            "setup_complete": self.setup_complete,
            "readiness":      self.readiness,
            "degraded_reasons": self.degraded_reasons,
            "checks": {
                "merchant_exists":        c.merchant_exists,
                "install_active":         c.install_active,
                "token_ok":               c.token_ok,
                "token_encrypted":        c.token_encrypted,
                "webhook_ok":             c.webhook_ok,
                "webhook_id":             c.webhook_id,
                "tracker_ok":             c.tracker_ok,
                "tracker_id":             c.tracker_id,
                "billing_active":         c.billing_active,
                "billing_plan":           c.billing_plan,
                "billing_charge_pending": c.billing_charge_pending,
            },
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _shopify_url(shop: str, path: str) -> str:
    return f"https://{shop}/admin/api/{_SHOPIFY_API_VERSION}/{path}"


def _assess_token(merchant: Merchant) -> tuple[bool, bool]:
    """
    Returns (token_ok, token_encrypted).
    token_ok = True when the stored value can be decrypted to a non-empty string.
    """
    raw = merchant.access_token
    if not raw:
        return False, False
    plaintext = decrypt_token(raw)
    ok        = bool(plaintext)
    encrypted = is_encrypted(raw)
    return ok, encrypted


def _derive_readiness(checks: SetupChecks, degraded_reasons: list[str]) -> tuple[str, bool]:
    """
    Returns (readiness_state, setup_complete).
    """
    if not checks.merchant_exists or not checks.install_active or not checks.token_ok:
        return "degraded", False

    setup_complete = checks.webhook_ok and checks.tracker_ok

    if not setup_complete:
        return "needs_repair", False

    if checks.billing_active and checks.billing_plan == "pro":
        return "pro_active", True

    return "lite_ready", True


def _build_degraded_reasons(checks: SetupChecks) -> list[str]:
    reasons: list[str] = []
    if not checks.merchant_exists:
        reasons.append("merchant_not_found — shop has no installation record")
    if checks.merchant_exists and not checks.install_active:
        reasons.append("install_inactive — app was uninstalled; merchant must reinstall")
    if checks.merchant_exists and checks.install_active and not checks.token_ok:
        if not checks.token_encrypted:
            reasons.append("token_missing — access token not stored; reinstall required")
        else:
            reasons.append(
                "token_decrypt_failed — stored token cannot be decrypted; "
                "check MERCHANT_TOKEN_ENCRYPTION_KEY or reinstall"
            )
    if checks.merchant_exists and checks.install_active and checks.token_ok:
        if not checks.webhook_ok:
            reasons.append(
                "webhook_missing — lifecycle webhook (app/uninstalled) not registered; "
                "use POST /setup/repair/webhook to reconnect"
            )
        if not checks.tracker_ok:
            reasons.append(
                "tracker_missing — spark-tracker.js Script Tag not installed on Shopify; "
                "use POST /setup/repair/tracker to fix"
            )
    return reasons


# ---------------------------------------------------------------------------
# Fast audit (synchronous, DB-only)
# ---------------------------------------------------------------------------

def compute_audit_fast(db: Session, shop_domain: str) -> SetupAudit:
    """
    Compute setup state from DB only.  Does not call Shopify API.

    webhook_ok / tracker_ok are inferred from stored IDs — if the ID is
    present in the DB, we assume the registration is active.  This assumption
    can be wrong if the merchant manually deleted the webhook/script_tag via
    their Shopify admin.  Use compute_audit_deep to verify live state.
    """
    now     = _now_utc()
    checks  = SetupChecks()
    audit   = SetupAudit(shop_domain=shop_domain, computed_at=now, audit_mode="fast", checks=checks)

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()

    if merchant is None:
        checks.merchant_exists = False
        audit.degraded_reasons = _build_degraded_reasons(checks)
        audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)
        return audit

    checks.merchant_exists = True
    checks.install_active  = merchant.install_status == "active"

    if checks.install_active:
        checks.token_ok, checks.token_encrypted = _assess_token(merchant)

    # Webhook: trust the stored ID as a proxy
    checks.webhook_id = merchant.webhook_id
    checks.webhook_ok = bool(merchant.webhook_id)

    # Tracker: trust the stored ID as a proxy
    checks.tracker_id = merchant.script_tag_id
    checks.tracker_ok = bool(merchant.script_tag_id)

    # Billing
    checks.billing_active         = bool(merchant.billing_active)
    checks.billing_plan           = merchant.plan or "lite"
    checks.billing_charge_pending = (
        bool(merchant.billing_charge_id) and not merchant.billing_active
    )

    audit.degraded_reasons = _build_degraded_reasons(checks)
    audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)

    log.debug(
        "setup_audit: fast shop=%s readiness=%s setup_complete=%s",
        shop_domain, audit.readiness, audit.setup_complete,
    )
    return audit


# ---------------------------------------------------------------------------
# Deep audit (async, live Shopify API verification)
# ---------------------------------------------------------------------------

async def _verify_webhook_live(shop: str, token: str) -> tuple[bool, Optional[str]]:
    """
    Returns (ok, webhook_id) by listing app/uninstalled webhooks from Shopify.
    """
    target = _orders_webhook_url()
    if not target:
        log.warning("setup_audit: APP_URL not configured — cannot verify webhook live")
        return False, None

    headers = {"X-Shopify-Access-Token": token}
    try:
        async with httpx.AsyncClient(timeout=_DEEP_TIMEOUT) as client:
            resp = await client.get(
                _shopify_url(shop, "webhooks.json"),
                headers=headers,
                params={"topic": "app/uninstalled", "limit": 50},
            )
        if resp.status_code != 200:
            log.warning(
                "setup_audit: webhook list returned %d shop=%s",
                resp.status_code, shop,
            )
            return False, None
        for wh in resp.json().get("webhooks", []):
            if wh.get("address") == target:
                return True, str(wh["id"])
        return False, None
    except Exception as exc:
        log.error("setup_audit: webhook verify exception shop=%s: %s", shop, exc)
        return False, None


async def _verify_tracker_live(shop: str, token: str) -> tuple[bool, Optional[str]]:
    """
    Returns (ok, script_tag_id) by listing script tags from Shopify.
    """
    target = _tracker_url()
    if not target:
        log.warning("setup_audit: tracker_url is empty — cannot verify tracker live")
        return False, None

    headers = {"X-Shopify-Access-Token": token}
    try:
        async with httpx.AsyncClient(timeout=_DEEP_TIMEOUT) as client:
            resp = await client.get(
                _shopify_url(shop, "script_tags.json"),
                headers=headers,
                params={"limit": 250, "fields": "id,src"},
            )
        if resp.status_code != 200:
            log.warning(
                "setup_audit: script_tag list returned %d shop=%s",
                resp.status_code, shop,
            )
            return False, None
        for st in resp.json().get("script_tags", []):
            if st.get("src") == target:
                return True, str(st["id"])
        return False, None
    except Exception as exc:
        log.error("setup_audit: tracker verify exception shop=%s: %s", shop, exc)
        return False, None


async def compute_audit_deep(db: Session, shop_domain: str) -> SetupAudit:
    """
    Compute setup state with live Shopify API verification.

    Calls Shopify to confirm webhook and script_tag are actually registered.
    If live state differs from DB (e.g. merchant deleted webhook manually),
    updates merchant.webhook_id / script_tag_id in the DB to reflect reality.

    Use this for:
    - Post-repair confirmation
    - Dashboard "refresh / check now" action
    - Periodic operator health monitoring
    """
    now    = _now_utc()
    checks = SetupChecks()
    audit  = SetupAudit(shop_domain=shop_domain, computed_at=now, audit_mode="deep", checks=checks)

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()

    if merchant is None:
        checks.merchant_exists = False
        audit.degraded_reasons = _build_degraded_reasons(checks)
        audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)
        return audit

    checks.merchant_exists = True
    checks.install_active  = merchant.install_status == "active"

    if not checks.install_active:
        audit.degraded_reasons = _build_degraded_reasons(checks)
        audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)
        return audit

    token_ok, token_encrypted = _assess_token(merchant)
    checks.token_ok        = token_ok
    checks.token_encrypted = token_encrypted

    # Billing — always from DB (Shopify billing state is only updated by our callback)
    checks.billing_active         = bool(merchant.billing_active)
    checks.billing_plan           = merchant.plan or "lite"
    checks.billing_charge_pending = (
        bool(merchant.billing_charge_id) and not merchant.billing_active
    )

    if not token_ok:
        # Can't call Shopify API — skip live checks
        audit.degraded_reasons = _build_degraded_reasons(checks)
        audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)
        return audit

    # Decrypt token for API calls
    plaintext_token = decrypt_token(merchant.access_token)  # type: ignore[arg-type]

    # Live webhook check
    webhook_ok, live_webhook_id = await _verify_webhook_live(shop_domain, plaintext_token)  # type: ignore[arg-type]
    checks.webhook_ok = webhook_ok
    checks.webhook_id = live_webhook_id

    # Live tracker check
    tracker_ok, live_tracker_id = await _verify_tracker_live(shop_domain, plaintext_token)  # type: ignore[arg-type]
    checks.tracker_ok = tracker_ok
    checks.tracker_id = live_tracker_id

    # Heal DB if live state differs from stored IDs (read-only heal — no Shopify writes)
    _heal_merchant_ids(db, merchant, live_webhook_id, live_tracker_id)

    audit.degraded_reasons = _build_degraded_reasons(checks)
    audit.readiness, audit.setup_complete = _derive_readiness(checks, audit.degraded_reasons)

    log.info(
        "setup_audit: deep shop=%s readiness=%s webhook=%s tracker=%s billing=%s",
        shop_domain, audit.readiness, webhook_ok, tracker_ok, checks.billing_active,
    )
    return audit


def _heal_merchant_ids(
    db:              Session,
    merchant:        Merchant,
    live_webhook_id: Optional[str],
    live_tracker_id: Optional[str],
) -> None:
    """
    Update stored IDs to match live Shopify state without touching Shopify.

    This corrects the case where a merchant manually deleted a webhook/script_tag,
    making the stored ID stale.  By nulling the stored ID the fast audit will
    correctly report needs_repair instead of falsely reporting ok.
    """
    changed = False

    if live_webhook_id != merchant.webhook_id:
        log.info(
            "setup_audit: healing webhook_id shop=%s stored=%s live=%s",
            merchant.shop_domain, merchant.webhook_id, live_webhook_id,
        )
        merchant.webhook_id = live_webhook_id
        changed = True

    if live_tracker_id != merchant.script_tag_id:
        log.info(
            "setup_audit: healing script_tag_id shop=%s stored=%s live=%s",
            merchant.shop_domain, merchant.script_tag_id, live_tracker_id,
        )
        merchant.script_tag_id = live_tracker_id
        changed = True

    if changed:
        try:
            db.commit()
        except Exception as exc:
            log.error(
                "setup_audit: heal commit failed shop=%s: %s",
                merchant.shop_domain, exc,
            )
            db.rollback()
