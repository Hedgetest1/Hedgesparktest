#!/usr/bin/env python3
"""JSONB array-length guard preventer.

Born 2026-04-27 from Gap #8 close DA-loop sibling hunt. Caught a latent
class: psycopg2 converts Python `None` to JSON null literal (`'null'::jsonb`)
on JSONB column inserts under some path configurations — NOT to SQL NULL.
SQL `IS NULL` does NOT match JSON null. Then `jsonb_array_length(<scalar>)`
panics with "cannot get array length of a scalar".

The bug is latent: tests pass when fixtures use SQL NULL, breaks when
real merchants happen to have JSON null literal stored. It DID surface
in test_first_discount_none_bucket of cohort-by-dimension and was fixed
across 9 sibling sites in lite_extras.py + conversion_metrics.py +
ltv_engine.py.

This preventer scans for `jsonb_array_length(...)` calls in raw SQL text
and asserts a `jsonb_typeof(<same_expr>) = 'array'` guard appears within
4 lines BEFORE the call (same SQL block).

Pre-flight blocker.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

BACKEND = Path("/opt/wishspark/backend")

# Files we scan: every .py under app/
SCAN_ROOTS = [BACKEND / "app"]
EXCLUDE_DIRS = {".venv", "venv", "__pycache__"}

# Pattern: jsonb_array_length(<EXPR>) where EXPR is the column or table.col
_JSONB_ARRAY_LEN_RE = re.compile(
    r"jsonb_array_length\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)"
)
# Pattern: jsonb_array_elements(<EXPR>) and jsonb_array_elements_text(<EXPR>).
# Same scalar-panic vulnerability — PostgreSQL can evaluate LATERAL
# expansion before WHERE filters.
_JSONB_ARRAY_ELEMS_RE = re.compile(
    r"jsonb_array_elements(?:_text)?\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)"
)
# Accepts both positive guard (`= 'array'`) and negative guard
# (`<> 'array'` which short-circuits on non-array via CASE/WHEN)
_JSONB_TYPEOF_RE = re.compile(
    r"jsonb_typeof\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)\s*(?:=|<>|!=)\s*'array'"
)
# Inline CASE WHEN jsonb_typeof = 'array' THEN <col> ELSE '[]'::jsonb END
# is the SAFE inline pattern that wraps the column to nullify scalars.
# Detect via the surrounding text: the elements call should be within
# 2 lines of a "CASE WHEN jsonb_typeof" or the column should be a CTE
# alias.
_INLINE_CASE_RE = re.compile(
    r"CASE\s+WHEN\s+jsonb_typeof\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)\s*=\s*'array'",
    re.IGNORECASE,
)


def _scan_file(path: Path) -> list[str]:
    """Return list of finding strings for unguarded jsonb calls."""
    findings: list[str] = []
    _raw = safe_read_text(path)
    if _raw is None:
        return findings
    lines = _raw.splitlines()

    full_text = "\n".join(lines)

    for idx, line in enumerate(lines):
        # Skip comments / explanatory docstrings
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("--"):
            continue

        # Class 1: jsonb_array_length() must be CASE-WRAPPED, not just
        # have a sibling jsonb_typeof guard in the same WHERE clause.
        #
        # The Postgres planner is FREE to reorder boolean clauses joined
        # by AND. So:
        #
        #   AND jsonb_typeof(col) = 'array'
        #   AND jsonb_array_length(col) > 0   -- planner can call this first → panic
        #
        # is NOT safe — observed live 2026-04-30 on /analytics/discount-codes
        # which had EXACTLY this pattern and produced "cannot get array
        # length of a scalar" 500s. The fix is CASE-wrap, which IS
        # short-circuit-evaluated:
        #
        #   AND CASE WHEN jsonb_typeof(col) = 'array'
        #            THEN jsonb_array_length(col) > 0
        #            ELSE FALSE
        #       END
        #
        # Audit rule: every jsonb_array_length(X) call must be in an
        # enclosing CASE WHEN branch that already verified
        # jsonb_typeof(X) = 'array'. Look back up to 6 lines for:
        #   (a) at least one `CASE WHEN` opener
        #   (b) a `jsonb_typeof(X) = 'array'` guard between that CASE
        #       WHEN and the array_length call
        #   (c) no `END` keyword between the typeof guard and the
        #       array_length (= the typeof's CASE hasn't closed)
        for m in _JSONB_ARRAY_LEN_RE.finditer(line):
            expr = m.group(1)
            expr_tail = expr.split(".")[-1]
            window_start = max(0, idx - 6)
            window_lines = lines[window_start:idx + 1]
            window = "\n".join(window_lines)
            # Position of THIS jsonb_array_length call within the window.
            cur_pos_in_window = window.rfind(line) + m.start()
            # Find typeof-array guards before the call, matching the
            # same column (tail-compared so `so.x` matches `x`).
            matching_typeof_positions = [
                tm.start()
                for tm in _JSONB_TYPEOF_RE.finditer(window)
                if tm.group(1).split(".")[-1] == expr_tail
                and tm.start() < cur_pos_in_window
            ]
            # CASE-wrapped if at least one matching typeof guard exists
            # before the array_length AND there's no `END` keyword
            # between that guard and the call (= the typeof's CASE
            # branch is still open).
            case_wrapped = False
            for tp in matching_typeof_positions:
                between = window[tp:cur_pos_in_window]
                if not re.search(r"\bEND\b", between, re.IGNORECASE):
                    # Also verify there's a CASE WHEN somewhere before
                    # the typeof position (otherwise the AND-pair is
                    # bare in a WHERE clause = vulnerable).
                    before_typeof = window[:tp]
                    if re.search(r"\bCASE\s+WHEN\b", before_typeof, re.IGNORECASE):
                        case_wrapped = True
                        break
            if case_wrapped:
                continue
            findings.append(
                f"{path.relative_to(BACKEND)}:{idx + 1}: "
                f"jsonb_array_length({expr}) NOT wrapped in "
                f"CASE WHEN jsonb_typeof({expr}) = 'array' THEN ... — "
                f"vulnerable to Postgres planner reorder + scalar panic"
            )

        # Class 2: jsonb_array_elements() / jsonb_array_elements_text()
        # in LATERAL or FROM — needs either:
        #  (a) inline CASE WHEN jsonb_typeof(<expr>) = 'array' wrapper
        #      around the column (within 4 lines back)
        #  (b) the source <expr> references a CTE alias that filtered
        #      by jsonb_typeof = 'array' (heuristic: alias starts with
        #      "vo." / "valid_orders." / "valid_" prefix)
        for m in _JSONB_ARRAY_ELEMS_RE.finditer(line):
            expr = m.group(1)
            window_start = max(0, idx - 4)
            window = "\n".join(lines[window_start:idx + 1])
            # Inline CASE-WHEN guard with same expr column tail
            expr_tail = expr.split(".")[-1]  # strip alias prefix
            inline_guarded = any(
                cm.group(1).split(".")[-1] == expr_tail
                for cm in _INLINE_CASE_RE.finditer(window)
            )
            # CTE alias heuristic: column source uses CTE-like alias
            cte_alias_safe = any(
                expr.startswith(prefix)
                for prefix in ("vo.", "valid_orders.", "valid_")
            )
            # Whole-file CTE pre-filter: file has WITH valid... AS (
            # ... jsonb_typeof = 'array' ...) before this line
            file_has_cte_filter = (
                "WITH valid" in full_text and
                "jsonb_typeof" in full_text and
                "= 'array'" in full_text and
                cte_alias_safe
            )
            if inline_guarded or file_has_cte_filter:
                continue
            findings.append(
                f"{path.relative_to(BACKEND)}:{idx + 1}: "
                f"jsonb_array_elements({expr}) without inline CASE-WHEN "
                f"typeof guard or CTE pre-filter — vulnerable to "
                f"'cannot extract elements from a scalar' panic on "
                f"JSON-null rows"
            )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on any finding (default: lenient)")
    args = ap.parse_args()

    all_findings: list[str] = []
    files_scanned = 0
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            files_scanned += 1
            all_findings.extend(_scan_file(p))

    if not all_findings:
        print(
            f"audit_jsonb_array_length_guard: OK — "
            f"{files_scanned} files scanned, all jsonb_array_length() "
            f"calls have jsonb_typeof guard"
        )
        return 0

    print(
        f"audit_jsonb_array_length_guard: FAIL — "
        f"{len(all_findings)} unguarded jsonb_array_length() call(s)"
    )
    print()
    for f in all_findings:
        print(f"  {f}")
    print()
    print("Fix: add `AND jsonb_typeof(<column>) = 'array'` BEFORE the")
    print("`jsonb_array_length(<column>)` call (same WHERE/CASE block,")
    print("within 4 lines). Background: psycopg2 may convert Python None")
    print("to JSON null literal (a JSONB scalar) instead of SQL NULL on")
    print("JSONB column insert; SQL `IS NULL` does NOT catch JSON null,")
    print("so the unguarded jsonb_array_length(scalar) call panics.")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
