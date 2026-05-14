#!/usr/bin/env python3
"""audit_read_replica_routing_drift.py — preventer for read-replica drift.

Doctrine: every pure-read GET endpoint in `app/api/*.py` should route
through `Depends(get_read_db)` so the day a Postgres read replica is
provisioned (DATABASE_READ_URL env var), the routing flips for free
without code changes. Endpoints that mutate (commit/INSERT/UPDATE/
DELETE/session.add) MUST stay on `get_db` (writes can't go to a
replica).

This audit walks every `app/api/*.py`, finds `@router.get(...)`
functions, and reports any that:
  - use `Depends(get_db)` AND
  - have no writes inside the function body AND
  - aren't on the explicit allowlist (admin/ops endpoints where
    routing offers no merchant-capacity benefit)

Soft enforcement: PRINT findings, exit 0 unless `--strict` is set.

Allowlist (functions that legitimately stay on primary even if
pure-read):
  - admin/ops endpoints (whole files: ops.py, auth_posture.py)
  - billing.py (sensitive — keep on primary for read-after-write
    consistency)
  - shopify_oauth.py / webhooks.py / auth.py / consent_banner.py
    (auth flow / webhooks / consent — short transactions, primary
    fine)

Annotation opt-out: add `# read-replica: stay-primary — <reason>` on
the `@router.get(...)` line OR the line above. Reasons must be
specific (e.g. "needs read-after-write consistency",
"admin endpoint, low traffic").
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from _audit_io import safe_read_text

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "app" / "api"

# Whole-file allowlist: admin/ops/auth/billing files where pure-read
# GETs are legitimately on primary.
ALLOWLIST_FILES = {
    "ops.py",                # admin operator endpoints, low traffic
    "auth_posture.py",       # auth surface, primary fine
    "billing.py",            # sensitive — read-after-write consistency
    "shopify_oauth.py",      # OAuth flow, primary fine
    "webhooks.py",           # webhook handlers
    "auth.py",               # session bootstrap
    "consent_banner.py",     # short consent flows
    "telegram_webhook.py",   # operator webhook
    "telegram_pipeline_dashboard.py",  # operator dashboard
    "internal_metrics.py",   # internal ops metrics
    "outbound_webhooks.py",  # operator webhook outbound
    "remote_config.py",      # short reads, primary fine
    "ops_apply_pending.py",  # operator
    "compliance_score.py",   # compliance read, primary fine (config)
    "webhook_health.py",     # operator
}

# Per-route annotation marker for opt-out.
OPT_OUT_MARKER = "read-replica: stay-primary"


def _has_writes(func_body: list[ast.stmt]) -> bool:
    """True if the function body invokes db.commit / .add / INSERT / UPDATE / DELETE,
    OR calls into an imported service function (which is opaque and may write)."""
    for node in ast.walk(ast.Module(body=func_body, type_ignores=[])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in {"commit", "add", "delete", "merge", "flush", "bulk_save_objects"}:
                recv = node.func.value
                if isinstance(recv, ast.Name) and recv.id in {"db", "session"}:
                    return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.upper()
            if "INSERT INTO" in v or "UPDATE " in v or "DELETE FROM" in v:
                return True
        # Conservative: an in-function `from app.services.X import f`
        # followed by a call to f(db, ...) is opaque — service may write.
        # Treat as "has writes" so audit defaults to safe.
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("app.services") or module.startswith("app.workers"):
                return True
    return False


def _uses_get_db(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if any param uses `Depends(get_db)`."""
    for arg in func.args.args + func.args.kwonlyargs:
        # Default values include calls like Depends(get_db)
        pass
    # Walk defaults
    for default in (func.args.defaults or []) + (func.args.kw_defaults or []):
        if default is None:
            continue
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name):
            if default.func.id == "Depends":
                if default.args and isinstance(default.args[0], ast.Name):
                    if default.args[0].id == "get_db":
                        return True
    return False


def _is_get_route(decorator: ast.AST) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    f = decorator.func
    if isinstance(f, ast.Attribute) and f.attr == "get":
        return True
    return False


def _has_optout(src_lines: list[str], decorator_line: int) -> bool:
    """Check decorator line + line above for opt-out marker."""
    for ln in (decorator_line - 1, decorator_line - 2):
        if 0 <= ln < len(src_lines):
            if OPT_OUT_MARKER in src_lines[ln]:
                return True
    return False


def audit_file(path: Path) -> list[tuple[int, str]]:
    src = safe_read_text(path)
    if src is None:
        return []
    src_lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not _is_get_route(dec):
                continue
            if not _uses_get_db(node):
                continue
            # Pure read?
            if _has_writes(node.body):
                continue
            # Opt-out marker?
            if _has_optout(src_lines, dec.lineno):
                continue
            findings.append((dec.lineno, node.name))

    return findings


def _apply_fix(path: Path, decorator_lines: list[int]) -> int:
    """Auto-switch flagged GET endpoints to Depends(get_read_db).

    Returns count of switches applied. Idempotent — re-running on a
    file that's already correct is a no-op.
    """
    import re

    src = safe_read_text(path)
    if src is None:
        return 0
    src_lines = src.split("\n")
    tree = ast.parse(src)

    edits_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = False
        for dec in node.decorator_list:
            if _is_get_route(dec) and dec.lineno in decorator_lines:
                match = True
                break
        if not match:
            continue
        for default in (node.args.defaults or []):
            if not (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
            ):
                continue
            if default.args and isinstance(default.args[0], ast.Name) and default.args[0].id == "get_db":
                idx = default.lineno - 1
                old = src_lines[idx]
                new = old.replace("Depends(get_db)", "Depends(get_read_db)")
                if old != new:
                    src_lines[idx] = new
                    edits_count += 1

    if edits_count == 0:
        return 0

    new_src = "\n".join(src_lines)
    # Ensure get_read_db imported. Check for explicit import name in
    # the database-import line; do NOT just check global string presence
    # (newly-inserted Depends(get_read_db) makes substring check unsafe).
    has_import = re.search(
        r"^from app\.core\.database import [^#\n]*\bget_read_db\b",
        new_src, flags=re.M,
    )
    if not has_import:
        if re.search(r"^from app\.core\.database import (.+)$", new_src, flags=re.M):
            new_src = re.sub(
                r"^(from app\.core\.database import )(.+)$",
                lambda m: f"{m.group(1)}{m.group(2).strip()}, get_read_db",
                new_src, count=1, flags=re.M,
            )
        else:
            # File imports get_db from elsewhere (e.g. app.core.deps).
            # Insert standalone import at the top imports block, after
            # the first `from app.` line.
            new_src = re.sub(
                r"^(from app\.[^\n]+\n)",
                r"\1from app.core.database import get_read_db\n",
                new_src, count=1, flags=re.M,
            )
    path.write_text(new_src)
    return edits_count


def main() -> int:
    strict = "--strict" in sys.argv
    fix_mode = "--fix" in sys.argv

    total_findings = 0
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for f in sorted(API_DIR.glob("*.py")):
        if f.name == "__init__.py" or f.name in ALLOWLIST_FILES:
            continue
        findings = audit_file(f)
        if findings:
            by_file[f] = findings
            total_findings += len(findings)

    if total_findings == 0:
        print("✅ no read-replica routing drift — all pure-read GET endpoints route via get_read_db.")
        return 0

    if fix_mode:
        total_fixed = 0
        for fpath, findings in by_file.items():
            decorator_lines = [line for line, _ in findings]
            fixed = _apply_fix(fpath, decorator_lines)
            if fixed:
                rel = fpath.relative_to(REPO_ROOT)
                print(f"  ✓ {rel}: switched {fixed} route(s) to get_read_db")
                total_fixed += fixed
        print(f"\nauto-fix applied: {total_fixed} route(s) switched.")
        # Re-run audit to confirm clean
        residual = 0
        for f in sorted(API_DIR.glob("*.py")):
            if f.name == "__init__.py" or f.name in ALLOWLIST_FILES:
                continue
            residual += len(audit_file(f))
        if residual == 0:
            print("✅ all drift resolved.")
            return 0
        print(f"⚠️  {residual} finding(s) remain after auto-fix — manual review needed.")
        return 1

    for fpath, findings in by_file.items():
        rel = fpath.relative_to(REPO_ROOT)
        for line, name in findings:
            print(
                f"  ⚠️  {rel}:{line} — GET {name}() uses Depends(get_db) "
                f"but appears pure-read"
            )

    print(
        f"\n{total_findings} pure-read GET endpoint(s) on primary instead of "
        f"read replica. Switch with: db: Session = Depends(get_read_db)\n"
        f"Or annotate `# {OPT_OUT_MARKER} — <reason>` on the @router.get line.\n"
        f"Auto-fix: re-run with `--fix` to switch all flagged routes."
    )
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
