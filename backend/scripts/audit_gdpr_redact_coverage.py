#!/usr/bin/env python3
"""
audit_gdpr_redact_coverage.py — enforce shop_redact coverage invariant.

Born 2026-04-23 after the Tier-A audit discovered the hardcoded table
list in `gdpr_processor._process_shop_redact` was missing 23 tables
with `shop_domain` column — a live GDPR Art. 17 non-compliance.

Rule: every table in the DB with a `shop_domain` column MUST either be
listed in `_process_shop_redact.tables` OR explicitly preserved in
`_GDPR_PRESERVE_TABLES`.

The invariant is verified against the LIVE DB schema (not against model
definitions) because the deletion is from DB reality — if the DB has a
table we don't know about, Shopify's redaction still targets that data.

Preserved (compliance-required retention):
  - `audit_log` — GDPR Art. 5(2) accountability chain; immutable
  - `merchants` — deleted last in _process_shop_redact, separately

Any new table with `shop_domain` added to the schema without being
listed in the hardcoded tables array will FAIL this preflight check.
The fix: add the table to the `tables` list in `gdpr_processor.py`
(in FK-safe order — leaf tables first).

Exit codes
----------
  0  every shop_domain table is covered (deletion list OR preserved)
  1  one or more tables with shop_domain are NOT covered

Usage
-----
    ./scripts/audit_gdpr_redact_coverage.py          # report
    ./scripts/audit_gdpr_redact_coverage.py --strict # exit 1 on any miss
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GDPR_PROCESSOR = REPO_ROOT / "app" / "services" / "gdpr_processor.py"

# Make `app` importable when the script is run directly from scripts/
# (mirrors the path-injection other scripts rely on).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Tables that MUST be preserved on shop_redact. These are the compliance
# exceptions. Any addition here is a deliberate compliance decision and
# must be justified in the comment beside the entry.
_PRESERVE_TABLES = {
    "audit_log",     # GDPR Art. 5(2) accountability — immutable by design
    "merchants",     # deleted last in _process_shop_redact, not via bulk loop
}


def _extract_hardcoded_tables(src: str) -> set[str]:
    """Return the set of table names in the `tables = [...]` literal
    inside `_process_shop_redact`. Uses a tolerant regex because the
    list is a simple Python literal with one quoted string per line."""
    # Match the function body to the first `]` closing its tables literal
    m = re.search(
        r"def _process_shop_redact.*?\btables\s*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    if not m:
        return set()
    body = m.group(1)
    return set(re.findall(r'"([a-zA-Z_][\w]*)"', body))


def _extract_db_tables_with_shop_domain() -> set[str]:
    """Query live DB for every table with a shop_domain column, EXCLUDING
    partition children. A DELETE on a range-partitioned parent (e.g.,
    `events`) cascades to its partitions, so listing both parent and
    children would double-count and pollute the "missing" report."""
    from app.core.database import engine
    from sqlalchemy import text

    with engine.connect() as conn:
        # 1. All tables with shop_domain
        rows = conn.execute(text("""
            SELECT table_name FROM information_schema.columns
            WHERE column_name = 'shop_domain'
              AND table_schema = 'public'
            GROUP BY table_name
        """)).fetchall()
        all_shop_tables = {r[0] for r in rows}

        # 2. Partition children (skip — redacted via parent)
        rows = conn.execute(text("""
            SELECT c.relname AS child
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relkind = 'p'
        """)).fetchall()
        partition_children = {r[0] for r in rows}

    return all_shop_tables - partition_children


def main(argv: list[str]) -> int:
    strict = "--strict" in argv

    try:
        src = GDPR_PROCESSOR.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"audit_gdpr_redact_coverage: FAIL — cannot read {GDPR_PROCESSOR}: {exc}")
        return 1

    hardcoded = _extract_hardcoded_tables(src)
    if not hardcoded:
        print("audit_gdpr_redact_coverage: FAIL — could not parse tables literal from gdpr_processor.py")
        return 1

    try:
        db_tables = _extract_db_tables_with_shop_domain()
    except Exception as exc:
        print(f"audit_gdpr_redact_coverage: FAIL — DB query failed: {exc}")
        return 1

    # Tables in DB but not hardcoded and not preserved
    missing = (db_tables - hardcoded) - _PRESERVE_TABLES

    # Tables hardcoded but no longer in DB (stale entries — not fatal)
    stale = hardcoded - db_tables

    if not missing:
        total_redacted = len(hardcoded & db_tables)
        total_preserved = len(db_tables & _PRESERVE_TABLES)
        print(
            f"audit_gdpr_redact_coverage: clean — "
            f"{total_redacted} tables will be redacted + "
            f"{total_preserved} preserved (audit_log, merchants) = "
            f"{total_redacted + total_preserved}/{len(db_tables)} covered"
        )
        if stale:
            print(
                f"  note: {len(stale)} hardcoded entries no longer in DB "
                f"(tolerated): {sorted(stale)}"
            )
        return 0

    print(
        f"audit_gdpr_redact_coverage: FAIL — {len(missing)} table(s) with "
        f"shop_domain NOT covered by shop_redact"
    )
    print()
    print("Missing from _process_shop_redact.tables (and not in _PRESERVE_TABLES):")
    for t in sorted(missing):
        print(f"  - {t}")
    print()
    print("Remediation:")
    print("  1. Add each table to the `tables = [...]` list in")
    print("     app/services/gdpr_processor.py::_process_shop_redact")
    print("     (leaf tables first to honour FK constraints).")
    print("  2. If a table MUST be preserved for compliance (e.g., a new")
    print("     audit chain), add it to _PRESERVE_TABLES in this script")
    print("     with a justification comment.")
    print()
    print("Per GDPR Art. 17 + Shopify App Store policy, every table with")
    print("merchant data MUST be redacted on shop/redact — a single missed")
    print("table = non-compliance + app-store-removal risk.")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
