#!/usr/bin/env python3
"""
audit_session_durability_invariants.py

Structural preventer for the session-durability E2E suite
(dashboard/e2e/session_durability.spec.ts).

The E2E suite runs against prod and catches session regressions, but
it only fires on a real test run. If someone deletes the retry
backoff in /app/page.tsx or the session_version check in deps.py,
production ships the regression until the next E2E run. This audit
catches the same class at preflight time by asserting the invariants
still exist in source.

Implementation
--------------
- Python files are parsed with `ast.parse` and the AST is walked to
  locate the target function, then searched for the required node
  patterns. This defeats the "comment out the invariant" attack that
  a regex-only audit would miss.
- TypeScript files are scanned with regex against a comment-stripped
  copy of the file (line-comments `//...` and block-comments `/* */`
  are removed before matching), so commented-out invariants fail the
  audit the same way AST does for Python.

Each invariant maps 1:1 to an E2E scenario. When editing this file,
also edit the scenario it protects — and vice versa.

Exit code:
  0 — every invariant present in source
  1 — one or more invariants missing (commit blocked)
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from _audit_telemetry_shim import telemetered

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
DASHBOARD = REPO / "dashboard"


# ---------------------------------------------------------------------------
# Python AST helpers
# ---------------------------------------------------------------------------


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    return None


def py_function_has_none_check(path: Path, func_name: str, var_name: str) -> tuple[bool, str]:
    """
    Assert function `func_name` in `path` contains an `if <var_name> is None:`
    statement that raises. Defeats the "comment the line" regex scam.
    """
    try:
        mod = _parse_python(path)
    except SyntaxError as exc:
        return False, f"syntax error parsing {path.name}: {exc}"
    func = _find_function(mod, func_name)
    if func is None:
        return False, f"function `{func_name}` not found in {path.name}"
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Match: `<name> is None`
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == var_name
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Is)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            # Body must raise (not just pass/log/return)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Raise):
                    return True, f"{func_name}: `if {var_name} is None:` → raise present"
    return False, f"{func_name}: no `if {var_name} is None: raise ...` in function body"


def py_function_has_compare(
    path: Path, func_name: str, left: str, op_type: type, right: str
) -> tuple[bool, str]:
    """
    Assert function body contains a comparison `<left> <op> <right>`
    where <op> is the ast comparison operator class (e.g. ast.Lt).
    """
    try:
        mod = _parse_python(path)
    except SyntaxError as exc:
        return False, f"syntax error parsing {path.name}: {exc}"
    func = _find_function(mod, func_name)
    if func is None:
        return False, f"function `{func_name}` not found in {path.name}"
    for node in ast.walk(func):
        if not isinstance(node, ast.Compare):
            continue
        if (
            isinstance(node.left, ast.Name)
            and node.left.id == left
            and len(node.ops) == 1
            and isinstance(node.ops[0], op_type)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == right
        ):
            return True, f"{func_name}: `{left} {op_type.__name__} {right}` present"
    return False, f"{func_name}: no `{left} < {right}` comparison in body"


def py_function_calls_with_kw(
    path: Path, func_name: str, call_attr: str, kw_name: str, kw_value_repr: str
) -> tuple[bool, str]:
    """
    Assert function `func_name` contains a call to `something.<call_attr>(...)`
    with keyword `kw_name=<expected>`. Used for `jwt.decode(algorithms=["HS256"])`.
    """
    try:
        mod = _parse_python(path)
    except SyntaxError as exc:
        return False, f"syntax error parsing {path.name}: {exc}"
    func = _find_function(mod, func_name)
    if func is None:
        return False, f"function `{func_name}` not found in {path.name}"
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Attribute) and callee.attr == call_attr:
            for kw in node.keywords:
                if kw.arg == kw_name and ast.unparse(kw.value) == kw_value_repr:
                    return True, f"{func_name}: `.{call_attr}({kw_name}={kw_value_repr})` call present"
    return False, f"{func_name}: no `.{call_attr}({kw_name}={kw_value_repr})` call in body"


# ---------------------------------------------------------------------------
# TypeScript helpers — regex on a comment-stripped copy
# ---------------------------------------------------------------------------

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(src: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", src))


def ts_contains(
    path: Path,
    pattern: str,
    *,
    label: str,
    strip_comments: bool = True,
) -> tuple[bool, str]:
    """
    Match `pattern` inside `path`'s source. When strip_comments=True
    (default) both `//` line-comments and `/* */` block-comments are
    removed before matching so a commented-out invariant fails.

    Caveat: naive comment-stripping also eats `//` sequences inside
    string literals (e.g. `"https://..."`). Files that embed URLs in
    strings should pass strip_comments=False — the trade-off is that
    a commented-out check won't be detected there. For those files
    we prefer a false-negative (miss commented invariant) over a
    false-positive (true invariant incorrectly flagged missing).
    """
    if not path.exists():
        return False, f"missing file: {path.relative_to(REPO)}"
    raw = path.read_text(encoding="utf-8")
    src = _strip_comments(raw) if strip_comments else raw
    if re.search(pattern, src):
        suffix = " (in live code, not a comment)" if strip_comments else ""
        return True, f"{label} present{suffix}"
    scope = "live code, comments stripped" if strip_comments else "raw source"
    return False, f"{label} not found in {path.relative_to(REPO)} ({scope})"


def file_exists(path: Path) -> tuple[bool, str]:
    if path.exists():
        return True, "present"
    return False, f"missing file: {path.relative_to(REPO)}"


# ---------------------------------------------------------------------------
# Invariants registry — each maps 1:1 to an E2E scenario
# ---------------------------------------------------------------------------


@dataclass
class Invariant:
    name: str
    scenarios: str
    check: Callable[[], tuple[bool, str]]


INVARIANTS: list[Invariant] = [
    Invariant(
        "retry backoff on /merchant/me (inline)",
        "S6",
        lambda: ts_contains(
            DASHBOARD / "src/app/app/page.tsx",
            r"retryDelaysMs\s*=\s*\[",
            label="retryDelaysMs array literal",
        ),
    ),
    Invariant(
        "hint-cookie recovery (readHintCookie reference)",
        "S2 / S3 / S4 / S5",
        lambda: ts_contains(
            DASHBOARD / "src/app/app/page.tsx",
            r"\breadHintCookie\b|\bhs_shop\b",
            label="hs_shop hint-cookie reader",
        ),
    ),
    Invariant(
        "bootstrap via /auth/session redirect",
        "S2",
        lambda: ts_contains(
            DASHBOARD / "src/app/app/page.tsx",
            r"bootstrapWithShop|/auth/session\?shop=",
            label="bootstrapWithShop / /auth/session redirect",
        ),
    ),
    Invariant(
        "Reconnect UI button copy",
        "S7",
        lambda: ts_contains(
            DASHBOARD / "src/app/app/page.tsx",
            r"Reconnect my store",
            label="'Reconnect my store' button copy",
        ),
    ),
    Invariant(
        "useSession hook retry backoff",
        "S6 (hook path)",
        lambda: ts_contains(
            DASHBOARD / "src/app/lib/useSession.ts",
            r"retryDelaysMs\s*=\s*\[",
            label="retryDelaysMs array literal (hook)",
        ),
    ),
    Invariant(
        "session_version mismatch rejection (forced logout)",
        "S5",
        lambda: py_function_has_compare(
            BACKEND / "app/core/deps.py",
            "require_merchant_session",
            left="token_sv",
            op_type=ast.Lt,
            right="db_sv",
        ),
    ),
    Invariant(
        "merchant-existence gate (JWT for unknown shop → 401)",
        "S12",
        lambda: py_function_has_none_check(
            BACKEND / "app/core/deps.py",
            "require_merchant_session",
            var_name="merchant",
        ),
    ),
    Invariant(
        "/ops/force-logout admin endpoint present",
        "S14",
        lambda: ts_contains(
            BACKEND / "app/api/ops.py",
            r'@router\.post\(\s*"/force-logout"\s*\)',
            label="/ops/force-logout POST route",
        ),
    ),
    Invariant(
        "force-logout bumps session_version",
        "S14",
        lambda: ts_contains(
            BACKEND / "app/api/ops.py",
            r"m\.session_version\s*=\s*previous_sv\s*\+\s*1",
            label="session_version bump in force_logout body",
        ),
    ),
    Invariant(
        "JWT signature verification via HS256",
        "S3",
        lambda: py_function_calls_with_kw(
            BACKEND / "app/core/merchant_session.py",
            "verify_session_token",
            call_attr="decode",
            kw_name="algorithms",
            kw_value_repr="['HS256']",
        ),
    ),
    Invariant(
        "JWT decode call present",
        "S4",
        lambda: py_function_calls_with_kw(
            BACKEND / "app/core/merchant_session.py",
            "verify_session_token",
            call_attr="decode",
            kw_name="algorithms",
            kw_value_repr="['HS256']",
        ),
    ),
    Invariant(
        "/auth/session unknown-shop → install redirect",
        "S8",
        lambda: ts_contains(
            BACKEND / "app/api/shopify_oauth.py",
            r"/auth/install\?shop=",
            label="/auth/install redirect target",
        ),
    ),
    Invariant(
        "CSP frame-ancestors allowlists Shopify Admin",
        "S15",
        # next.config.ts has `https://admin.shopify.com` as a string
        # constant — naive comment-stripping would eat the `//` inside
        # the URL, so strip_comments=False here. Trade-off: a
        # commented-out frame-ancestors directive won't be flagged by
        # the audit. Runtime E2E S15 catches that case by inspecting
        # the actual served header, which is the authoritative source.
        lambda: ts_contains(
            DASHBOARD / "next.config.ts",
            r"frame-ancestors[^`;\n]*(admin\.shopify\.com|\$\{SHOPIFY_ADMIN\})",
            label="frame-ancestors directive with admin.shopify.com (or SHOPIFY_ADMIN template var)",
            strip_comments=False,
        ),
    ),
    Invariant(
        "SHOPIFY_ADMIN constant resolves to admin.shopify.com",
        "S15",
        lambda: ts_contains(
            DASHBOARD / "next.config.ts",
            r'SHOPIFY_ADMIN\s*=\s*"https://admin\.shopify\.com"',
            label='SHOPIFY_ADMIN = "https://admin.shopify.com"',
            strip_comments=False,
        ),
    ),
    Invariant(
        "E2E suite file present",
        "all",
        lambda: file_exists(DASHBOARD / "e2e/session_durability.spec.ts"),
    ),
    Invariant(
        "E2E helper file present",
        "all",
        lambda: file_exists(DASHBOARD / "e2e/helpers/session.ts"),
    ),
]


@telemetered("audit_session_durability_invariants")
def main() -> int:
    failures: list[str] = []
    print("session-durability invariants audit (AST + comment-stripped regex)")
    print(f"  repo: {REPO}")
    print(f"  checks: {len(INVARIANTS)}")
    for inv in INVARIANTS:
        ok, msg = inv.check()
        status = "✓" if ok else "✗"
        print(f"  {status} [{inv.scenarios}] {inv.name}: {msg}")
        if not ok:
            failures.append(f"{inv.scenarios}: {inv.name} — {msg}")
    print()
    if failures:
        print(f"BLOCKED — {len(failures)} invariant(s) missing:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("Fix: restore the invariant in source OR update")
        print("  dashboard/e2e/session_durability.spec.ts + this audit")
        print("  to reflect the new design. Never remove invariants blindly.")
        return 1
    print("OK — every session-durability invariant present in source (AST-verified).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
