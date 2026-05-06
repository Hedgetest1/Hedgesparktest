#!/usr/bin/env python3
"""audit_orphan_alerts_no_growth.py — Pin orphan-alert hygiene.

An "orphan alert" is an unresolved row in `ops_alerts` whose
`shop_domain` does not appear in `merchants.shop_domain`. This is
typically caused by:

    1. Test-fixture leakage: a service (`risk_forecast`,
       `signal_webhooks`, etc.) opens its own `SessionLocal()` to
       persist a write_alert outside the test's SAVEPOINT, which
       writes a real row that escapes test cleanup.
    2. Merchant uninstall + delete pathway leaving alerts behind.

Both pollute /ops/system-health and the daily digest.

This audit runs in two modes:

  - `--strict` (preflight blocking): fails if any unresolved orphan
    alert references a synthetic-test-shop pattern (per
    `app.core.test_shop_blocklist`). The `write_alert` synthetic-
    shop guard added 2026-05-06 should keep this at 0.
  - default (info-only): reports total orphan count + breakdown
    for visibility, exits 0.

The audit doesn't currently fail on uninstall-orphans because the
gdpr/uninstall pipeline owns that cleanup; it would create false
positives during a redact-in-progress window.

# invariant-eligible: true
"""
from __future__ import annotations

import argparse
import os
import sys


_PROBE_UNAVAILABLE = object()  # sentinel — distinct from "0 orphans"


def _run_query():
    """Return (total_orphans, synthetic_orphans, breakdown_top_10)
    OR _PROBE_UNAVAILABLE if the DB connection itself can't be opened.

    Uses `app.core.database.SessionLocal` to inherit the same
    DATABASE_URL resolution path as the rest of the codebase
    (handles `.env` loading via app.main side effects when invoked
    through the venv). Fail-loud distinction between "no orphans"
    and "couldn't probe" prevents the silent-fallback false-OK that
    a simple `os.getenv("DATABASE_URL", "")` empty-default would
    produce when the audit runs outside a PM2-loaded env."""
    try:
        # Add backend root so `app.*` imports resolve.
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from app.core.database import SessionLocal
        from sqlalchemy import text
        from app.core.test_shop_blocklist import is_synthetic_test_shop
    except Exception as exc:
        print(f"audit_orphan_alerts_no_growth: skip — module import failed: {exc}")
        return _PROBE_UNAVAILABLE

    try:
        db = SessionLocal()
    except Exception as exc:
        print(f"audit_orphan_alerts_no_growth: skip — DB session failed: {exc}")
        return _PROBE_UNAVAILABLE

    try:
        rows = db.execute(text(
            """
            SELECT a.shop_domain, COUNT(*) AS cnt
            FROM ops_alerts a
            LEFT JOIN merchants m ON a.shop_domain = m.shop_domain
            WHERE a.resolved = false
              AND a.shop_domain IS NOT NULL
              AND m.shop_domain IS NULL
            GROUP BY a.shop_domain
            ORDER BY cnt DESC
            LIMIT 100
            """
        )).fetchall()
    except Exception as exc:
        print(f"audit_orphan_alerts_no_growth: skip — query failed: {exc}")
        return _PROBE_UNAVAILABLE
    finally:
        try:
            db.close()
        except Exception:
            pass

    if not rows:
        return (0, 0, [])

    total = sum(c for _, c in rows)
    synthetic = sum(c for s, c in rows if is_synthetic_test_shop(s))
    return (total, synthetic, [(s, c) for s, c in rows[:10]])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="Fail on synthetic-shop orphans (write_alert guard regression).")
    args = parser.parse_args()

    result = _run_query()
    if result is _PROBE_UNAVAILABLE:
        # Fail-loud per CLAUDE.md §11 + §2 rule 2 ("no half-truths"):
        # don't pretend "0 orphans" when we couldn't actually probe.
        # Strict mode treats unavailable as a regression to surface
        # config issues; default mode passes (info-only).
        return 1 if args.strict else 0
    total, synthetic, top10 = result

    if synthetic > 0 and args.strict:
        print(
            f"audit_orphan_alerts_no_growth: FAIL — {synthetic} unresolved "
            f"orphan alert(s) on synthetic test shops (write_alert guard regression?). "
            f"Top:"
        )
        for shop, cnt in top10[:10]:
            print(f"  {shop:<60} {cnt}")
        print(
            "\nFix: confirm `app.core.test_shop_blocklist.is_synthetic_test_shop` "
            "still flags these patterns AND `app.services.alerting.write_alert` "
            "still calls the guard before any DB persist. Then run the cleanup "
            "snippet from the 2026-05-06 audit memo."
        )
        return 1

    print(
        f"audit_orphan_alerts_no_growth: OK — {total} unresolved orphan alert(s) "
        f"({synthetic} synthetic-shop). "
        f"Synthetic guard: {'no regression' if synthetic == 0 else 'REGRESSED — see --strict'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
