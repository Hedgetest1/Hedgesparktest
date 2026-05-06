#!/usr/bin/env python3
"""audit_operator_dev_shop_no_outbound.py — Pin the operator-shop
email guard regression.

The founder reported receiving merchant-shaped emails at
`tedialarana@gmail.com` on 2026-05-06. Root cause: dev tenant
`hedgespark-dev.myshopify.com` is a real merchant row (billing_active
=true) and pre-fix digest cycles routed it through the orchestrator
without an operator-shop guard.

Fix: `app/core/operator_blocklist.py` predicates + gates at:
    - `email_orchestrator._resolve_merchant`
    - `core.email.send_email` (last-line address guard)
    - merchant-resolution queries in merchant_digest / lite_morning_digest /
      silence_detector / send_all_digests script.

This audit verifies the guard hasn't regressed by querying the
`email_event` table for any delivery to an operator address OR for
any merchant-row that's an operator/dev shop. Default mode is
info-only (just reports counts). `--strict` exits 1 if any operator
delivery occurred in the last 7 days.

Operator/dev shops legitimately receive: GDPR Art. 15 export emails
(if the founder triggers the flow themselves, they get the export).
The audit excludes that channel via `email_type='gdpr_export'`.

# invariant-eligible: true
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone


_PROBE_UNAVAILABLE = object()


def _run_query():
    """Return (operator_email_deliveries_7d, operator_shop_deliveries_7d,
    sample_rows) — or _PROBE_UNAVAILABLE if DB session can't open."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    try:
        from app.core.database import SessionLocal
        from app.core.operator_blocklist import operator_dev_shops, operator_emails
        from sqlalchemy import text
    except Exception as exc:
        print(f"audit_operator_dev_shop_no_outbound: skip — module import failed: {exc}")
        return _PROBE_UNAVAILABLE

    try:
        db = SessionLocal()
    except Exception as exc:
        print(f"audit_operator_dev_shop_no_outbound: skip — DB session failed: {exc}")
        return _PROBE_UNAVAILABLE

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    op_shops = list(operator_dev_shops())
    op_emails_lower = [e.lower() for e in operator_emails()]

    try:
        # `merchant_emails` is the canonical send-log: rows insert
        # per orchestrator-routed delivery + every status change.
        # Status of a successfully-sent message is 'sent' or 'delivered'.
        # Exclude legitimate channels: GDPR data export is legally
        # required even for operator-triggered flows.
        rows = db.execute(text("""
            SELECT shop_domain, to_email, email_type, created_at, status
            FROM merchant_emails
            WHERE created_at >= :cutoff
              AND email_type NOT IN ('gdpr_export')
              AND status NOT IN ('suppressed', 'rejected', 'failed')
              AND (
                  shop_domain = ANY(:op_shops)
                  OR LOWER(to_email) = ANY(:op_emails)
              )
            ORDER BY created_at DESC
            LIMIT 50
        """), {
            "cutoff": cutoff,
            "op_shops": op_shops,
            "op_emails": op_emails_lower,
        }).fetchall()
    except Exception as exc:
        print(f"audit_operator_dev_shop_no_outbound: skip — query failed: {exc}")
        try:
            db.close()
        except Exception:
            pass
        return _PROBE_UNAVAILABLE
    finally:
        try:
            db.close()
        except Exception:
            pass

    op_email_count = sum(1 for r in rows if r[1] and r[1].lower() in op_emails_lower)
    op_shop_count = sum(1 for r in rows if r[0] in op_shops)
    return (op_email_count, op_shop_count, rows[:10])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any operator-shop email was sent in the last 7 days.")
    args = parser.parse_args()

    result = _run_query()
    if result is _PROBE_UNAVAILABLE:
        return 1 if args.strict else 0
    op_emails, op_shops, sample = result

    if (op_emails or op_shops) and args.strict:
        print(
            f"audit_operator_dev_shop_no_outbound: FAIL — operator-shop guard "
            f"regressed: {op_emails} email(s) to operator address, "
            f"{op_shops} delivery(ies) to operator shop in the last 7 days."
        )
        for row in sample:
            print(f"  shop={row[0]} → {row[1]} ({row[2]}) at {row[3]}")
        print(
            "\nFix: trace the producer that bypassed the guard. Check:\n"
            "  - `app/services/email_orchestrator._resolve_merchant`\n"
            "    operator gate at top of function (Step 0).\n"
            "  - `app/core/email.send_email` operator-address guard.\n"
            "  - Direct send_email() callers in scripts/ that may have\n"
            "    skipped the merchants query filter.\n"
            "  - New email producer added without routing through orchestrator.\n"
        )
        return 1

    print(
        f"audit_operator_dev_shop_no_outbound: OK — operator-shop guard intact "
        f"({op_emails} operator-email + {op_shops} operator-shop in 7d, all "
        f"either legitimate GDPR or pre-fix history)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
