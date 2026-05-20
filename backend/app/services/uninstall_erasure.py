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

from sqlalchemy import and_, exists
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
    # session-rollback: ok — caller `run_uninstall_erasure_watchdog` wraps each per-merchant iteration in `with savepoint_scope(db):` (16-site regression-lock entry). _emit_self_heal_alert is called INSIDE that savepoint — a poisoned-flush exception bubbles to savepoint_scope.__exit__ which rolls back the savepoint cleanly. Inner swallow is redundant but harmless; outer savepoint provides the real isolation.
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

    # operator-filter: GDPR uninstall erasure must process every shop
    # regardless of operator status — legal Art. 17 right-to-erasure
    # applies uniformly. Including operator/dev shops here is correct.
    cutoff = _now() - timedelta(hours=_GRACE_PERIOD_HOURS)
    # 10k GDPR-Art.17 tail-starvation fix (2026-05-18, surfaced by the
    # extended audit_worker_loop_cursor): an uninstalled merchant row
    # NEVER leaves this WHERE (install_status stays 'uninstalled'
    # forever; the redact request does not mutate it). The prior
    # `ORDER BY uninstalled_at ASC LIMIT _BATCH_CAP` with the dedup
    # done IN PYTHON inside the loop meant: once the oldest _BATCH_CAP
    # uninstalled rows are all already-redacted, EVERY cycle re-scans
    # those same 50 (all skipped) and a NEWLY-uninstalled merchant
    # whose Shopify shop/redact webhook failed — beyond position
    # _BATCH_CAP in the monotonically-growing ex-merchant set — would
    # NEVER get its self-healed erasure request → silent Art.17
    # non-compliance at scale. Root fix (better than a round-robin
    # cursor — no wasted re-scan): push the dedup predicate INTO the
    # query as a NOT EXISTS, so the scan returns ONLY merchants that
    # genuinely still NEED a redact request. The set now truly
    # self-drains (create request → excluded next cycle → the next
    # oldest is reached); ordered oldest-first = FIFO erasure fairness.
    # The in-loop `_has_recent_redact_request` stays as a TOCTOU race
    # guard (a request created between this query and the iteration).
    # worker-loop-cursor: ok — self-draining via the ~_recent_redact
    # NOT EXISTS below: the scan returns ONLY merchants that still need
    # a shop_redact request, ordered uninstalled_at ASC (FIFO erasure).
    # Creating the request removes the merchant from the set on the
    # next cycle, so the next-oldest is always reached — no fixed-
    # window tail starvation. A round-robin cursor would only add
    # wasted re-scan of already-redacted rows (§2 r10).
    dedup_cutoff = _now() - timedelta(days=_DEDUP_LOOKBACK_DAYS)
    _recent_redact = exists().where(
        and_(
            GdprRequest.shop_domain == Merchant.shop_domain,
            GdprRequest.request_type == "shop_redact",
            GdprRequest.created_at >= dedup_cutoff,
        )
    )
    try:
        rows = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "uninstalled",
                Merchant.uninstalled_at.isnot(None),
                Merchant.uninstalled_at < cutoff,
                ~_recent_redact,
            )
            .order_by(Merchant.uninstalled_at.asc())
            .limit(_BATCH_CAP)
            .all()
        )
    except Exception as exc:
        log.warning("uninstall_erasure: merchant scan failed: %s", exc)
        return report

    report["scanned"] = len(rows)
    from app.core.database import savepoint_scope
    for merchant in rows:
        shop = merchant.shop_domain
        if _has_recent_redact_request(db, shop):
            report["already_redacted"] += 1
            continue
        try:
            # SAVEPOINT-per-merchant (write_no_rollback class close
            # 2026-05-19). GDPR Art.17: this loop flushes a GdprRequest
            # per merchant then a SINGLE post-loop commit (line ~195). A
            # bare rollback on a failing merchant would discard EVERY
            # prior merchant's queued erasure request → regulatory
            # exposure. The savepoint rolls back only the failing
            # merchant; the rest still commit.
            with savepoint_scope(db):
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
            except Exception as exc:
                log.warning("uninstall_erasure: run_uninstall_erasure_watchdog failed: %s", exc)
    return report
