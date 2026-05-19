"""
webhook_health.py — Webhook drift detection and health monitoring.

Compares expected webhook registrations against actual Shopify API state
for a merchant. Reports missing/stale webhooks and optionally repairs them.

Expected webhooks (current):
  - app/uninstalled → {APP_URL}/webhooks/shopify/app-uninstalled

This is the operational safety layer — it detects when webhooks silently
disappear (Shopify can drop them on API version migration, app update,
or intermittent failures).

Public interface:
    check_webhook_health(db, shop_domain) -> WebhookHealthReport
    repair_missing_webhooks(db, shop_domain) -> WebhookRepairResult
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant

log = logging.getLogger(__name__)

_APP_URL = os.getenv("APP_URL", "")
_TIMEOUT = 8.0

# Expected webhook registrations — topic → relative target path
EXPECTED_WEBHOOKS: dict[str, str] = {
    "app/uninstalled": "/webhooks/shopify/app-uninstalled",
}


@dataclass
class WebhookStatus:
    topic: str
    expected_url: str
    registered: bool = False
    registered_url: str | None = None
    webhook_id: str | None = None
    stale: bool = False  # registered but wrong URL


@dataclass
class WebhookHealthReport:
    shop_domain: str
    healthy: bool = True
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    details: list[WebhookStatus] = field(default_factory=list)
    error: str | None = None


@dataclass
class WebhookRepairResult:
    shop_domain: str
    repaired: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    already_ok: list[str] = field(default_factory=list)
    error: str | None = None


def check_webhook_health(db: Session, shop_domain: str) -> WebhookHealthReport:
    """
    Check whether all expected webhooks are registered for a merchant.

    Does NOT modify anything — read-only detection.
    Returns a structured report indicating missing/stale/ok webhooks.
    """
    report = WebhookHealthReport(shop_domain=shop_domain)

    # Defense-in-depth: skip blocklisted shops even if caller forgot
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    if shop_domain in _ONBOARDING_BLOCKLIST:
        report.healthy = True
        return report

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant or not merchant.access_token:
        report.healthy = False
        report.error = "merchant_not_found_or_no_token"
        return report

    token = decrypt_token(merchant.access_token)
    if not token:
        report.healthy = False
        report.error = "token_decryption_failed"
        return report

    if not _APP_URL:
        report.healthy = False
        report.error = "APP_URL_not_configured"
        return report

    # Fetch all webhooks from Shopify in one call
    try:
        registered = _list_all_webhooks(shop_domain, token)
    except Exception as exc:
        report.healthy = False
        report.error = f"shopify_api_error: {type(exc).__name__}"
        log.error("webhook_health: API error shop=%s: %s", shop_domain, exc)
        return report

    # Compare expected vs actual
    for topic, relative_path in EXPECTED_WEBHOOKS.items():
        expected_url = f"{_APP_URL}{relative_path}"
        status = WebhookStatus(topic=topic, expected_url=expected_url)
        report.checked += 1

        matching = [w for w in registered if w.get("topic") == topic]
        if not matching:
            status.registered = False
            report.missing.append(topic)
            report.healthy = False
        else:
            wh = matching[0]
            status.webhook_id = str(wh.get("id", ""))
            status.registered_url = wh.get("address", "")
            if status.registered_url == expected_url:
                status.registered = True
                report.ok.append(topic)
            else:
                status.stale = True
                status.registered = True
                report.stale.append(topic)
                report.healthy = False

        report.details.append(status)

    return report


def repair_missing_webhooks(db: Session, shop_domain: str) -> WebhookRepairResult:
    """
    Re-register any missing or stale webhooks for a merchant.

    Uses the existing _ensure_webhook from shopify_admin.py which is
    idempotent — it deletes stale URLs and creates correct ones.

    Only touches webhooks that are in EXPECTED_WEBHOOKS.
    Does NOT delete unrecognized webhooks.

    Skips blocklisted shops (legacy/dev placeholders with no real credentials).
    """
    # Skip blocklisted shops — they have no valid Shopify credentials
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    if shop_domain in _ONBOARDING_BLOCKLIST:
        log.info("webhook_health: skipping blocklisted shop=%s", shop_domain)
        return WebhookRepairResult(shop_domain=shop_domain, already_ok=["blocklisted"])
    import asyncio
    from app.services.shopify_admin import _ensure_webhook

    result = WebhookRepairResult(shop_domain=shop_domain)

    # First check health to know what needs repair
    health = check_webhook_health(db, shop_domain)
    if health.error:
        result.error = health.error
        return result

    if health.healthy:
        result.already_ok = health.ok
        return result

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    token = decrypt_token(merchant.access_token)

    needs_repair = set(health.missing) | set(health.stale)

    for topic in needs_repair:
        relative_path = EXPECTED_WEBHOOKS.get(topic)
        if not relative_path:
            continue
        target_url = f"{_APP_URL}{relative_path}"
        try:
            # SAVEPOINT-per-topic (write_no_rollback class — born
            # 2026-05-19f; the §21 multidim sweep a7de1ee12382855f5
            # found this LIVE-latent instance the earlier per-site
            # sweep MISSED because it grepped the locked-site list, not
            # the class). BATCH loop: merchant.webhook_id is flushed
            # per topic, the CALLER commits (orchestrator /
            # aggregation_worker reuse this shared db). A failed flush
            # must roll back ONLY this topic, not poison the session
            # for the remaining topics + the caller. _ensure_webhook is
            # an async Shopify HTTP call (no inner commit →
            # savepoint-legal, trace-the-helper verified).
            from app.core.database import savepoint_scope
            with savepoint_scope(db):
                wh_id, created = asyncio.get_event_loop().run_until_complete(
                    _ensure_webhook(shop_domain, token, topic, target_url)
                )
                if wh_id:
                    result.repaired.append(topic)
                    # Update merchant record if it's the uninstall webhook
                    if topic == "app/uninstalled" and created:
                        merchant.webhook_id = wh_id
                        db.flush()
                    log.info("webhook_health: repaired shop=%s topic=%s id=%s", shop_domain, topic, wh_id)
                else:
                    result.failed.append(topic)
        except Exception as exc:
            result.failed.append(topic)
            log.error("webhook_health: repair failed shop=%s topic=%s: %s", shop_domain, topic, exc)

    for topic in health.ok:
        result.already_ok.append(topic)

    return result


def _list_all_webhooks(shop_domain: str, token: str) -> list[dict]:
    """Fetch all webhook subscriptions from Shopify Admin API."""
    from app.services.shopify_admin import _shopify_url
    headers = {"X-Shopify-Access-Token": token}
    resp = httpx.get(
        _shopify_url(shop_domain, "webhooks.json"),
        headers=headers,
        params={"limit": 250},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("webhooks", [])
