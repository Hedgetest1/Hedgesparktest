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

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("billing_sync")

_MAX_PER_CYCLE = 10
_MAX_DEACTIVATIONS_BEFORE_HALT = 3
# P4 close 2026-05-12: parallel Shopify API calls. Threads carry the
# I/O cost; 20 in-flight requests is conservative (Shopify per-shop
# rate limit is ~2 req/s and each request hits a DIFFERENT shop, so
# our outbound capacity is the only constraint). Env-overridable for
# future scale tuning.
_MAX_CONCURRENCY = int(os.environ.get("BILLING_SYNC_CONCURRENCY", "20"))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_billing_sync(db: Session) -> dict:
    """
    Check Pro merchants' billing status against Shopify.

    Returns {"checked": int, "deactivated": int, "ok": int, "errors": list}
    """
    summary = {"checked": 0, "deactivated": 0, "ok": 0, "errors": []}

    # 10k tail-starvation fix (2026-05-18, surfaced by the extended
    # audit_worker_loop_cursor — a real sibling of the merchant_brain
    # §11 miss): the prior `.limit(_MAX_PER_CYCLE)` had NO order_by and
    # NO cross-cycle cursor, so at 10k Pro merchants this 15-min cycle
    # re-checked the SAME arbitrary _MAX_PER_CYCLE every time → ~all
    # other Pro merchants NEVER billing-verified → a merchant who
    # cancelled in Shopify keeps Pro access indefinitely (revenue +
    # entitlement integrity). Fix = the proven shared round-robin
    # cursor (mirror merchant_brain.tick_all_active_merchants post-fix
    # / aggregation_worker / intelligence_worker). At
    # n <= _MAX_PER_CYCLE (today: N=2 Pro) rr_slice returns the whole
    # list ⟹ ZERO behaviour change below scale (no 1k refactor).
    from app.workers._rr_cursor import (
        load_cursor as _rr_load, save_cursor as _rr_save,
        rr_slice as _rr_slice, next_cursor as _rr_next,
    )
    _BILLING_CURSOR_KEY = "hs:billing_sync:cursor"

    # Cheap deterministic spine: just the sorted shop_domains of every
    # billable Pro merchant (NOT the ORM rows — loading 10k ORM objects
    # to slice _MAX_PER_CYCLE would be its own 10k smell).
    all_domains = sorted(
        r[0] for r in db.query(Merchant.shop_domain).filter(
            Merchant.plan == "pro",
            Merchant.billing_active == True,
            Merchant.billing_charge_id.isnot(None),
            Merchant.install_status == "active",
        ).all()
    )
    if not all_domains:
        return summary

    _cursor = _rr_load(_BILLING_CURSOR_KEY)
    domain_slice = _rr_slice(all_domains, _cursor, _MAX_PER_CYCLE)

    # Load the full ORM rows for ONLY this cycle's slice (<= _MAX_PER_
    # CYCLE rows) — _deactivate + token decrypt need the ORM instance.
    merchants = (
        db.query(Merchant)
        .filter(Merchant.shop_domain.in_(domain_slice))
        .all()
    )
    # Advance the cursor by what we pulled this cycle (advance-by-
    # actual-processed; the next cycle resumes after this slice so
    # every Pro merchant is billing-verified within ⌈N/_MAX⌉ cycles).
    _rr_save(
        _BILLING_CURSOR_KEY,
        _rr_next(_cursor, len(domain_slice), len(all_domains)),
    )

    if not merchants:
        return summary

    # P4: I/O-bound per-merchant Shopify calls run in a thread pool.
    # Pre-fix: 10 merchants × 10s timeout = 100s serial wall-clock; at
    # uncapped 10k Pro merchants the projection was 28h. Post-fix: same
    # cap at 10/cycle completes in ~ceil(10/20) × max(per-req latency)
    # ≈ 1-3s. The pool also makes lifting _MAX_PER_CYCLE safe.
    #
    # Thread safety contract (audit 2026-05-12): worker threads MUST NOT
    # touch the SQLAlchemy Session or ORM instances. We extract every
    # value the HTTP call needs into an immutable _ChargeCheckJob on the
    # main thread BEFORE submitting; the worker consumes only primitives.
    # The Merchant ORM instance stays main-thread-only for _deactivate.
    from app.core.token_crypto import decrypt_token

    jobs: list[_ChargeCheckJob] = []
    for m in merchants:
        jobs.append(_ChargeCheckJob(
            shop_domain=m.shop_domain,
            charge_id=str(m.billing_charge_id),
            token=decrypt_token(m.access_token),
        ))
    job_to_merchant = {id(j): m for j, m in zip(jobs, merchants)}

    halted = False
    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY) as pool:
        future_to_job = {pool.submit(_check_charge_status, j): j for j in jobs}
        for fut in as_completed(future_to_job):
            job = future_to_job[fut]
            m = job_to_merchant[id(job)]
            if halted:
                # Halted: filter out any future that completes after the
                # halt threshold was reached. The `halted` flag is the
                # load-bearing safety; future.cancel() is best-effort
                # and a no-op for already-started futures (the common
                # case when N ≤ max_workers).
                continue
            try:
                status = fut.result()
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
                        halted = True
                        # Best-effort cancellation of not-yet-started
                        # futures; running futures continue but are
                        # filtered by the halted check above.
                        for pending in future_to_job:
                            pending.cancel()
                else:
                    summary["ok"] += 1

            except Exception as exc:
                log.warning("billing_sync: error checking %s: %s", job.shop_domain, exc)
                summary["errors"].append(f"{job.shop_domain}: {exc}")

    return summary


@dataclass(frozen=True)
class _ChargeCheckJob:
    """Immutable primitives passed to worker threads — NO ORM refs.

    Created on the main thread by run_billing_sync, consumed by
    _check_charge_status on a worker thread. Frozen so a worker
    cannot accidentally mutate shared state.
    """
    shop_domain: str
    charge_id: str
    token: str | None  # decrypted; None if decrypt failed on main thread


def _check_charge_status(job: _ChargeCheckJob) -> str:
    """
    Query Shopify for the charge status.

    Thread-safe: consumes only primitives from `job`; no ORM, no Session,
    no shared mutable state. Pure I/O + return value.

    Returns: "active" | "cancelled" | "declined" | "expired" | "frozen" | "pending" | "unknown"
    """
    if not job.token:
        return "unknown"

    try:
        import httpx
        url = (
            f"https://{job.shop_domain}/admin/api/2024-10"
            f"/recurring_application_charges/{job.charge_id}.json"
        )
        resp = httpx.get(
            url,
            headers={"X-Shopify-Access-Token": job.token},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return "cancelled"
        if resp.status_code != 200:
            log.warning("billing_sync: Shopify returned %d for %s", resp.status_code, job.shop_domain)
            return "unknown"

        data = resp.json()
        charge = data.get("recurring_application_charge", {})
        return charge.get("status", "unknown")

    except Exception as exc:
        log.warning("billing_sync: API call failed for %s: %s", job.shop_domain, exc)
        return "unknown"


def _deactivate(db: Session, merchant: Merchant, reason: str) -> None:
    """Deactivate a merchant's billing and log it."""
    merchant.billing_active = False
    db.flush()
    # Invalidate the auth-session cache so the next request sees the
    # deactivated state immediately. Cache (hs:auth:msv:v1:*) stores
    # tier alongside session_version since 2026-05-08.
    try:
        from app.core.redis_client import _client as _rc
        rc = _rc()
        if rc is not None:
            rc.delete(f"hs:auth:msv:v1:{merchant.shop_domain}")
    except Exception:
        pass  # SILENT-EXCEPT-OK: cache invalidation best-effort; 30s TTL bounds stale window

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
        after_state=json.dumps({"reason": reason, "charge_id": merchant.billing_charge_id}),
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
