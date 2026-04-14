"""
uninstall_erasure.py — Belt-and-braces erasure watchdog for uninstalled
shops whose Shopify `shop/redact` webhook never arrived.

Shopify's GDPR API sends `shop/redact` 48 hours after an uninstall to
give merchants a reinstall grace window. Most of the time it fires
reliably. When it doesn't (network failure, webhook deregistration,
Shopify outage), HedgeSpark would silently retain the merchant's data
indefinitely — exactly the "quiet indefinite retention" that regulators
care about most.

This watchdog closes the gap:

  1. Every hour, scan `merchants` for rows where
     `install_status='uninstalled'` AND `uninstalled_at < now - 48h`.
  2. For each hit, check if a `shop_redact` GdprRequest has already been
     created in the last 14 days (either by Shopify's webhook OR by a
     prior watchdog run).
  3. If not, create one ourselves — `gdpr_worker` picks it up on the
     next cycle and runs the normal erasure path.
  4. Emit an `uninstall_erasure_self_healed` ops_alert so the operator
     can see that Shopify dropped a webhook.

The 48h grace window is preserved: we only act AFTER Shopify's own
deadline has elapsed. A merchant who reinstalls within 48h triggers
the OAuth callback which sets `install_status='active'` again and
drops out of the watchdog's scan criteria.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("uninstall_erasure")

_GRACE_PERIOD_HOURS = 48
_DEDUP_LOOKBACK_DAYS = 14
_BATCH_CAP = 50  # safety cap per run


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _has_recent_redact_request(db: Session, shop_domain: str) -> bool:
    """Return True when a shop_redact GdprRequest already exists for this
    shop within the dedup window — regardless of status. Avoids creating
    duplicate requests."""
    from app.models.gdpr_request import GdprRequest

    cutoff = _now() - timedelta(days=_DEDUP_LOOKBACK_DAYS)
    try:
        row = (
            db.query(GdprRequest.id)
            .filter(
                GdprRequest.shop_domain == shop_domain,
                GdprRequest.request_type == "shop_redact",
                GdprRequest.created_at >= cutoff,
            )
            .first()
        )
        return row is not None
    except Exception as exc:
        log.warning("uninstall_erasure: dedup query failed: %s", exc)
        return True  # fail closed — don't create if we can't verify


def _emit_self_heal_alert(db: Session, shop_domain: str) -> None:
    from app.models.ops_alert import OpsAlert
    try:
        db.add(OpsAlert(
            severity="warning",
            source=f"uninstall_erasure:{shop_domain}",
            alert_type="uninstall_erasure_self_healed",
            shop_domain=shop_domain,
            summary=(
                f"Self-healed missing shop/redact for {shop_domain} — "
                f"created GdprRequest ourselves after 48h grace"
            ),
            detail=(
                "Shopify did not deliver the shop/redact webhook within "
                "48 hours of uninstall. The watchdog created the GdprRequest "
                "locally to guarantee erasure. Investigate whether the "
                "webhook subscription was dropped or misconfigured."
            ),
            resolved=False,
        ))
    except Exception as exc:
        log.warning("uninstall_erasure: alert write failed: %s", exc)


def run_uninstall_erasure_watchdog(db: Session) -> dict:
    """Scan for uninstalled shops past their grace period, create
    missing `shop_redact` GdprRequests. Returns a structured report."""
    from app.models.merchant import Merchant
    from app.models.gdpr_request import GdprRequest

    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "grace_hours": _GRACE_PERIOD_HOURS,
        "scanned": 0,
        "self_healed": 0,
        "already_redacted": 0,
    }

    cutoff = _now() - timedelta(hours=_GRACE_PERIOD_HOURS)
    try:
        rows = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "uninstalled",
                Merchant.uninstalled_at.isnot(None),
                Merchant.uninstalled_at < cutoff,
            )
            .order_by(Merchant.uninstalled_at.asc())
            .limit(_BATCH_CAP)
            .all()
        )
    except Exception as exc:
        log.warning("uninstall_erasure: merchant scan failed: %s", exc)
        return report

    report["scanned"] = len(rows)
    for merchant in rows:
        shop = merchant.shop_domain
        if _has_recent_redact_request(db, shop):
            report["already_redacted"] += 1
            continue
        try:
            new_req = GdprRequest(
                request_type="shop_redact",
                shop_domain=shop,
                status="pending",
                payload='{"created_by":"uninstall_erasure_watchdog"}',
            )
            db.add(new_req)
            db.flush()
            _emit_self_heal_alert(db, shop)
            report["self_healed"] += 1
            log.warning(
                "uninstall_erasure: SELF-HEALED shop=%s — Shopify did not "
                "deliver shop/redact within %dh grace",
                shop, _GRACE_PERIOD_HOURS,
            )
        except Exception as exc:
            log.warning(
                "uninstall_erasure: failed to create redact request for %s: %s",
                shop, exc,
            )

    if report["self_healed"] > 0:
        try:
            db.commit()
        except Exception as exc:
            log.warning("uninstall_erasure: commit failed: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
    return report
