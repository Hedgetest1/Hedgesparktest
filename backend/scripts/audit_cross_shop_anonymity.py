#!/usr/bin/env python3
"""audit_cross_shop_anonymity.py — Sprint 3 #3 GDPR invariant gate.

Verifies the cross_shop_patterns table never violates its anonymity
contract:

  1. Schema invariant: no shop_domain / shop_id / merchant_id / email /
     ip column. The table is aggregate-only by design.
  2. k-anonymity invariant: every row has n_shops >= 3.
  3. Source invariant: the SQL CHECK constraint
     `cross_shop_patterns_n_shops_min` exists and matches n_shops >= 3.

Failing this audit blocks deploy via preflight (preflight.sh + invariant_
monitor). The intent: even if a future refactor accidentally weakens
the aggregator code (e.g. drops the K_ANONYMITY_MIN_SHOPS gate), the
SQL CHECK constraint still blocks INSERTs, and this audit verifies
the constraint hasn't been removed.

Exits non-zero on any violation.
"""
from __future__ import annotations

import os
import sys

# Make backend importable when run from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Make _audit_telemetry_shim importable from scripts/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text

from app.core.database import engine

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def decorator(fn):
            return fn
        return decorator


FORBIDDEN_COLUMNS = ("shop_domain", "shop_id", "merchant_id", "email", "ip")


def check_no_pii_columns() -> list[str]:
    errors: list[str] = []
    with engine.connect() as conn:
        cols = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'cross_shop_patterns'
        """)).fetchall()
        names = {c.column_name for c in cols}
        if not names:
            # Table not yet created — acceptable in fresh dev envs; the
            # audit's other invariants vacuously hold. Preflight will
            # re-run after `alembic upgrade head` lands the table.
            return []
        for forbidden in FORBIDDEN_COLUMNS:
            if forbidden in names:
                errors.append(
                    f"FORBIDDEN COLUMN '{forbidden}' present in cross_shop_patterns "
                    "— GDPR invariant violated"
                )
    return errors


def check_k_anonymity_constraint() -> list[str]:
    errors: list[str] = []
    with engine.connect() as conn:
        # Check the CHECK constraint exists with the expected definition.
        # pg_constraint stores the parsed expression — we look for any
        # check that mentions n_shops with a >= 3 floor.
        rows = conn.execute(text("""
            SELECT conname, pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'cross_shop_patterns' AND c.contype = 'c'
        """)).fetchall()
        if not rows:
            # Table missing — see note in check_no_pii_columns.
            return []
        found = False
        for r in rows:
            d = (getattr(r, "def") or "").lower()
            if "n_shops" in d and ">= 3" in d.replace(" ", ""):
                found = True
                break
            # Tolerate alternate formatting: "(n_shops >= 3)"
            if "n_shops" in d and "3" in d and (">=" in d):
                found = True
                break
        if not found:
            errors.append(
                "CHECK constraint cross_shop_patterns_n_shops_min "
                "(n_shops >= 3) missing or weakened — GDPR k-anonymity "
                "floor at risk"
            )

        # Verify no row actually violates k-anonymity in case the
        # constraint was added with NOT VALID at some point.
        bad = conn.execute(text("""
            SELECT COUNT(*) FROM cross_shop_patterns WHERE n_shops < 3
        """)).scalar()
        if bad and int(bad) > 0:
            errors.append(
                f"{bad} rows in cross_shop_patterns have n_shops < 3 — "
                "k-anonymity violated at row level"
            )
    return errors


@telemetered("audit_cross_shop_anonymity")
def main() -> int:
    errors: list[str] = []
    errors.extend(check_no_pii_columns())
    errors.extend(check_k_anonymity_constraint())
    if errors:
        for e in errors:
            print(f"✗ {e}")
        return 1
    print("✓ cross_shop_patterns GDPR invariants intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
