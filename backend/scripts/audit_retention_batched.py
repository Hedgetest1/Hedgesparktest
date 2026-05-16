#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of retention module source — code structure, not
#   runtime state. Commit-stage-only by nature.
"""audit_retention_batched.py — structural preventer (10k).

Born 2026-05-16. Every retention DELETE used to be a SINGLE unbatched
table-wide statement (`DELETE FROM events WHERE timestamp < cutoff`),
and aggregation_worker wrapped all four in ONE transaction. At 10k
merchants the `events` table is ~100M rows AND is the storefront
tracker's hot-path INSERT target — an unbounded retention DELETE holds
row locks + the xmin horizon for minutes, stalling ingestion for every
merchant. A prior change had even *introduced* this by collapsing an
N+1 into one unbounded DELETE.

The contract: any `DELETE FROM <table>` in a retention module MUST be
self-limiting (id-scoped sub-select ending in `LIMIT`), so the caller
can commit per batch. This is the proven in-repo pattern
(app/services/data_retention.py).

FAIL (exit 1) if a string literal in a retention module contains
`DELETE FROM` but NOT both `id IN (` and `LIMIT`. A genuinely-bounded
delete (single-subject GDPR erasure, tiny-table truncate) opts out
with a `# unbatched-delete: ok — <reason>` comment in the same module
referencing the literal.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe read

_ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    _ROOT / "app" / "workers" / "tasks" / "retention_task.py",
    _ROOT / "app" / "services" / "data_retention.py",
    _ROOT / "app" / "services" / "event_bus.py",
]


def _violations_in(path: Path) -> list[str]:
    src = safe_read_text(path)
    if src is None:
        return []
    opt_out = "unbatched-delete: ok" in src
    out: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        s = node.value
        if "DELETE FROM" not in s.upper():
            continue
        batched = ("id IN (" in s or "ID IN (" in s.upper()) and "LIMIT" in s.upper()
        if not batched and not opt_out:
            line = getattr(node, "lineno", "?")
            snippet = " ".join(s.split())[:80]
            out.append(
                f"  {path.relative_to(_ROOT)}:{line} — unbatched DELETE "
                f"(no id-scoped LIMIT sub-select): \"{snippet}...\". "
                f"Use the batched pattern (see retention_task._run_batched / "
                f"data_retention._delete_events_older_than)."
            )
    return out


def main() -> int:
    violations: list[str] = []
    for t in TARGETS:
        if t.exists():
            violations.extend(_violations_in(t))
    if violations:
        print("audit_retention_batched: FAIL — unbatched retention DELETE "
              "(the 10k hot-path long-txn stall class):")
        print("\n".join(violations))
        return 1
    print("audit_retention_batched: OK — every retention DELETE is "
          "id-scoped batched (commit-per-batch safe).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
