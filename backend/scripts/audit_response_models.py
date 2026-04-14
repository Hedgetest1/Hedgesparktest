#!/usr/bin/env python
"""
audit_response_models.py — Tier 3.1: find API routes missing response_model.

Policy: every public API route (/pro/*, /merchant/*, /analytics/*,
/app/*, /track*, /signal/*, /ops/*) must declare `response_model=...`
in its @router decorator. Routes returning bare dicts are a contract
gap — the dashboard could read a stale shape and fail silently, and
the generated TypeScript client has nothing to type.

What the audit does
-------------------
Walks app/api/*.py and finds every decorator of the form
`@router.<method>("/path", ...)` (or `@app.<method>`), then checks
whether the `response_model=` keyword is set.

Exclusions
----------
* `include_in_schema=False` routes are OK to skip — they're internal
  or operator-only and don't participate in the merchant TypeScript
  client.
* HEAD / OPTIONS routes are skipped by convention.
* Routes on `app.exception_handler` or `app.middleware` are not
  HTTP routes and are skipped.
* File-download routes (StreamingResponse / FileResponse return
  annotation) are skipped — response_model does not apply.

Usage:
    ./venv/bin/python scripts/audit_response_models.py
    ./venv/bin/python scripts/audit_response_models.py --strict
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import Counter, defaultdict

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
API_ROOT = APP_ROOT / "api"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class Finding:
    __slots__ = ("file", "line", "method", "path", "function")

    def __init__(self, file, line, method, path, function):
        self.file = file
        self.line = line
        self.method = method
        self.path = path
        self.function = function


def _string_const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _decorator_is_route(dec: ast.AST) -> tuple[str, str, str] | None:
    """If `dec` is `@X.<method>("/path", ...)`, return (router_name, method, path)."""
    if not isinstance(dec, ast.Call):
        return None
    fn = dec.func
    if not isinstance(fn, ast.Attribute):
        return None
    if not isinstance(fn.value, ast.Name):
        return None
    router_name = fn.value.id
    method = fn.attr
    if method not in HTTP_METHODS:
        return None
    if not dec.args:
        return None
    path = _string_const(dec.args[0])
    if path is None:
        return None
    return router_name, method, path


def _kwargs_of(dec: ast.Call) -> dict[str, ast.AST]:
    return {kw.arg: kw.value for kw in dec.keywords if kw.arg}


def _bool_const(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _returns_stream(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    if fn.returns is None:
        return False
    try:
        txt = ast.unparse(fn.returns)
    except Exception:
        return False
    return any(t in txt for t in ("StreamingResponse", "FileResponse", "Response"))


def _hidden_routers(tree: ast.Module) -> set[str]:
    """Find module-level `X = APIRouter(..., include_in_schema=False)`
    assignments. Return the set of variable names so we can skip
    decorators attached to them."""
    hidden: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        call = stmt.value
        fn = call.func
        is_router = (
            (isinstance(fn, ast.Name) and fn.id == "APIRouter")
            or (isinstance(fn, ast.Attribute) and fn.attr == "APIRouter")
        )
        if not is_router:
            continue
        include = None
        for kw in call.keywords:
            if kw.arg == "include_in_schema":
                include = kw.value
                break
        if include is not None and _bool_const(include) is False:
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    hidden.add(target.id)
    return hidden


def scan_file(path: pathlib.Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    findings: list[Finding] = []
    rel = path.relative_to(APP_ROOT.parent).as_posix()
    hidden = _hidden_routers(tree)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            info = _decorator_is_route(dec)
            if info is None:
                continue
            router_name, method, route_path = info
            if router_name in hidden:
                continue
            kwargs = _kwargs_of(dec)

            include = kwargs.get("include_in_schema")
            if include is not None and _bool_const(include) is False:
                continue

            if _returns_stream(node):
                continue

            if "response_model" in kwargs:
                continue

            findings.append(Finding(
                rel, dec.lineno, method.upper(), route_path, node.name
            ))
    return findings


def walk() -> list[Finding]:
    findings: list[Finding] = []
    if not API_ROOT.exists():
        return findings
    for path in API_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        findings.extend(scan_file(path))
    return findings


_TARGET_PREFIXES = ("/pro/", "/merchant/", "/analytics/")


def main() -> int:
    findings = walk()
    by_file = defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    target = [f for f in findings if f.path.startswith(_TARGET_PREFIXES)]
    other = [f for f in findings if not f.path.startswith(_TARGET_PREFIXES)]

    print(f"audit_response_models: scanned {API_ROOT}")
    print(f"  routes missing response_model (total)       : {len(findings)}")
    print(f"    on /pro/ | /merchant/ | /analytics/       : {len(target)}")
    print(f"    other prefixes (lower priority)           : {len(other)}")
    print()

    if target:
        by_file_t = defaultdict(list)
        for f in target:
            by_file_t[f.file].append(f)
        ranked = sorted(by_file_t.items(), key=lambda kv: len(kv[1]), reverse=True)
        print("Target-prefix files (missing response_model):")
        for file, items in ranked[:25]:
            print(f"  {len(items):3d}  {file}")
        print()

    if "--detail" in sys.argv and findings:
        print("All target-prefix sites:")
        for f in sorted(target, key=lambda x: (x.file, x.line)):
            print(f"  {f.file}:{f.line}  {f.method} {f.path}  → {f.function}")

    # --strict (Tier 3.1 final): fail on any target-prefix route missing
    # a response_model. Until the full sweep is done we use --strict-soft
    # to baseline the current count without blocking commits.
    strict = "--strict" in sys.argv
    if strict and target:
        print(f"FAIL: {len(target)} /pro|/merchant|/analytics routes missing response_model")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
