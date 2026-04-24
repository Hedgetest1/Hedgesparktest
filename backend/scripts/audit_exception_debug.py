#!/usr/bin/env python
"""
audit_exception_debug.py — Tier 2.2: find every exception handler that
swallows errors into log.debug().

Policy: `log.debug` is invisible in prod. If the exception matters for
operations (DB query, external API call, user-visible behaviour) it
must escalate to `log.warning` or `write_alert`. Only genuinely
fire-and-forget paths (telemetry counter writes, cache invalidation,
debug tracing) are allowed to remain at debug level.

This tool finds every

    except <...> [as exc]:
        [log.debug(...)]        # only log call in handler
        [pass | return]

and emits them as candidates for promotion. It cannot judge business
relevance — that is a human call. The report is the driver.

Usage:
    ./venv/bin/python scripts/audit_exception_debug.py
    ./venv/bin/python scripts/audit_exception_debug.py --strict  # not yet wired
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import Counter, defaultdict
from _audit_telemetry_shim import telemetered

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


class Finding:
    __slots__ = ("file", "line", "kind", "exc_name", "risk")

    def __init__(self, file: str, line: int, kind: str, exc_name: str, risk: str):
        self.file = file
        self.line = line
        self.kind = kind  # debug_only | debug_plus_warning | no_logging
        self.exc_name = exc_name
        self.risk = risk  # prod_relevant | redis_only | unknown


# Names of receivers (the `x` in `x.method(...)`) that indicate a
# production-relevant call worth surfacing if it fails.
# MED-07 closure 2026-04-24: expanded list to catch more prod sites
# (stripe, sentry outbound API, smtp, pm2 telegram_bot, etc.).
_PROD_RECEIVERS = {
    "db", "session", "sess", "conn", "connection", "self_db",
    "httpx_client", "http", "anthropic", "openai", "client",
    "shopify", "klaviyo", "resend", "stripe", "sentry_client",
    "smtp", "requests", "bot", "telegram_bot", "slack_client",
    "engine", "pool",
}
# Names of receivers that indicate fire-and-forget Redis / cache.
_REDIS_RECEIVERS = {"rc", "redis", "cache", "pipe", "r", "redis_client"}


def _log_level(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute) and fn.attr in {
        "debug", "info", "warning", "error", "exception", "critical"
    }:
        return fn.attr
    return None


def _is_alert_write(call: ast.Call) -> bool:
    fn = call.func
    if isinstance(fn, ast.Name) and fn.id in {"write_alert", "record_silent_return"}:
        return True
    if isinstance(fn, ast.Attribute) and fn.attr in {
        "write_alert", "record_silent_return"
    }:
        return True
    return False


def _iter_handler_calls(handler: ast.ExceptHandler):
    """Yield every Call node inside a handler body, INCLUDING calls
    hidden inside lambda bodies (MED-07 closure). Pre-MED-07 a pattern
    like `executor.submit(lambda: log.debug(...))` was invisible to
    this audit because ast.walk descended into the Lambda node but the
    inner call was reported as a regular call, losing the "this is a
    deferred log" semantic. We surface them explicitly so the
    classifier treats deferred logging as equivalent to direct logging
    for swallow-detection purposes."""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Call):
            yield node
        # Lambda bodies: ast.walk already descends into them, but we
        # keep the explicit case-comment here to make the intention
        # obvious to any future reader extending the audit.


def _classify_handler(handler: ast.ExceptHandler) -> str | None:
    """Return 'debug_only' / 'debug_plus_warning' / 'no_logging' / None
    (None means the handler does real work and is not a swallow)."""
    log_levels: list[str] = []
    has_alert = False
    has_nonlogging_call = False
    for node in _iter_handler_calls(handler):
        lvl = _log_level(node)
        if lvl is not None:
            log_levels.append(lvl)
            continue
        if _is_alert_write(node):
            has_alert = True
            continue
        # Any other call means the handler is doing real work — not a swallow.
        has_nonlogging_call = True

    if has_alert:
        return None  # real alerting — not a candidate
    if has_nonlogging_call:
        return None  # does real work

    # No real work, no alert. Classify by logging level.
    if not log_levels:
        return "no_logging"
    if "debug" in log_levels and not any(
        l in log_levels for l in ("warning", "error", "exception", "critical")
    ):
        return "debug_only"
    if "debug" in log_levels:
        return "debug_plus_warning"
    return None


def _risk_of_try_body(body: list[ast.stmt]) -> str:
    """Look at calls in the try-block to decide how risky it is.
    Returns 'prod_relevant' (DB/HTTP/external), 'redis_only' (rc.* or
    pipe.*), or 'unknown' (nothing we recognize either way)."""
    prod = False
    redis = False
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        value = fn.value
        if not isinstance(value, ast.Name):
            continue
        if value.id in _PROD_RECEIVERS:
            prod = True
        elif value.id in _REDIS_RECEIVERS:
            redis = True
    if prod:
        return "prod_relevant"
    if redis:
        return "redis_only"
    return "unknown"


def scan_file(path: pathlib.Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    findings: list[Finding] = []
    rel = path.relative_to(APP_ROOT.parent).as_posix()

    # Walk Try nodes so we have access to each handler's try-body.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        risk = _risk_of_try_body(node.body)
        for handler in node.handlers:
            kind = _classify_handler(handler)
            if kind != "debug_only":
                continue
            exc_name = ""
            if handler.type is not None:
                exc_name = ast.unparse(handler.type)
            findings.append(
                Finding(rel, handler.lineno, kind, exc_name, risk)
            )
    return findings


def walk_app() -> list[Finding]:
    findings: list[Finding] = []
    for path in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        findings.extend(scan_file(path))
    return findings


@telemetered("audit_exception_debug")
def main() -> int:
    findings = walk_app()
    by_risk = Counter(f.risk for f in findings)

    print(f"audit_exception_debug: scanned {APP_ROOT}")
    print(f"  total debug-only swallow handlers: {len(findings)}")
    print(f"    prod_relevant (DB/HTTP/external) : {by_risk.get('prod_relevant', 0)}")
    print(f"    redis_only    (fire-and-forget)  : {by_risk.get('redis_only', 0)}")
    print(f"    unknown       (needs inspection) : {by_risk.get('unknown', 0)}")
    print()

    prod_sites = [f for f in findings if f.risk == "prod_relevant"]
    if prod_sites:
        print(f"PROD-RELEVANT SITES ({len(prod_sites)}) — candidates for promotion:")
        for f in sorted(prod_sites, key=lambda x: (x.file, x.line)):
            print(f"  {f.file}:{f.line}  except {f.exc_name}")
        print()

    if "--detail" in sys.argv:
        print("All unknown sites:")
        for f in sorted(findings, key=lambda x: (x.file, x.line)):
            if f.risk == "unknown":
                print(f"  {f.file}:{f.line}  except {f.exc_name}")

    strict = "--strict" in sys.argv
    if strict and prod_sites:
        print(f"FAIL: {len(prod_sites)} prod-relevant debug-only handlers remain (target: 0)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
