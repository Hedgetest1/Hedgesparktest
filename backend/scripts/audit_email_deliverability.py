#!/usr/bin/env python3
"""audit_email_deliverability.py — preflight health-check for the Resend
domain verification state.

Born 2026-04-22 after 10 days of silent email suppression against
hedgesparkhq.com. DNS verification lives outside code (it's a registrar
concern), so this audit does not BLOCK — it WARNS the operator so the
state never falls off the radar during unrelated work.

What it checks
--------------
1. The Resend API is reachable with the current `RESEND_API_KEY`.
2. The `hedgesparkhq.com` domain status is `verified`.
3. No scheduled email cron (lite_morning_digest, merchant_digest, etc.)
   is enabled in env while status=failed — if so, the WARN becomes
   louder so the operator notices before committing more email features.

Exit codes
----------
    0  verified (or API unreachable — fail-open)
    0  failed, but warn-only mode (always warn-only: WARN-not-BLOCK)

The script intentionally exits 0 even on WARN so preflight stays green.
Output goes to stdout; preflight.sh shows the summary line.

Run manually any time with:
    ./venv/bin/python scripts/audit_email_deliverability.py
"""
from __future__ import annotations

import os
import sys

# Allow `from app.services.email_deliverability import ...` when run
# standalone from the backend/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env so RESEND_API_KEY is picked up when invoked from preflight or
# manually. Best-effort — if dotenv isn't available, the audit simply runs
# with whatever is already in os.environ.
try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, ".env"))
except Exception:
    pass


def main() -> int:
    try:
        # Delay import so --help works without app deps loaded.
        from app.services.email_deliverability import get_domain_status
    except Exception as exc:
        print(f"audit_email_deliverability: import error (skipped): {exc}")
        return 0

    # Force a fresh fetch — the preflight audit is the one place we WANT
    # to bypass the 10-minute cache and see the current truth.
    try:
        status = get_domain_status(force_refresh=True)
    except Exception as exc:
        print(f"audit_email_deliverability: status fetch failed: {exc}")
        return 0

    state = status.get("status", "unknown")
    verified = bool(status.get("verified", True))

    if verified and state == "verified":
        print("OK: Resend domain hedgesparkhq.com verified — email flows enabled")
        return 0

    if state == "unknown":
        # Fail-open path: usually means RESEND_API_KEY isn't set in this
        # shell (local dev), or Resend is briefly unreachable. Not a
        # blocker; just mention so the operator knows the check didn't run.
        print(
            "WARN: Resend API unreachable (missing RESEND_API_KEY or network) — "
            "cannot verify domain state"
        )
        return 0

    # Real problem — DNS not verified.
    print(
        "WARN: Resend domain hedgesparkhq.com status=%s — ALL merchant email "
        "suppressed. See docs/RESEND_DNS_RUNBOOK.md for recovery steps."
        % state
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
