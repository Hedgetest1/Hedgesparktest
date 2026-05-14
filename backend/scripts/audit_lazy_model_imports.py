#!/usr/bin/env python3
"""audit_lazy_model_imports.py — Pin G6.

Catch `from app.models.X import Y` patterns INSIDE function bodies
in hot-path code (app/api/ + app/services/). SQLAlchemy mapper
compilation on first invocation costs ~120-180ms per worker per
import — paid at cold-start on every fresh request. Module-level
imports move that cost to import-time (once per worker boot).

Born 2026-05-11 after the SLO cold-path investigation found a
lazy `from app.models.merchant import Merchant` inside
`revenue_metrics.get_shop_currency` was causing today_snapshot +
orders_summary p95 to spike to 1016ms (cold) vs 18ms (warm). Fix in
commit 49df540 hoisted the import → 209ms → 57ms.

Policy:
  - app/api/*.py and app/services/*.py: `from app.models.X import Y`
    MUST be at module level, NOT inside def/async-def bodies.
  - Exempt: app/workers/*.py (workers are not request-hot-path),
    tests/*, scripts/*.
  - Exempt: imports that resolve circular-dependency cycles —
    annotated with `# audit_lazy_model_imports: ok — circular import`.

Exit codes:
  0 — no findings OR all findings are exempted
  1 — at least one unexempted lazy model import in hot-path code

# invariant-eligible: false — runs at preflight, not periodic
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
from _audit_io import safe_read_text


REPO_ROOT = pathlib.Path("/opt/wishspark/backend")
HOT_PATH_DIRS = [
    REPO_ROOT / "app" / "api",
    REPO_ROOT / "app" / "services",
]
EXEMPT_FILES = {
    # Files that intentionally lazy-import to break a circular dependency.
    # Each entry MUST be paired with a code-side `# audit_lazy_model_imports:
    # ok — <reason>` comment so the doctrine reason lives at the
    # import site, not just in this allowlist.
}


def _is_model_import(node: ast.ImportFrom) -> bool:
    """True for `from app.models.X import Y` shapes."""
    module = node.module or ""
    return module == "app.models" or module.startswith("app.models.")


def _scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return [(lineno, snippet)] for every model import inside a
    function body (NOT at module level)."""
    src = safe_read_text(path)
    if src is None:
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    src_lines = src.splitlines()
    findings: list[tuple[int, str]] = []

    class FunctionImportVisitor(ast.NodeVisitor):
        def __init__(self):
            self.fn_depth = 0

        def visit_FunctionDef(self, node):
            self.fn_depth += 1
            self.generic_visit(node)
            self.fn_depth -= 1

        def visit_AsyncFunctionDef(self, node):
            self.fn_depth += 1
            self.generic_visit(node)
            self.fn_depth -= 1

        def visit_ImportFrom(self, node):
            if self.fn_depth > 0 and _is_model_import(node):
                imported = ", ".join(a.name for a in node.names)
                snippet = f"from {node.module} import {imported}"
                # Check for exemption comment on same line. Re-uses
                # the cached `src_lines` from the enclosing scope —
                # avoids a second TOCTOU-prone read on each visit.
                line = (
                    src_lines[node.lineno - 1]
                    if 0 <= node.lineno - 1 < len(src_lines) else ""
                )
                if "audit_lazy_model_imports: ok" in line:
                    return
                findings.append((node.lineno, snippet))
            self.generic_visit(node)

    FunctionImportVisitor().visit(tree)
    return findings


def main() -> int:
    all_findings: list[tuple[pathlib.Path, int, str]] = []
    for dir_path in HOT_PATH_DIRS:
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.rglob("*.py")):
            rel = file_path.relative_to(REPO_ROOT)
            if str(rel) in EXEMPT_FILES:
                continue
            for lineno, snippet in _scan_file(file_path):
                all_findings.append((rel, lineno, snippet))

    if not all_findings:
        print("audit_lazy_model_imports: clean — 0 lazy model imports in hot-path code")
        return 0

    # Group by file for readable output
    by_file: dict[pathlib.Path, list[tuple[int, str]]] = {}
    for rel, lineno, snippet in all_findings:
        by_file.setdefault(rel, []).append((lineno, snippet))

    print(
        f"audit_lazy_model_imports: FAIL — "
        f"{len(all_findings)} lazy model import(s) in {len(by_file)} hot-path file(s)"
    )
    for rel, items in by_file.items():
        print(f"  {rel}:")
        for lineno, snippet in items:
            print(f"    :{lineno}  {snippet}")
    print(
        "\n  Hot-path code (app/api + app/services) must import models at\n"
        "  module level — lazy import inside function bodies costs 120-180ms\n"
        "  per worker on first call (SQLAlchemy mapper compilation + relationship\n"
        "  resolution). See commit 49df540 for evidence (today_snapshot p95\n"
        "  209ms → 57ms after hoisting one such import).\n"
        "\n  To fix: move `from app.models.X import Y` to the top of the file.\n"
        "  If the lazy import resolves a circular dependency, annotate with:\n"
        "    from app.models.X import Y  # audit_lazy_model_imports: ok — circular import\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
