"""
webhook_health_task.py — Merchant webhook health check + auto-repair.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Runs at most
once per 24 hours across all active merchants. Detects webhook drift
(missing or stale topics) and attempts auto-repair through the
webhook_health service.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("worker.aggregation.webhook_health")

_INTERVAL_S = 86_400  # 24 hours
_last_run: float | None = None


def should_run() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def run() -> None:
    """
    Check and optionally repair webhooks for all active merchants.

    Opens its own DB session (webhook_health uses the ORM). Non-fatal:
    errors are logged but never crash the caller's cycle.
    """
    from app.core.database import SessionLocal
    from app.models.merchant import Merchant
    from app.services.webhook_health import check_webhook_health, repair_missing_webhooks
    from app.services.audit import write_audit_log
    from app.services.alerting import write_alert
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    from app.services.webhook_monitor import record_check_result

    db = SessionLocal()
    try:
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.access_token.isnot(None),
            )
            .all()
        )
        _log.info("webhook health: checking %d active merchant(s)", len(merchants))

        for m in merchants:
            if m.shop_domain in _ONBOARDING_BLOCKLIST:
                continue

            try:
                report = check_webhook_health(db, m.shop_domain)
                if report.healthy:
                    record_check_result(m.shop_domain, healthy=True)
                    continue

                if report.error:
                    record_check_result(
                        m.shop_domain, healthy=False, error=report.error,
                    )
                    _log.info("webhook health: skip shop=%s error=%s", m.shop_domain, report.error)
                    continue

                _log.info("webhook health: drift shop=%s missing=%s stale=%s",
                          m.shop_domain, report.missing, report.stale)
                result = repair_missing_webhooks(db, m.shop_domain)
                db.commit()

                repair_succeeded = bool(result.repaired) and not result.failed
                record_check_result(
                    m.shop_domain, healthy=repair_succeeded,
                    missing=report.missing, stale=report.stale,
                    repair_attempted=True, repair_succeeded=repair_succeeded,
                )

                write_audit_log(
                    db,
                    actor_type="worker",
                    actor_name="aggregation_worker",
                    action_type="webhook_repair",
                    target_type="merchant",
                    target_id=m.shop_domain,
                    shop_domain=m.shop_domain,
                    before_state={"missing": report.missing, "stale": report.stale},
                    after_state={"repaired": result.repaired, "failed": result.failed},
                    status="completed" if not result.failed else "partial",
                    approval_mode="autonomous",
                )
                db.commit()

                if result.repaired:
                    _log.info("webhook health: repaired shop=%s topics=%s", m.shop_domain, result.repaired)
                    write_alert(
                        db, severity="info", source="aggregation_worker",
                        alert_type="webhook_repaired", shop_domain=m.shop_domain,
                        summary=f"Auto-repaired webhooks: {result.repaired}",
                    )
                    db.commit()
                if result.failed:
                    _log.warning("webhook health: repair FAILED shop=%s topics=%s", m.shop_domain, result.failed)
                    write_alert(
                        db, severity="warning", source="aggregation_worker",
                        alert_type="webhook_repair_failed", shop_domain=m.shop_domain,
                        summary=f"Webhook repair failed for: {result.failed}",
                        detail={"failed": result.failed, "repaired": result.repaired},
                    )
                    db.commit()

            except Exception as exc:
                _log.warning("webhook health: error shop=%s: %s", m.shop_domain, exc)
                db.rollback()

    finally:
        db.close()
