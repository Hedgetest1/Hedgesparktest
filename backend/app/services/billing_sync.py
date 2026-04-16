"""
billing_sync.py — Verify Pro merchant billing state with Shopify.

Queries Shopify's RecurringApplicationCharge API for each Pro merchant.
If the charge is cancelled/declined/expired, flips billing_active=False.

Safety:
    - Rate-limited: max 10 merchants per cycle (Shopify API courtesy)
    - Audit-logged: every state change is recorded
    - No mass deactivation: if >3 deactivations in a cycle, stop and alert
    - Weekly cadence (Sunday only, enforced by caller)

Called by: agent_worker.py phase 7i
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("billing_sync")

_MAX_PER_CYCLE = 10
_MAX_DEACTIVATIONS_BEFORE_HALT = 3


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_billing_sync(db: Session) -> dict:
    """
    Check Pro merchants' billing status against Shopify.

    Returns {"checked": int, "deactivated": int, "ok": int, "errors": list}
    """
    summary = {"checked": 0, "deactivated": 0, "ok": 0, "errors": []}

    # Find Pro merchants with billing_charge_id set
    merchants = (
        db.query(Merchant)
        .filter(
            Merchant.plan == "pro",
            Merchant.billing_active == True,
            Merchant.billing_charge_id.isnot(None),
            Merchant.install_status == "active",
        )
        .limit(_MAX_PER_CYCLE)
        .all()
    )

    if not merchants:
        return summary

    for m in merchants:
        try:
            status = _check_charge_status(m)
            summary["checked"] += 1

            if status in ("cancelled", "declined", "expired", "frozen"):
                _deactivate(db, m, status)
                summary["deactivated"] += 1

                if summary["deactivated"] >= _MAX_DEACTIVATIONS_BEFORE_HALT:
                    log.warning(
                        "billing_sync: HALTED — %d deactivations in one cycle (safety limit)",
                        summary["deactivated"],
                    )
                    _alert_mass_deactivation(summary["deactivated"])
                    break
            else:
                summary["ok"] += 1

        except Exception as exc:
            log.warning("billing_sync: error checking %s: %s", m.shop_domain, exc)
            summary["errors"].append(f"{m.shop_domain}: {exc}")

    return summary


def _check_charge_status(merchant: Merchant) -> str:
    """
    Query Shopify for the charge status.

    Returns: "active" | "cancelled" | "declined" | "expired" | "frozen" | "pending" | "unknown"
    """
    from app.core.token_crypto import decrypt_token

    token = decrypt_token(merchant.access_token)
    if not token:
        return "unknown"

    try:
        import httpx
        url = (
            f"https://{merchant.shop_domain}/admin/api/2024-10"
            f"/recurring_application_charges/{merchant.billing_charge_id}.json"
        )
        resp = httpx.get(
            url,
            headers={"X-Shopify-Access-Token": token},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return "cancelled"
        if resp.status_code != 200:
            log.warning("billing_sync: Shopify returned %d for %s", resp.status_code, merchant.shop_domain)
            return "unknown"

        data = resp.json()
        charge = data.get("recurring_application_charge", {})
        return charge.get("status", "unknown")

    except Exception as exc:
        log.warning("billing_sync: API call failed for %s: %s", merchant.shop_domain, exc)
        return "unknown"


def _deactivate(db: Session, merchant: Merchant, reason: str) -> None:
    """Deactivate a merchant's billing and log it."""
    merchant.billing_active = False
    db.flush()

    # Audit log
    from app.services.audit import write_audit_log
    write_audit_log(
        db,
        actor_type="system",
        actor_name="billing_sync",
        action_type="billing_deactivated",
        target_type="merchant",
        target_id=merchant.shop_domain,
        shop_domain=merchant.shop_domain,
        after_state=f'{{"reason": "{reason}", "charge_id": "{merchant.billing_charge_id}"}}',
    )

    log.warning(
        "billing_sync: deactivated billing for %s (charge status=%s)",
        merchant.shop_domain, reason,
    )


def _alert_mass_deactivation(count: int) -> None:
    """Alert operator when too many deactivations happen at once."""
    try:
        from app.services.telegram_agent import send_message, is_configured
        if is_configured():
            send_message(
                f"*BILLING SYNC ALERT*\n\n"
                f"Halted after {count} deactivations in one cycle.\n"
                f"Possible Shopify billing API issue. Manual review required."
            )
    except Exception as exc:
        log.warning("billing_sync: _alert_mass_deactivation failed: %s", exc)
