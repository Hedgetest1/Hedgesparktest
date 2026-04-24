#!/usr/bin/env python3
"""audit_backend_frontend_coverage.py — enforce "every merchant-facing
endpoint has a real UI consumer OR an explicit `# ui-exempt` tag".

Problem class
-------------
HedgeSpark claim: "every feature end-to-end wired, no theater" (§2 rule
2). Over 14 days the founder caught TWO instances of the bug class:

  1. 2026-04-19  — Explore agent reported 53 "fantasma" backend
     endpoints with no UI consumer. Cross-check narrowed to 26 real
     fantasma (9 pure-code + 3 Ads + 14 utility/admin).
  2. 2026-04-25  — memory of that audit turned out to be 4 days stale.
     5/9 pure-code were shipped 2026-04-20 but 14 utility/admin + 3 Ads
     remained unsurfaced with zero alert.

This audit is the preventer proposed as Phase 4.1 of the v1.0 launch
roadmap. It is the structural close on the class: "endpoint ships
without a real consumer and nobody notices".

What counts as "merchant-facing"
--------------------------------
Whitelist of route prefixes that belong on the merchant dashboard:

  /pro/       — Pro-tier features
  /merchant/  — Merchant API (Lite + Pro)
  /analytics/ — Merchant analytics

Routes under other prefixes (`/webhooks/*`, `/ops/*`, `/auth/*`,
`/install/*`, `/public/*`, `/track`, `/system/*`, `/telegram*`, `/deploy*`,
`/docs`, `/openapi.json`, `/redoc`, `/billing/*`, …) are infrastructure,
operator, or frontend-less by design and are not scanned.

Exemption mechanism
-------------------
Some merchant-facing-prefixed routes are genuinely internal (e.g. a
`/pro/webhooks/deliveries` consumed by an external integration, not the
dashboard). Mark those with an inline comment on the `@router.xxx`
decorator line:

    @router.get("/pro/webhooks/deliveries")  # ui-exempt: external-consumer
    def handler(...): ...

The exemption MUST include a reason ("external-consumer", "internal-api",
"cli-only", etc.). Empty or missing reason → preventer blocks.

Consumer detection
------------------
A route is "consumed" if ANY `.ts`/`.tsx` file under `dashboard/src/`
(EXCLUDING the auto-generated `api-types.ts`) mentions:

  * The literal path (e.g. `"/pro/rars/summary"`)
  * The literal prefix up to the first `{param}` AND the literal suffix
    after the last `{param}` co-occurring in the same file (caller
    builds via template literal)

False-positive-aware: api-types.ts is the TypeScript types mirror of
the OpenAPI schema. Presence there proves the backend declares the
route — it does NOT prove the dashboard uses it.

Exit codes
----------
  0  survey mode (default) — prints report, never fails
     --strict mode — every merchant-facing route has consumer OR exempt
  1  --strict mode — one or more uncovered routes without exemption
  2  script error

Usage
-----
    ./audit_backend_frontend_coverage.py            # survey (exit 0)
    ./audit_backend_frontend_coverage.py --strict   # blocking
    ./audit_backend_frontend_coverage.py --json     # machine-readable
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

DASHBOARD_SRC = pathlib.Path("/opt/wishspark/dashboard/src")
BACKEND_API = pathlib.Path("/opt/wishspark/backend/app/api")

# Files that must NOT count as consumers even if they contain the path
# as a literal. api-types.ts is the auto-generated TypeScript mirror of
# the OpenAPI schema — presence there is a STATEMENT OF BACKEND
# CONTRACT, not evidence of UI usage.
_EXCLUDED_CONSUMERS = {"api-types.ts"}

# Only these prefixes are scanned. Everything else is infra / operator /
# callback / frontend-less by design. Keep narrow: better to miss a
# borderline case than false-positive-block infra routes.
_MERCHANT_PREFIXES = (
    "/pro/",
    "/merchant/",
    "/analytics/",
)

# HTTP methods we never audit.
_IGNORE_METHODS = {"HEAD", "OPTIONS"}

# Whitelist of valid `ui-exempt` reasons. Forces the tag to CATEGORIZE
# the intent rather than accept any junk string. New categories land
# here with a code-review deliberate decision; junk reasons ("x", "tbd",
# "idk") still get flagged as invalid. Reason count gives us a fire-
# rate ceiling — if one reason balloons, audit output surfaces it.
_VALID_EXEMPT_REASONS = frozenset({
    "external-consumer",       # called by Shopify / Klaviyo / etc., not dashboard
    "internal-api",            # consumed by other backend services only
    "cli-only",                # operator CLI / curl target
    "oauth-callback",          # OAuth redirect landing (browser → browser, no SPA)
    "webhook-receiver",        # inbound webhook (HMAC verified, caller is SaaS)
    "shopify-admin",           # Shopify Admin extension / Flow trigger
    "payload-trigger",         # manually-invoked trigger (refresh button, cron-kicked)
    "deprecated",              # sunsetting, intentionally no new UI
})

# Regex for `@<name>.<method>("<path>")` + optional same-line
# `# ui-exempt: <reason>` comment. We match ANY attribute decorator
# (not just `@router`) so files with multiple routers — `router` +
# `lite_router` in cohorts.py, or feature-scoped names — all get their
# decorators recognized. We then resolve the full path via the runtime
# FastAPI `app.routes` enumeration below, which has authoritative
# prefix data without needing to track variable names by hand.
_ROUTE_DECORATOR_RE = re.compile(
    # `@<var>.<method>(` followed by optional whitespace/newlines, then
    # the path literal. Multi-line decorators (`@router.get(\n "",\n
    # response_model=...,\n)`) are common — DOTALL lets `\s` match
    # newlines inside the arg list but we still anchor on `@<var>` at
    # start-of-line to avoid cross-decorator smearing.
    r"(?m)^\s*@(\w+)\.(get|post|put|patch|delete)\s*\(\s*"
    r"[\"']([^\"']*)[\"']"
    # Optional same-line ui-exempt comment — if the decorator spans
    # multiple lines, the comment (if any) is usually on the closing-
    # paren line; we look for it on the FIRST line with the path OR
    # on the SAME line as the closing paren. Simplest: scan for it in
    # the 150 chars after the path literal.
    r"(?:[^)]|\n)*?\)\s*(?:#\s*ui-exempt\s*:\s*([a-z][a-z0-9_-]*))?",
)


@dataclass
class RouteDecl:
    """One `@<var>.<method>("<path>")` declaration resolved to its full
    runtime path (with router prefix applied) via FastAPI."""
    file: str
    line: int
    method: str
    path: str              # full runtime path
    exempt_reason: str | None


def _parse_file_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Return {router_varname: prefix} for every
    `<var> = APIRouter(prefix="/x", ...)` assignment at module level.
    Missing `prefix=` or non-literal values → empty string."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "APIRouter"):
            continue
        # Resolve prefix kwarg (literal only)
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value.rstrip("/")
                break
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def _extract_decorator_index(api_dir: pathlib.Path) -> dict[tuple[str, str], dict]:
    """Return {(method, FULL_path): {file, line, exempt}}.

    Uses AST to bind each `@<var>.<method>("/x")` decorator to the
    router variable it references, then joins `<var>'s prefix + /x`
    to compute the FULL path. That resolves (method, full_path) to a
    unique (file, line) even when two files contain decorators with
    the same literal arg.
    """
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

        # Regex on the source so we capture the same-line ui-exempt
        # comment that AST drops. For each decorator match we look up
        # the router var via the regex group 1.
        for m in _ROUTE_DECORATOR_RE.finditer(text):
            var_name = m.group(1)
            method = m.group(2).upper()
            dec_path = m.group(3)
            exempt = m.group(4)
            if method in _IGNORE_METHODS:
                continue
            if var_name not in router_prefixes:
                # decorator on something that isn't a module-level
                # APIRouter var (e.g., a nested router) — skip
                continue
            prefix = router_prefixes[var_name]
            if dec_path == "/":
                full = prefix or "/"
            else:
                full = (prefix + dec_path) if prefix else dec_path
                if not full.startswith("/"):
                    full = "/" + full

            # m.start() may fall in the leading whitespace of the
            # decorator — advance to the actual `@` so the reported line
            # is the decorator's own line, not the blank above it.
            at_offset = text.find("@", m.start(), m.end())
            pos = at_offset if at_offset >= 0 else m.start()
            lineno = text[: pos].count("\n") + 1
            key = (method, full)
            if key not in out:
                out[key] = {
                    "file": str(py.relative_to(BACKEND_API.parent.parent)),
                    "line": lineno,
                    "exempt": exempt,
                }
    return out


def _extract_routes(api_dir: pathlib.Path) -> list[RouteDecl]:
    """Enumerate every merchant-facing route by asking FastAPI at
    runtime (authoritative source for prefix resolution), then attach
    file/line/exempt from the on-disk decorator scan via suffix match
    on the decorator path literal.
    """
    # Load backend app so its routes register
    from app.main import app

    dec_index = _extract_decorator_index(api_dir)

    out: list[RouteDecl] = []
    for r in app.routes:
        if not (hasattr(r, "path") and hasattr(r, "methods")):
            continue
        full_path = str(r.path)
        methods = [m for m in (r.methods or set()) if m not in _IGNORE_METHODS]
        for method in methods:
            file_hit = dec_index.get((method, full_path))
            if file_hit is None:
                # Route exists at runtime but we couldn't find a matching
                # decorator on disk — FastAPI dynamic route, app.include
                # _router with prefix kwarg not reflected in the module,
                # or a module-level prefix we couldn't parse.
                file_hit = {"file": "<runtime>", "line": 0, "exempt": None}

            out.append(RouteDecl(
                file=file_hit["file"],
                line=file_hit["line"],
                method=method,
                path=full_path,
                exempt_reason=file_hit["exempt"],
            ))
    return out


def _is_merchant_facing(path: str) -> bool:
    return any(path.startswith(p) for p in _MERCHANT_PREFIXES)


def _consumer_search_strings(path: str) -> list[str]:
    """Strings to look for in dashboard files to prove the route is
    consumed. Parameterized paths (`/pro/foo/{id}`) are hard to match
    literally since the caller builds via template literals — we return
    the longest literal prefix AND longest literal suffix so a consumer
    that concatenates them (in the SAME file) counts."""
    parts = re.split(r"/\{[^}]+\}", path)
    parts = [p for p in parts if p]
    if not parts:
        return [path]
    if len(parts) == 1:
        return [parts[0].rstrip("/") or path]
    return [parts[0].rstrip("/"), parts[-1].rstrip("/")]


def _has_consumer(path: str, files: list[pathlib.Path]) -> bool:
    # Literal-path fast path
    for f in files:
        try:
            txt = f.read_text(errors="ignore")
        except Exception:
            continue
        if path in txt:
            return True

    # Parameterized path: require all needles present in the SAME file
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


def _dashboard_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    if not DASHBOARD_SRC.is_dir():
        return out
    for p in DASHBOARD_SRC.rglob("*.ts"):
        if p.name in _EXCLUDED_CONSUMERS:
            continue
        if "node_modules" in p.parts:
            continue
        out.append(p)
    for p in DASHBOARD_SRC.rglob("*.tsx"):
        if p.name in _EXCLUDED_CONSUMERS:
            continue
        if "node_modules" in p.parts:
            continue
        out.append(p)
    return out


@telemetered("audit_backend_frontend_coverage")
def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    as_json = "--json" in argv

    if not BACKEND_API.is_dir():
        print(f"audit_backend_frontend_coverage: {BACKEND_API} not found",
              file=sys.stderr)
        return 2
    if not DASHBOARD_SRC.is_dir():
        print(f"audit_backend_frontend_coverage: {DASHBOARD_SRC} not found",
              file=sys.stderr)
        return 2

    all_routes = _extract_routes(BACKEND_API)
    merchant_routes = [r for r in all_routes if _is_merchant_facing(r.path)]
    dashboard_files = _dashboard_files()

    uncovered: list[RouteDecl] = []
    exempted: list[RouteDecl] = []
    invalid_exempt: list[RouteDecl] = []
    covered: list[RouteDecl] = []

    seen_paths: set[str] = set()
    for r in merchant_routes:
        if r.path in seen_paths:
            continue
        if r.exempt_reason:
            reason = r.exempt_reason.strip()
            if reason not in _VALID_EXEMPT_REASONS:
                invalid_exempt.append(r)
            else:
                exempted.append(r)
                seen_paths.add(r.path)
            continue
        if _has_consumer(r.path, dashboard_files):
            covered.append(r)
        else:
            uncovered.append(r)
        seen_paths.add(r.path)

    payload = {
        "merchant_routes_scanned": len(seen_paths),
        "covered": len(covered),
        "exempted": len(exempted),
        "invalid_exempt": len(invalid_exempt),
        "uncovered": len(uncovered),
        "prefixes": list(_MERCHANT_PREFIXES),
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
    else:
        _print_human(payload, strict=strict)

    if strict and (uncovered or invalid_exempt):
        return 1
    return 0


def _print_human(p: dict, *, strict: bool) -> None:
    print("# Backend → frontend coverage\n")
    print(f"Merchant-facing prefixes: {', '.join(p['prefixes'])}")
    print(f"Routes scanned:   **{p['merchant_routes_scanned']}**")
    print(f"Covered:          **{p['covered']}**")
    print(f"Exempted:         **{p['exempted']}**")
    print(f"Uncovered:        **{p['uncovered']}**")
    if p["invalid_exempt"]:
        print(f"Invalid exempts:  **{p['invalid_exempt']}**  (reason too short / missing)")
    print()

    if p["uncovered_list"]:
        print(f"## {len(p['uncovered_list'])} uncovered merchant-facing route(s)\n")
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
        print(f"## {len(p['invalid_exempt_list'])} invalid `ui-exempt` tag(s)\n")
        print(f"Valid reasons: {sorted(_VALID_EXEMPT_REASONS)}\n")
        for r in p["invalid_exempt_list"]:
            print(f"- `{r['method']} {r['path']}` — `{r['file']}:{r['line']}` — "
                  f"reason=`{r['reason']}` (not in allowlist)")
        print()

    if p["uncovered_list"] or p["invalid_exempt_list"]:
        print(
            "Fix: either (a) add a real UI consumer in dashboard/src/ that "
            "calls the endpoint via apiClient or fetch, OR (b) tag the "
            "`@router.xxx(...)` decorator with `# ui-exempt: <reason>` "
            "if the endpoint is genuinely internal/external-consumer.\n"
        )
        if strict:
            print("FAIL (--strict): coverage gate broken")
    else:
        print("OK: every merchant-facing endpoint has a consumer or exemption")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_backend_frontend_coverage: script error — {exc}",
              file=sys.stderr)
        sys.exit(2)
