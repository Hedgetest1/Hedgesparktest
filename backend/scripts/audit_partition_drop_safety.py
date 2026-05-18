#!/usr/bin/env python
# invariant-eligible: false
#   Static source scan of retention_task.py — code structure, not
#   runtime state. Commit-stage-only (like audit_retention_batched).
"""audit_partition_drop_safety.py — J4-part-1 structural preventer
(2026-05-18, design + verified by independent Agent a93059e).

Retention now DROPs fully-aged WHOLE `events` partitions. The single
make-or-break operational invariant: the detach MUST be
`DETACH PARTITION ... CONCURRENTLY`. A plain `DETACH PARTITION` (or a
direct `DROP TABLE` on a still-attached child) takes
AccessExclusiveLock on the PARENT `events` — at 10k that stalls the
ENTIRE storefront ingest path. A future refactor must not be able to
silently drop `CONCURRENTLY`, nor introduce a parent-table
`DROP TABLE events`, nor a partition DROP not gated by the
retention-cutoff predicate. This audit fails the commit if any of
those regress.

Checks on app/workers/tasks/retention_task.py:
  (A) every `DETACH PARTITION` occurrence is `DETACH PARTITION ...
      CONCURRENTLY` (hard FAIL on a plain DETACH — parent
      AccessExclusive = 10k ingest stall).
  (B) no bare parent-table drop: a `DROP TABLE events` not followed
      by an `_y` child suffix / quoted child name (catastrophic
      irreversible loss of the whole hot-path table).
  (C) the drop path keeps the `events_default` never-drop exclusion
      AND the `cutoff`/`<=` predicate gate (no ungated DROP).
  (D) `audit_retention_batched` still applies — the batched
      row-DELETE literal must remain (this audit asserts the DROP
      path is ADDITIVE, not a replacement of the superset fallback).

Non-vacuous: it FAILS on a plain `DETACH PARTITION` (no CONCURRENTLY)
and on a `DROP TABLE events` parent literal.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = (Path(__file__).resolve().parent.parent
          / "app" / "workers" / "tasks" / "retention_task.py")

# `DETACH PARTITION <name>` — capture whether CONCURRENTLY follows
# before the statement-ending quote/paren/newline.
_DETACH_RE = re.compile(r"DETACH\s+PARTITION\s+[^\n'\"]*?(CONCURRENTLY)?\s*['\"\)]",
                        re.IGNORECASE)
_DETACH_ANY_RE = re.compile(r"DETACH\s+PARTITION", re.IGNORECASE)
_DETACH_CONC_RE = re.compile(r"DETACH\s+PARTITION[^\n]*?CONCURRENTLY",
                             re.IGNORECASE)
# bare parent drop: DROP TABLE events  NOT followed by _y / a quoted
# child / {name} interpolation.
_PARENT_DROP_RE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?events\b(?!_y|\"|\{|%)",
    re.IGNORECASE)


def main() -> int:
    if not TARGET.exists():
        print(f"audit_partition_drop_safety: FAIL — {TARGET} missing")
        return 1
    src = TARGET.read_text()
    bad: list[str] = []

    # (A) every DETACH PARTITION must be CONCURRENTLY
    n_detach = len(_DETACH_ANY_RE.findall(src))
    n_conc = len(_DETACH_CONC_RE.findall(src))
    if n_detach == 0:
        bad.append("no `DETACH PARTITION` found — J4-part-1 detach path "
                   "missing/renamed (a direct child DROP would "
                   "AccessExclusive-lock the parent ⟹ 10k ingest stall)")
    elif n_conc < n_detach:
        bad.append(f"{n_detach - n_conc} `DETACH PARTITION` occurrence(s) "
                   f"WITHOUT `CONCURRENTLY` — plain DETACH takes "
                   f"AccessExclusive on parent `events` ⟹ stalls ALL "
                   f"10k ingest. MUST be `DETACH PARTITION ... "
                   f"CONCURRENTLY`.")

    # (B) no bare parent-table drop
    if _PARENT_DROP_RE.search(src):
        bad.append("`DROP TABLE events` (PARENT) literal — catastrophic "
                   "irreversible loss of the 100M-row hot-path table. "
                   "Only standalone CHILD partitions may be dropped.")

    # (C) never-drop exclusion + predicate gate present
    if "events_default" not in src:
        bad.append("the `events_default` never-drop exclusion is gone — "
                   "the catch-all partition must NEVER be dropped.")
    if "cutoff" not in src or "<=" not in src:
        bad.append("the drop path's cutoff/`<=` predicate gate appears "
                   "absent — a partition DROP not gated by the retention "
                   "cutoff can drop in-window data.")

    # (E) autocommit must be set on the REAL DBAPI conn, not the
    # SQLAlchemy _ConnectionFairy (a bare `raw.autocommit = True` is
    # SILENT attribute-shadowing — never reaches psycopg2 ⟹ every
    # DETACH CONCURRENTLY raises ActiveSqlTransaction ⟹ J4 silently
    # dead. Independent Agent a28854e empirically proved this.)
    if "raw_connection()" in src and _DETACH_ANY_RE.search(src):
        if "driver_connection.autocommit" not in src:
            bad.append(
                "DETACH CONCURRENTLY path uses raw_connection() but does "
                "NOT set `.driver_connection.autocommit` — autocommit on "
                "the _ConnectionFairy is a silent no-op ⟹ DETACH "
                "CONCURRENTLY raises ActiveSqlTransaction (J4 dead).")
        if re.search(r"(?<!_)\braw\.autocommit\s*=", src):
            bad.append(
                "bare `raw.autocommit =` on the _ConnectionFairy — "
                "silent attribute-shadowing no-op. Set "
                "`raw.driver_connection.autocommit` instead.")

    # (D) batched row-DELETE superset fallback still present
    if "DELETE FROM events WHERE id IN (" not in src:
        bad.append("the batched row-DELETE superset fallback is gone — "
                   "DROP must be ADDITIVE (straddle partition + "
                   "events_default still need the row-DELETE).")

    if bad:
        print("audit_partition_drop_safety: FAIL — partition-DROP "
              "retention safety regressed:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("audit_partition_drop_safety: OK — DETACH is CONCURRENTLY "
          f"({n_conc}/{n_detach}), no parent DROP, events_default + "
          "cutoff-gate + batched fallback intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
