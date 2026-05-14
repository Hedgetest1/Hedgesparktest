#!/usr/bin/env python3
"""audit_route_runtime_coverage.py — authoritative route coverage
via coverage.py (runtime signal).

Complements audit_endpoint_test_coverage.py (grep-based): this one
reads /tmp/cov.json (produced by `pytest --cov=app.api --cov-report=
json:/tmp/cov.json`) and checks whether ANY body line of each route
handler was executed during the test suite.

Why both?
  * Grep audit runs in preflight every commit (~1s, no test run).
  * Runtime audit runs on demand after a full test suite (~3min).
  * Grep says "some test file MENTIONS the path" — weak signal.
  * Runtime says "the handler body was ACTUALLY executed" — strong
    signal. Catches (a) test fixtures that mention paths without
    calling, (b) DELETE/PATCH routes with no test, (c) dead routes.

Empirical delta at birth (2026-04-25):
  grep-based uncovered:    191  (audit_endpoint_test_coverage)
  runtime-based uncovered: 239  (this script)

The 48-route delta is the "mentioned-but-not-hit" class — a real
blind spot the grep signal alone would miss.

Usage
-----
    # 1. Generate coverage.json (takes ~3min)
    ./venv/bin/python -m pytest tests/ --cov=app.api \\
        --cov-report=json:/tmp/cov.json -q --tb=no

    # 2. Run this audit
    ./venv/bin/python scripts/audit_route_runtime_coverage.py

Exits 0 when coverage.json missing (informational tool, not
preflight-blocking). When present, prints per-route coverage breakdown.

Flags
-----
    --cov-file PATH    override default /tmp/cov.json
    --strict           exits 1 if any handler had no body-line execution
    --json             machine-readable output
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, "/opt/wishspark/backend")

from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

BACKEND_ROOT = pathlib.Path("/opt/wishspark/backend")
BACKEND_API = BACKEND_ROOT / "app" / "api"
DEFAULT_COV_PATH = pathlib.Path("/tmp/cov.json")

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_IGNORE_METHODS = {"HEAD", "OPTIONS"}


@dataclass
class HandlerRecord:
    method: str
    path: str
    file: str             # relative to backend/
    file_abs: str         # absolute path — used as coverage-map key
    start_line: int
    end_line: int


def _parse_router_prefixes(tree: ast.Module) -> dict[str, str]:
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


def _collect_handlers() -> list[HandlerRecord]:
    out: list[HandlerRecord] = []
    for py in sorted(BACKEND_API.rglob("*.py")):
        text = safe_read_text(py)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        prefixes = _parse_router_prefixes(tree)
        if not prefixes:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                if not isinstance(fn, ast.Attribute) or not isinstance(fn.value, ast.Name):
                    continue
                if fn.attr not in _HTTP_METHODS:
                    continue
                if not dec.args:
                    continue
                first = dec.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                var_name = fn.value.id
                if var_name not in prefixes:
                    continue
                prefix = prefixes[var_name]
                dec_path = first.value
                full = (prefix + dec_path) if prefix and dec_path != "/" else (prefix or dec_path or "/")
                if not full.startswith("/"):
                    full = "/" + full
                method = fn.attr.upper()
                if method in _IGNORE_METHODS:
                    continue
                out.append(HandlerRecord(
                    method=method,
                    path=full,
                    file=str(py.relative_to(BACKEND_ROOT)),
                    file_abs=str(py),
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                ))
    return out


def _load_coverage(cov_path: pathlib.Path) -> dict[str, set[int]] | None:
    """Load coverage.json and return {absolute_path: {executed_line, ...}}.
    Returns None if the file is missing/unparseable."""
    if not cov_path.is_file():
        return None
    try:
        data = json.loads(cov_path.read_text())
    except Exception:
        return None
    out: dict[str, set[int]] = {}
    for rel, entry in data.get("files", {}).items():
        executed = set(entry.get("executed_lines", []))
        # Try resolving via candidate roots
        for candidate in ("/opt/wishspark/" + rel,
                          "/opt/wishspark/backend/" + rel,
                          str(BACKEND_ROOT / rel)):
            if pathlib.Path(candidate).is_file():
                out[candidate] = executed
                break
        else:
            out[rel] = executed
    return out


def _parse_args(argv: list[str]) -> dict:
    cov_file = DEFAULT_COV_PATH
    strict = False
    strict_body = False
    as_json = False
    min_body_lines = 1
    it = iter(argv)
    for a in it:
        if a == "--cov-file":
            cov_file = pathlib.Path(next(it))
        elif a == "--strict":
            strict = True
        elif a == "--strict-body":
            # Closes the "early-return dead code" edge case: require
            # ≥2 body lines executed so a handler with `return {}` on
            # the first body line followed by unreachable code doesn't
            # falsely count as covered. Born 2026-04-25 after founder
            # caught the silent defer in commit 5bab987.
            strict_body = True
            min_body_lines = 2
        elif a == "--min-body-lines":
            min_body_lines = int(next(it))
        elif a == "--json":
            as_json = True
    return {
        "cov_file": cov_file,
        "strict": strict,
        "strict_body": strict_body,
        "as_json": as_json,
        "min_body_lines": min_body_lines,
    }


@telemetered("audit_route_runtime_coverage")
def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    handlers = _collect_handlers()

    cov_map = _load_coverage(args["cov_file"])
    if cov_map is None:
        msg = (f"audit_route_runtime_coverage: {args['cov_file']} missing — "
               f"run `pytest --cov=app.api --cov-report=json:{args['cov_file']}` "
               "first. This audit is informational (exits 0) when no coverage "
               "data is available.")
        if args["as_json"]:
            print(json.dumps({
                "error": "no_coverage_data",
                "path": str(args["cov_file"]),
                "handlers_scanned": len(handlers),
            }))
        else:
            print(msg)
        return 0

    covered: list[HandlerRecord] = []
    uncovered: list[HandlerRecord] = []
    no_cov_data: list[HandlerRecord] = []

    min_body = args["min_body_lines"]
    partial_covered: list[HandlerRecord] = []
    for h in handlers:
        executed = cov_map.get(h.file_abs)
        if executed is None:
            no_cov_data.append(h)
            continue
        body_lines = set(range(h.start_line + 1, h.end_line + 1))
        hit = body_lines & executed
        if len(hit) >= min_body:
            covered.append(h)
        elif len(hit) >= 1:
            # Body was entered (e.g., first line return) but fewer than
            # `min_body` lines executed. Under --strict-body this is
            # classified as partial/suspicious; under default mode it
            # still counts as covered.
            if args["strict_body"]:
                partial_covered.append(h)
            else:
                covered.append(h)
        else:
            uncovered.append(h)

    payload = {
        "handlers_scanned": len(handlers),
        "runtime_covered": len(covered),
        "partial_covered": len(partial_covered),
        "runtime_uncovered": len(uncovered),
        "no_coverage_data": len(no_cov_data),
        "min_body_lines": min_body,
        "uncovered_list": [
            {"method": h.method, "path": h.path,
             "file": h.file, "line": h.start_line}
            for h in sorted(uncovered, key=lambda x: (x.path, x.method))
        ],
        "partial_list": [
            {"method": h.method, "path": h.path,
             "file": h.file, "line": h.start_line}
            for h in sorted(partial_covered, key=lambda x: (x.path, x.method))
        ],
    }

    gate_failed = args["strict"] and (uncovered or (args["strict_body"] and partial_covered))

    if args["as_json"]:
        print(json.dumps(payload, indent=2))
        return 1 if gate_failed else 0

    print("# Route runtime coverage\n")
    print(f"Handlers scanned:          **{len(handlers)}**")
    print(f"Runtime-covered:           **{len(covered)}**")
    if partial_covered:
        print(f"Partial (≥1 body line, <{min_body}):  **{len(partial_covered)}**")
    print(f"Runtime-uncovered:         **{len(uncovered)}**")
    if no_cov_data:
        print(f"Outside coverage scope:    **{len(no_cov_data)}**")
    print()

    if uncovered:
        print(f"## {len(uncovered)} handler(s) with no body-line execution\n")
        by_prefix: dict[str, list[HandlerRecord]] = defaultdict(list)
        for h in uncovered:
            parts = h.path.strip("/").split("/")
            pfx = "/" + "/".join(parts[:2]) if len(parts) >= 2 else "/" + parts[0]
            by_prefix[pfx].append(h)
        for pfx in sorted(by_prefix):
            group = by_prefix[pfx]
            print(f"### `{pfx}/...` — {len(group)} handler(s)\n")
            for h in group:
                print(f"- `{h.method} {h.path}` — `{h.file}:{h.start_line}`")
            print()

    return 1 if gate_failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_route_runtime_coverage: script error — {exc}",
              file=sys.stderr)
        sys.exit(2)
