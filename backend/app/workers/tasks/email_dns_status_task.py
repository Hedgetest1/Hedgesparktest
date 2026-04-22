"""
email_dns_status_task.py — Hourly Resend DNS verification poller.

Runs inside the agent_worker 15-min cycle but self-gates to ~1 hour.
Refreshes the `hs:email:domain_status:v1` cache and detects
verified ↔ failed flips via a sticky Redis state (`hs:email:last_verified:v1`).

On flip:
  - `failed → verified`: fire a 🟢 Telegram alert — founder-driven DNS fix
    just landed, merchant email flow is restored.
  - `verified → failed`: fire a 🔴 Telegram alert — DNS broke, all merchant
    email is being suppressed until re-verified.

Companion to:
  - `app/services/email_deliverability.py` — cache + org-domain check
  - `app/core/email.py::send_email()` — runtime suppression gate
  - `scripts/audit_email_deliverability.py` — preflight warn
  - `docs/RESEND_DNS_RUNBOOK.md` — founder recovery steps
"""
from __future__ import annotations

import logging
import time
from typing import Optional

_log = logging.getLogger("worker.agent.email_dns_status")

_INTERVAL_S = 3600  # 1 hour
_last_run: Optional[float] = None


def should_run() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def run() -> None:
    """Refresh cache + flip detection. Best-effort; never raises."""
    from app.services.email_deliverability import (
        get_domain_status,
        invalidate_cache,
        read_last_verified_state,
        write_last_verified_state,
    )

    invalidate_cache()
    status = get_domain_status(force_refresh=True)
    curr = bool(status.get("verified", True))
    prev = read_last_verified_state()
    write_last_verified_state(curr)

    # First run in this Redis instance — no flip to report.
    if prev is None:
        _log.info(
            "email_dns_status: first observation status=%s verified=%s",
            status.get("status"), curr,
        )
        return

    if curr == prev:
        _log.debug(
            "email_dns_status: unchanged status=%s verified=%s",
            status.get("status"), curr,
        )
        return

    # Flip detected — alert.
    if curr and not prev:
        message = (
            "🟢 Resend DNS verified — @hedgesparkhq.com emails will now "
            "deliver. Morning briefs, weekly digests, and monthly ROI "
            "emails resume on the next scheduled cycle."
        )
    else:
        message = (
            "🔴 Resend DNS FAILED — status="
            f"{status.get('status', 'unknown')}. Merchant email "
            "suppression is active. See docs/RESEND_DNS_RUNBOOK.md for "
            "recovery steps."
        )

    try:
        from app.services.telegram_agent import send_message  # type: ignore
        send_message(message)
    except Exception as exc:
        _log.warning("email_dns_status: telegram alert failed: %s", exc)

    _log.info(
        "email_dns_status: FLIP verified=%s→%s reason=%r",
        prev, curr, status.get("reason", ""),
    )
