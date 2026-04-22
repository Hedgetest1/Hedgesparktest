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
3. The published DKIM TXT record decodes as strict base64. Gmail's
   verifier rejects records with embedded whitespace (Resend's lax
   verifier does not) — so a Resend "verified" status is insufficient
   to guarantee Gmail-side DKIM alignment. Born 2026-04-22 after
   Resend reported `last_event=delivered` for a test email Gmail
   silent-dropped — root cause was 3 embedded spaces in the published
   `p=` base64 from a Hostinger paste.

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

import base64
import os
import re
import subprocess
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


_DKIM_HOST = "resend._domainkey.hedgesparkhq.com"


def _check_published_dkim_strict() -> tuple[bool, str]:
    """Fetch the DKIM TXT record via dig and confirm the `p=` base64
    decodes under strict (whitespace-intolerant) rules.

    Returns (ok, reason). `ok=True` means Gmail would accept the
    signature from a structural standpoint; `ok=False` flags a
    published record that passes lax Resend checks but fails strict
    Gmail DMARC alignment.

    Queries Google Public DNS (8.8.8.8) directly — that is the
    resolver Gmail's inbound MTA consults when verifying DKIM. Using
    the local stub resolver instead (systemd-resolved at 127.0.0.53)
    would surface stale TTL cache after a DNS change and fire false
    WARNs until the record's TTL expires (up to 48 hours for our
    current records). Discovered 2026-04-22: minutes after the
    founder fixed the whitespace, 8.8.8.8 already served the clean
    record but the local resolver kept the broken one cached."""
    try:
        out = subprocess.run(
            ["dig", "@8.8.8.8", "+short", "TXT", _DKIM_HOST],
            capture_output=True, text=True, timeout=5.0,
        )
    except Exception as exc:
        return True, f"dns_lookup_unavailable: {exc}"  # fail-open

    raw = (out.stdout or "").strip()
    if not raw:
        return False, f"no TXT record at {_DKIM_HOST}"

    # dig +short returns each TXT string quoted and one record per line;
    # multi-string records are space-separated quoted chunks on one line.
    # Concatenate all chunks into a single string (standard DNS behavior).
    concatenated = "".join(re.findall(r'"([^"]*)"', raw)) or raw

    # Extract the p= base64 body (optional v=DKIM1; k=rsa; prefix tolerated).
    m = re.search(r"p=([^;]*)$", concatenated) or re.search(r"p=(.+)", concatenated)
    if not m:
        return False, "DKIM TXT missing p= tag"

    p_value = m.group(1)
    # Any whitespace inside base64 is invalid — Gmail's strict decoder
    # rejects it even though Resend's lax decoder (and dig's display
    # concatenation) swallow it. Flag explicitly.
    if any(c.isspace() for c in p_value):
        return False, (
            f"DKIM `p=` contains embedded whitespace — strict base64 will "
            f"fail signature verification (Gmail silent-drops). Fix: edit "
            f"{_DKIM_HOST} TXT at registrar, remove whitespace from the key."
        )

    try:
        base64.b64decode(p_value, validate=True)
    except Exception as exc:
        return False, f"DKIM `p=` strict base64 decode failed: {exc}"

    return True, ""


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

    # Orthogonal check: Resend's verifier is lax, Gmail's is strict. A
    # record can be "verified" on Resend and still silent-drop on Gmail
    # if the published base64 contains whitespace or malformed tags.
    dkim_ok, dkim_reason = _check_published_dkim_strict()

    if verified and state == "verified" and dkim_ok:
        print("OK: Resend domain hedgesparkhq.com verified — email flows enabled")
        return 0

    if verified and state == "verified" and not dkim_ok:
        print(
            "WARN: Resend says verified but published DKIM fails strict "
            "validation — Gmail will silent-drop. %s" % dkim_reason
        )
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
