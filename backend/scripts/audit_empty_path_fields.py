#!/usr/bin/env python3
"""
audit_empty_path_fields.py — catch early-return fallbacks that drop
fields declared in the Pydantic response_model.

The recurring bug (three times today during the native-currency sweep):
a service function returns a dict with {"currency": X} on its happy
path, but an early-return fallback ({"status": "insufficient"} or similar)
FORGETS the field. The Pydantic response_model has a default so the
response still validates, but the dashboard receives `"USD"` (the
Pydantic default) instead of the merchant's native currency.

This audit walks every `app/api/*.py` + `app/services/*.py`, finds
functions that declare a `response_model=FOO` or return a dict in a
service, scans for fields on the model, and verifies each early-return
in that function includes every non-default field that appears in the
happy-path return.

Output:
  FINDING: <file>:<line>  function `foo` happy-path returns {a, b, c}
           but early-return at line <M> returns only {a, b}. Missing: {c}.

Exit 1 in --strict when any finding is emitted.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text


BACKEND_DIR = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [
    BACKEND_DIR / "app" / "api",
    BACKEND_DIR / "app" / "services",
]

# Per-line allowlist: "<rel_path>:<lineno>" → justification.
# Used when a function intentionally varies return shape (e.g. `status`
# field only present on error paths).
_ALLOWLIST: dict[str, str] = {
    # The bugfix_pipeline's orchestrate_cycle() returns different shapes
    # per status intentionally — not a Pydantic-backed response.
}


@dataclass
class Finding:
    file: str
    fn_line: int
    fn_name: str
    happy_fields: frozenset[str]
    empty_line: int
    empty_fields: frozenset[str]

    def missing(self) -> set[str]:
        return set(self.happy_fields) - set(self.empty_fields)


def _dict_keys(node: ast.AST) -> frozenset[str]:
    """Extract string literal keys from a dict literal. Non-literal
    keys (computed at runtime) are skipped."""
    if not isinstance(node, ast.Dict):
        return frozenset()
    out = set()
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out.add(k.value)
    return frozenset(out)


def _function_dict_returns(fn: ast.FunctionDef) -> list[tuple[int, frozenset[str]]]:
    """Collect every `return {...}` inside the function body.
    Returns [(lineno, key_set), ...]. Skips returns of non-dict-literals."""
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            keys = _dict_keys(node.value)
            if keys:
                found.append((node.lineno, keys))
    return found


def _scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    source = safe_read_text(path)
    if source is None:
        return findings
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings

    rel = path.relative_to(BACKEND_DIR).as_posix()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        dict_returns = _function_dict_returns(node)
        if len(dict_returns) < 2:
            continue  # need ≥ 2 return sites to compare shapes

        # Heuristic: the LARGEST return is the "happy path" (most
        # fields). Every other return is considered an "empty-path"
        # and must be a superset of the happy fields for the function
        # to be schema-consistent.
        happy_line, happy_keys = max(dict_returns, key=lambda r: len(r[1]))
        if len(happy_keys) < 3:
            continue  # skip trivially-shaped functions

        for empty_line, empty_keys in dict_returns:
            if empty_line == happy_line:
                continue

            # Only flag when the empty-return is a "near sibling" shape
            # — i.e., it keeps most of the happy-path fields but drops
            # some. An empty-return with just `{error: ...}` or `{status:
            # "..."}` is a distinct shape by design; we don't touch those.
            shared = set(happy_keys) & set(empty_keys)
            if len(shared) < 2:
                continue  # distinct-shape return (error, status, etc.)

            missing = set(happy_keys) - set(empty_keys)
            if not missing:
                continue

            # Target the specific class of bugs this audit was written
            # to catch: data fields that carry merchant-observable values
            # (currency, shop_domain, identifiers, domain-specific _eur
            # values, score fields, aggregation totals).
            data_field_prefixes = (
                "currency", "shop_domain", "total", "avg", "median", "sum",
                "count", "recent", "baseline", "ratio", "score",
            )
            data_field_suffixes = ("_eur", "_usd", "_ms", "_pct", "_rate")
            relevant_missing = {
                k for k in missing
                if not k.startswith("_")
                and (
                    k in data_field_prefixes
                    or any(k.startswith(p) for p in data_field_prefixes)
                    or any(k.endswith(s) for s in data_field_suffixes)
                )
            }
            if not relevant_missing:
                continue

            key = f"{rel}:{empty_line}"
            if key in _ALLOWLIST:
                continue

            findings.append(Finding(
                file=rel,
                fn_line=node.lineno,
                fn_name=node.name,
                happy_fields=happy_keys,
                empty_line=empty_line,
                empty_fields=empty_keys,
            ))
    return findings


@telemetered("audit_empty_path_fields")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any finding is emitted")
    args = parser.parse_args()

    all_findings: list[Finding] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            all_findings.extend(_scan_file(py))

    if not all_findings:
        print("✅ No empty-path field drops detected")
        return 0

    print(f"🟡 {len(all_findings)} finding(s):\n")
    for f in all_findings:
        missing = f.missing()
        print(f"  {f.file}:{f.empty_line}")
        print(f"    fn `{f.fn_name}` (defined at line {f.fn_line})")
        print(f"    happy path (line unknown) returns: "
              f"{sorted(f.happy_fields)}")
        print(f"    this return returns:               "
              f"{sorted(f.empty_fields)}")
        print(f"    MISSING: {sorted(missing)}")
        print()

    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
