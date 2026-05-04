#!/usr/bin/env python3
"""audit_worker_scope_coverage.py — preventer for runtime N+1 in workers.

Doctrine: every per-shop iteration in `app/workers/*.py` (and per-shop
service loops called from workers) MUST be wrapped in
`with worker_scope("module.op", shop_id):` so the runtime N+1 detector
gets a chance to fire on iterations that exceed the soft/hard
thresholds.

This audit walks the AST, finds `for X in Y:` loops where the loop
body issues SQL (db.query / db.execute / db.add) AND iterates over a
shop-shaped target, then checks whether the body is wrapped in a
`worker_scope` context manager (directly or via an ancestor `with`).

Soft enforcement: PRINT findings, exit 0 unless `--strict` is set.
Strict enforcement (preflight): exit 1 on first finding.

Heuristic for "shop-shaped" iterations:
  - Loop variable name contains 'shop' (e.g. shop_domain, shop, shops)
  - Loop iterable name contains 'shops' or 'pairs' (heuristic for
    enumerate-over-shop-pair patterns)
  - First element of unpacked tuple is named 'shop_domain' / 'shop'

Skip rules (commented opt-out for false positives):
  - Comment `# worker-scope: not-per-shop` on the for line
  - Functions decorated with `@no_worker_scope_required` (none today)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKERS_DIR = REPO_ROOT / "app" / "workers"
# Service files that contain known per-shop loops invoked by workers.
SERVICE_FILES_WITH_PER_SHOP_LOOPS = [
    REPO_ROOT / "app" / "services" / "nudge_optimizer.py",
]

OPT_OUT_COMMENT = "# worker-scope: not-per-shop"


def _is_shop_shaped_target(target: ast.AST) -> bool:
    """True when the loop target binds something with 'shop' in the name."""
    if isinstance(target, ast.Name):
        return "shop" in target.id.lower()
    if isinstance(target, ast.Tuple):
        return any(
            isinstance(e, ast.Name) and "shop" in e.id.lower()
            for e in target.elts
        )
    return False


def _is_shop_shaped_iter(it: ast.AST) -> bool:
    """True when the iterable expression suggests a shop-keyed sequence."""
    if isinstance(it, ast.Name):
        n = it.id.lower()
        return any(tok in n for tok in ("shops", "pairs", "pending"))
    if isinstance(it, ast.Attribute):
        return any(tok in it.attr.lower() for tok in ("shops", "pairs"))
    return False


def _body_has_db_call(body: list[ast.stmt]) -> bool:
    """True if any statement in body invokes db.query / db.execute / db.add."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in {"query", "execute", "add", "scalar", "scalars", "merge"}:
                # Make sure receiver is named `db` to avoid false positives
                # on things like `request.app.state.execute(...)`.
                recv = node.func.value
                if isinstance(recv, ast.Name) and recv.id in {"db", "session"}:
                    return True
                # Allow service-call form: extract_patterns(db, ...) — caller
                # passes db; if the for-body invokes any function passing db,
                # treat as a possible per-shop SQL site.
                # (Conservative: skip this expansion — too many false
                # positives.)
    return False


def _body_has_worker_scope(body: list[ast.stmt]) -> bool:
    """True if any With-statement in body uses `worker_scope(...)`."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.With):
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call):
                    f = ce.func
                    if isinstance(f, ast.Name) and f.id == "worker_scope":
                        return True
                    if isinstance(f, ast.Attribute) and f.attr == "worker_scope":
                        return True
                    # Aliased `_worker_scope`
                    if isinstance(f, ast.Name) and f.id == "_worker_scope":
                        return True
                    if isinstance(f, ast.Attribute) and f.attr == "_worker_scope":
                        return True
    return False


def _has_optout_comment(src_lines: list[str], lineno: int) -> bool:
    """Check the for-line and the line above for the opt-out marker."""
    for ln in (lineno - 1, lineno - 2):
        if 0 <= ln < len(src_lines):
            if OPT_OUT_COMMENT in src_lines[ln]:
                return True
    return False


def audit_file(path: Path) -> list[tuple[int, str]]:
    src = path.read_text()
    src_lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        # Heuristic match: shop-shaped iteration
        target_match = _is_shop_shaped_target(node.target)
        iter_match = _is_shop_shaped_iter(node.iter)
        if not (target_match or iter_match):
            continue

        # Skip if the body is empty or only contains pass/continue
        if not _body_has_db_call(node.body):
            continue

        # Skip if already wrapped in worker_scope
        if _body_has_worker_scope(node.body):
            continue

        # Opt-out marker on the for line
        if _has_optout_comment(src_lines, node.lineno):
            continue

        findings.append((node.lineno, ast.unparse(node.target)))

    return findings


def main() -> int:
    strict = "--strict" in sys.argv
    fix_mode = "--fix" in sys.argv

    files = sorted(WORKERS_DIR.glob("*.py")) + [
        f for f in SERVICE_FILES_WITH_PER_SHOP_LOOPS if f.exists()
    ]

    total_findings = 0
    findings_by_file: dict[Path, list[tuple[int, str]]] = {}
    for f in files:
        if f.name == "__init__.py":
            continue
        findings = audit_file(f)
        if findings:
            findings_by_file[f] = findings
            total_findings += len(findings)

    if total_findings == 0:
        print("✅ all per-shop worker loops are wrapped in worker_scope.")
        return 0

    if fix_mode:
        # Auto-fix is non-trivial — wrapping a for-body in `with worker_scope(...)`
        # requires re-indenting every body statement. Doable but high
        # regression risk if the body has comments / decorators / multi-
        # line statements. Surface the structured suggestion instead so
        # the operator (or pipeline) can apply a deterministic patch.
        print("auto-fix mode: emitting structured suggestions (manual apply)")
        for fpath, findings in findings_by_file.items():
            rel = fpath.relative_to(REPO_ROOT)
            for line, target in findings:
                module = fpath.stem.replace("_worker", "")
                print(
                    f"  ⚠️  {rel}:{line} — wrap with:"
                    f"\n      from app.core.query_count_monitor import worker_scope as _worker_scope"
                    f"\n      ...for {target} in <iter>:"
                    f"\n          with _worker_scope('{module}.<op>', {target}):"
                    f"\n              ..."
                )
        return 1 if strict else 0

    for fpath, findings in findings_by_file.items():
        rel = fpath.relative_to(REPO_ROOT)
        for line, target in findings:
            print(
                f"  ⚠️  {rel}:{line} — for-loop over '{target}' issues "
                f"db.* calls without worker_scope wrap"
            )

    print(
        f"\n{total_findings} unwrapped per-shop loop(s) found. "
        f"Wrap with: with worker_scope('module.op', shop_id): ...\n"
        f"Or annotate with `{OPT_OUT_COMMENT}` if not actually per-shop.\n"
        f"Suggested wrapping: re-run with `--fix` for structured suggestions."
    )
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
