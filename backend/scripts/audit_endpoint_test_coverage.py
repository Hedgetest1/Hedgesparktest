#!/usr/bin/env python3
"""audit_endpoint_test_coverage.py — enforce "every HTTP route has at
least one test that mentions its path, OR an explicit # test-exempt tag".

Problem class
-------------
2026-04-25 empirical survey: 242 of 395 registered routes had NO literal
path reference in any test file. The founder had a stale memo claiming
"~20" — reality was an order of magnitude worse. Without a preventer,
every future endpoint risks slipping into "no test ever" without signal.

This audit surfaces the gap (warn-only) so the trend becomes visible
via /ops/audit-telemetry, then flips to `--strict` once the gap is
closed.

Coverage detection
------------------
A route is "covered" if ANY `.py` file under `backend/tests/` mentions:

  * The literal template path (e.g. `/pro/rars/summary`)
  * The literal prefix up to the first `{param}` AND the literal suffix
    after the last `{param}` co-occurring in the same file (test uses
    a concrete path like `/pro/goals/revenue` that shares prefix +
    suffix with `/pro/goals/{metric}`)

Exemption mechanism
-------------------
`# test-exempt: <reason>` comment on the `@router.<method>(...)`
decorator line. Reason MUST be in the allowlist:

  framework-auto    — FastAPI framework routes (/, /docs, /openapi.json, etc.)
  oauth-callback    — browser-redirect landing page, no unit-test shape
  webhook-receiver  — inbound webhook tested via HMAC pattern elsewhere
  sse-stream        — Server-Sent Events, streaming contract
  deprecated        — sunsetting, no new coverage

Infra routes under certain prefixes get an implicit framework-auto
allow (the allowlist is encoded in `_IMPLICIT_FRAMEWORK_PREFIXES`) so
we don't force a tag on every FastAPI auto-mounted path.

Exit codes
----------
  0  survey mode (default) — always exits 0
     --strict mode — every route covered OR tagged with valid reason
  1  --strict mode — any uncovered-without-exempt OR invalid reason
  2  script error

Usage
-----
    ./audit_endpoint_test_coverage.py              # summary
    ./audit_endpoint_test_coverage.py --details    # per-route list
    ./audit_endpoint_test_coverage.py --strict     # blocking
    ./audit_endpoint_test_coverage.py --json       # machine-readable
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, "/opt/wishspark/backend")

from _audit_telemetry_shim import telemetered

BACKEND_ROOT = pathlib.Path("/opt/wishspark/backend")
BACKEND_API = BACKEND_ROOT / "app" / "api"
TESTS_DIR = BACKEND_ROOT / "tests"

# FastAPI auto-registered routes and developer surfaces don't need
# per-route tests. These prefixes get an implicit framework-auto allow
# so the allowlist tag isn't noise on every file.
_IMPLICIT_FRAMEWORK_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi",
)
_IMPLICIT_FRAMEWORK_EXACT = {"/", "/favicon.ico"}

_IGNORE_METHODS = {"HEAD", "OPTIONS"}

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# `# test-exempt: <reason>` parser. Only valid-reason tags count.
_TEST_EXEMPT_RE = re.compile(r"#\s*test-exempt\s*:\s*([a-z][a-z0-9_-]*)")
_VALID_TEST_EXEMPT_REASONS = frozenset({
    "framework-auto",
    "oauth-callback",
    "webhook-receiver",
    "sse-stream",
    "deprecated",
})


@dataclass
class RouteDecl:
    """An HTTP route, resolved to its full runtime path, with file/line
    and an optional test-exempt tag."""
    file: str
    line: int
    method: str
    path: str
    exempt_reason: str | None


def _parse_file_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Return {router_varname: prefix} for every APIRouter(prefix=...)
    assignment at module level. Copy of the same helper in
    audit_backend_frontend_coverage — kept inline here so this script
    stands alone for preflight."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "APIRouter"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value.rstrip("/")
                break
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def _decorator_call_parts(dec: ast.expr) -> tuple[str, str, str] | None:
    if not isinstance(dec, ast.Call):
        return None
    fn = dec.func
    if not isinstance(fn, ast.Attribute):
        return None
    if not isinstance(fn.value, ast.Name):
        return None
    method = fn.attr
    if method not in _HTTP_METHODS:
        return None
    if not dec.args:
        return None
    first_arg = dec.args[0]
    if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
        return None
    return (fn.value.id, method, first_arg.value)


def _exempt_reason_for_decorator(
    text_lines: list[str], dec: ast.expr
) -> str | None:
    """Search for `# test-exempt: <reason>` on any line covered by the
    decorator's source span."""
    start = dec.lineno
    end = getattr(dec, "end_lineno", dec.lineno) or dec.lineno
    for ln in range(start, end + 1):
        idx = ln - 1
        if 0 <= idx < len(text_lines):
            m = _TEST_EXEMPT_RE.search(text_lines[idx])
            if m:
                return m.group(1)
    return None


def _extract_decorator_index(api_dir: pathlib.Path) -> dict[tuple[str, str], dict]:
    """{(method, FULL_path): {file, line, exempt}}."""
    out: dict[tuple[str, str], dict] = {}
    for py in sorted(api_dir.rglob("*.py")):
        try:
            text = py.read_text()
            tree = ast.parse(text, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        router_prefixes = _parse_file_router_prefixes(tree)
        if not router_prefixes:
            continue
        text_lines = text.splitlines()
        rel_file = str(py.relative_to(BACKEND_ROOT))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                parts = _decorator_call_parts(dec)
                if parts is None:
                    continue
                var_name, method_lc, dec_path = parts
                method = method_lc.upper()
                if method in _IGNORE_METHODS:
                    continue
                if var_name not in router_prefixes:
                    continue
                prefix = router_prefixes[var_name]
                if dec_path == "/":
                    full = prefix or "/"
                else:
                    full = (prefix + dec_path) if prefix else dec_path
                    if not full.startswith("/"):
                        full = "/" + full
                exempt = _exempt_reason_for_decorator(text_lines, dec)
                key = (method, full)
                if key not in out:
                    out[key] = {
                        "file": rel_file,
                        "line": dec.lineno,
                        "exempt": exempt,
                    }
    return out


def _extract_routes() -> list[RouteDecl]:
    """Runtime-enumerate routes from FastAPI, attaching file/line from
    the AST decorator index."""
    from app.main import app

    dec_index = _extract_decorator_index(BACKEND_API)

    out: list[RouteDecl] = []
    for r in app.routes:
        if not (hasattr(r, "path") and hasattr(r, "methods")):
            continue
        full_path = str(r.path)
        methods = [m for m in (r.methods or set()) if m not in _IGNORE_METHODS]
        for method in methods:
            hit = dec_index.get((method, full_path))
            if hit is None:
                hit = {"file": "<runtime>", "line": 0, "exempt": None}
            out.append(RouteDecl(
                file=hit["file"],
                line=hit["line"],
                method=method,
                path=full_path,
                exempt_reason=hit["exempt"],
            ))
    return out


def _is_implicit_framework(path: str) -> bool:
    if path in _IMPLICIT_FRAMEWORK_EXACT:
        return True
    return any(path.startswith(p) for p in _IMPLICIT_FRAMEWORK_PREFIXES)


def _consumer_search_strings(path: str) -> list[str]:
    parts = re.split(r"/\{[^}]+\}", path)
    parts = [p for p in parts if p]
    if not parts:
        return [path]
    if len(parts) == 1:
        return [parts[0].rstrip("/") or path]
    return [parts[0].rstrip("/"), parts[-1].rstrip("/")]


def _test_files() -> list[pathlib.Path]:
    if not TESTS_DIR.is_dir():
        return []
    return [p for p in TESTS_DIR.rglob("test_*.py") if p.is_file()]


def _has_test_reference(path: str, files: list[pathlib.Path]) -> bool:
    for f in files:
        try:
            txt = f.read_text(errors="ignore")
        except Exception:
            continue
        if path in txt:
            return True
    needles = _consumer_search_strings(path)
    if not needles:
        return False
    for f in files:
        try:
            txt = f.read_text(errors="ignore")
        except Exception:
            continue
        if all(n in txt for n in needles if n):
            return True
    return False


@telemetered("audit_endpoint_test_coverage")
def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    details = "--details" in argv
    as_json = "--json" in argv

    if not BACKEND_API.is_dir():
        print(f"audit_endpoint_test_coverage: {BACKEND_API} not found",
              file=sys.stderr)
        return 2
    if not TESTS_DIR.is_dir():
        print(f"audit_endpoint_test_coverage: {TESTS_DIR} not found",
              file=sys.stderr)
        return 2

    routes = _extract_routes()
    test_files = _test_files()

    # De-duplicate by path: a route served under multiple methods only
    # needs one test reference to count as covered.
    seen: set[str] = set()
    unique_routes: list[RouteDecl] = []
    for r in routes:
        if r.path in seen:
            continue
        seen.add(r.path)
        unique_routes.append(r)

    implicit_ok: list[RouteDecl] = []
    exempted: list[RouteDecl] = []
    invalid_exempt: list[RouteDecl] = []
    covered: list[RouteDecl] = []
    uncovered: list[RouteDecl] = []

    for r in unique_routes:
        if _is_implicit_framework(r.path):
            implicit_ok.append(r)
            continue
        if r.exempt_reason:
            if r.exempt_reason not in _VALID_TEST_EXEMPT_REASONS:
                invalid_exempt.append(r)
            else:
                exempted.append(r)
            continue
        if _has_test_reference(r.path, test_files):
            covered.append(r)
        else:
            uncovered.append(r)

    payload = {
        "total_routes": len(unique_routes),
        "implicit_framework": len(implicit_ok),
        "covered": len(covered),
        "exempted": len(exempted),
        "invalid_exempt": len(invalid_exempt),
        "uncovered": len(uncovered),
        "valid_reasons": sorted(_VALID_TEST_EXEMPT_REASONS),
        "uncovered_list": [
            {"method": r.method, "path": r.path, "file": r.file, "line": r.line}
            for r in sorted(uncovered, key=lambda x: (x.path, x.method))
        ],
        "invalid_exempt_list": [
            {"method": r.method, "path": r.path, "file": r.file, "line": r.line,
             "reason": r.exempt_reason or ""}
            for r in invalid_exempt
        ],
    }

    if as_json:
        print(json.dumps(payload, indent=2))
        return 1 if strict and (uncovered or invalid_exempt) else 0

    _print_human(payload, details=details, strict=strict)
    return 1 if strict and (uncovered or invalid_exempt) else 0


def _print_human(p: dict, *, details: bool, strict: bool) -> None:
    print("# Endpoint test coverage\n")
    print(f"Total distinct routes:  **{p['total_routes']}**")
    print(f"Framework auto (skip):  **{p['implicit_framework']}**")
    print(f"Covered:                **{p['covered']}**")
    print(f"Exempted:               **{p['exempted']}**")
    if p["invalid_exempt"]:
        print(f"Invalid exempts:        **{p['invalid_exempt']}**  "
              f"(reason not in allowlist)")
    print(f"Uncovered:              **{p['uncovered']}**")
    print()

    if details and p["uncovered_list"]:
        print(f"## {len(p['uncovered_list'])} uncovered route(s)\n")
        by_prefix: dict[str, list[dict]] = defaultdict(list)
        for r in p["uncovered_list"]:
            parts = r["path"].strip("/").split("/")
            pfx = "/" + "/".join(parts[:2]) if len(parts) >= 2 else "/" + parts[0]
            by_prefix[pfx].append(r)
        for pfx in sorted(by_prefix):
            print(f"### `{pfx}/...` — {len(by_prefix[pfx])} route(s)\n")
            for r in by_prefix[pfx]:
                print(f"- `{r['method']} {r['path']}` — `{r['file']}:{r['line']}`")
            print()

    if p["invalid_exempt_list"]:
        print(f"## {len(p['invalid_exempt_list'])} invalid `test-exempt` tag(s)\n")
        print(f"Valid reasons: {p['valid_reasons']}\n")
        for r in p["invalid_exempt_list"]:
            print(f"- `{r['method']} {r['path']}` — `{r['file']}:{r['line']}` — "
                  f"reason=`{r['reason']}` (not in allowlist)")
        print()

    if p["uncovered_list"] or p["invalid_exempt_list"]:
        print(
            "Fix: either (a) write a test under backend/tests/ that mentions "
            "the literal path (or its prefix+suffix for parameterized paths), "
            "OR (b) tag the `@router.xxx(...)` decorator with "
            "`# test-exempt: <reason>` where reason is in the allowlist."
        )
        print()
        if strict:
            print("FAIL (--strict): coverage gate broken")
    else:
        print("OK: every non-framework route has a test reference or "
              "valid exemption")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_endpoint_test_coverage: script error — {exc}",
              file=sys.stderr)
        sys.exit(2)
