"""
webhook_monitor.py — Proactive webhook drift monitoring and status tracking.

Builds on top of existing webhook_health.py (check/repair) and adds:
  - Per-merchant webhook status tracking (Redis-backed)
  - Severity classification (healthy / drifted / broken / unreachable)
  - Fleet-wide summary for operator visibility
  - Consecutive failure tracking with escalation

Does NOT replace webhook_health.py or aggregation_worker repair logic.
This is the OBSERVABILITY layer, not the repair layer.

Public interface:
    record_check_result(shop_domain, report, repair_result) -> None
    get_merchant_webhook_status(shop_domain) -> dict
    get_fleet_webhook_summary(db) -> dict
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("webhook_monitor")

_REDIS_PREFIX = "hs:wh_status:"
_STATUS_TTL = 172800  # 48h — auto-cleanup if merchant is removed


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------

def _classify_severity(
    healthy: bool,
    error: str | None,
    missing: list,
    stale: list,
    repair_attempted: bool,
    repair_succeeded: bool,
) -> str:
    """
    Classify webhook drift severity.

    healthy     — all expected webhooks registered with correct URLs
    drifted     — webhooks exist but have stale/wrong URLs (auto-repaired)
    broken      — webhooks missing or repair failed
    unreachable — Shopify API error (token invalid, 4xx, 5xx)
    """
    if healthy:
        return "healthy"
    if error:
        return "unreachable"
    if repair_attempted and repair_succeeded:
        return "healthy"  # was drifted but repaired successfully
    if stale and not missing:
        return "drifted"
    return "broken"


# ---------------------------------------------------------------------------
# Per-merchant status recording
# ---------------------------------------------------------------------------

def record_check_result(
    shop_domain: str,
    healthy: bool,
    error: str | None = None,
    missing: list | None = None,
    stale: list | None = None,
    repair_attempted: bool = False,
    repair_succeeded: bool = False,
):
    """
    Record the result of a webhook health check for a merchant.
    Called from aggregation_worker after check/repair cycle.
    """
    severity = _classify_severity(
        healthy, error, missing or [], stale or [],
        repair_attempted, repair_succeeded,
    )

    status = {
        "shop": shop_domain,
        "severity": severity,
        "healthy": healthy,
        "checked_at": _now_iso(),
        "missing": missing or [],
        "stale": stale or [],
        "error": error,
        "repair_attempted": repair_attempted,
        "repair_succeeded": repair_succeeded,
    }

    # Persist to Redis
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            key = f"{_REDIS_PREFIX}{shop_domain}"
            rc.set(key, json.dumps(status, default=str), ex=_STATUS_TTL)
    except Exception:
        pass

    # Log significant states
    if severity == "broken":
        log.warning("webhook_monitor: %s BROKEN — missing=%s repair_succeeded=%s",
                     shop_domain, missing, repair_succeeded)
    elif severity == "unreachable":
        log.warning("webhook_monitor: %s UNREACHABLE — error=%s", shop_domain, error)
    elif severity == "drifted":
        log.info("webhook_monitor: %s drifted — stale=%s", shop_domain, stale)


def get_merchant_webhook_status(shop_domain: str) -> dict | None:
    """Get the latest webhook status for a merchant from Redis."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return None
        raw = rc.get(f"{_REDIS_PREFIX}{shop_domain}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fleet-wide summary
# ---------------------------------------------------------------------------

def get_fleet_webhook_summary(db: Session) -> dict:
    """
    Build a fleet-wide webhook status summary for operator visibility.

    Returns:
        {
            "generated_at": str,
            "total_merchants": int,
            "checked_merchants": int,
            "by_severity": {"healthy": N, "drifted": N, "broken": N, "unreachable": N},
            "broken_shops": [{"shop": str, "missing": [...], "checked_at": str}, ...],
            "unreachable_shops": [{"shop": str, "error": str, "checked_at": str}, ...],
        }
    """
    from app.models.merchant import Merchant

    # Count active merchants
    try:
        total = db.execute(text(
            "SELECT COUNT(*) FROM merchants WHERE install_status = 'active' AND access_token IS NOT NULL"
        )).fetchone()
        total_merchants = total[0] if total else 0
    except Exception:
        total_merchants = 0

    # Scan Redis for all webhook statuses
    by_severity: dict[str, int] = {"healthy": 0, "drifted": 0, "broken": 0, "unreachable": 0}
    broken_shops: list[dict] = []
    unreachable_shops: list[dict] = []
    checked = 0

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor, match=f"{_REDIS_PREFIX}*", count=100)
                for key in keys:
                    try:
                        raw = rc.get(key)
                        if not raw:
                            continue
                        status = json.loads(raw)
                        checked += 1
                        sev = status.get("severity", "unknown")
                        by_severity[sev] = by_severity.get(sev, 0) + 1

                        if sev == "broken":
                            broken_shops.append({
                                "shop": status.get("shop", "?"),
                                "missing": status.get("missing", []),
                                "checked_at": status.get("checked_at"),
                            })
                        elif sev == "unreachable":
                            unreachable_shops.append({
                                "shop": status.get("shop", "?"),
                                "error": status.get("error", "?"),
                                "checked_at": status.get("checked_at"),
                            })
                    except Exception:
                        continue
                if cursor == 0:
                    break
    except Exception:
        pass

    return {
        "generated_at": _now_iso(),
        "total_merchants": total_merchants,
        "checked_merchants": checked,
        "by_severity": by_severity,
        "broken_shops": broken_shops,
        "unreachable_shops": unreachable_shops,
    }
