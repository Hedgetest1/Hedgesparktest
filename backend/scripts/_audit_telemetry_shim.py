"""Shared best-effort telemetry sink for preflight audit scripts.

Two wiring styles, both fail-safe:

1. **Decorator (preferred for bulk wiring)** — wrap an audit's
   `main()` with `@telemetered("audit_foo")`. The decorator captures
   the exit code and emits a COARSE signal (findings=0/1, severity
   derived from rc). Zero further code change inside main().

2. **Inline precise emit** — call `emit("audit_foo", findings=len(x),
   severity="warn")` before returning. Use when the audit knows its
   exact findings count and you want precision in /ops/audit-telemetry.
   Inline emit is per-process idempotent: the decorator will NOT
   re-emit afterwards, preserving your precise values.

Any import error, redis error, or config error is swallowed. Audits
MUST keep working when the telemetry backend is unavailable (fresh
clone, redis restart, CI with no REDIS_URL, etc.).

Usage patterns:

    from _audit_telemetry_shim import telemetered
    @telemetered("audit_foo")
    def main(argv):
        ...
        return 0 if clean else 1

Or precise:

    from _audit_telemetry_shim import emit, telemetered
    @telemetered("audit_bundle_budget")
    def main(argv):
        ...
        if over:
            emit("audit_bundle_budget", findings=len(over), severity="warn")
            return 1
        return 0
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

# Prepend backend/ so `from app...` resolves when a script is invoked
# directly from anywhere.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# Per-process set of audit names already emitted. Lets inline `emit()`
# take precedence over the decorator's coarse fallback in the same run.
_EMITTED_THIS_PROCESS: set[str] = set()


def emit(audit_name: str, findings: int, severity: str = "info") -> bool:
    """Record one audit run in the telemetry Redis HASH.

    Per-process idempotent: a second call with the same `audit_name`
    within this process no-ops (returns True). Across processes the
    underlying `record_run` is idempotent-per-day by construction
    (increments `runs`, overwrites findings+severity with latest).

    Returns True on successful write or idempotent no-op, False on
    Redis/import failure. Never raises.
    """
    if audit_name in _EMITTED_THIS_PROCESS:
        return True
    _EMITTED_THIS_PROCESS.add(audit_name)
    try:
        from app.services.audit_telemetry import record_run
        return record_run(audit_name, findings, severity=severity)
    except Exception:
        return False


def telemetered(audit_name: str):
    """Decorator for an audit's `main()` — emits a coarse telemetry
    signal based on the returned exit code:

        rc == 0  →  findings=0, severity="info"
        rc == 1  →  findings=1, severity="warn"
        rc >= 2  →  findings=0, severity="critical" (script error)

    If `main()` already called `emit(audit_name, ...)` with precise
    findings, the decorator no-ops (per-process dedup). This way the
    decorator is a safe default for any audit, and inline calls win
    when precision matters.
    """
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            rc = fn(*args, **kwargs)
            if audit_name in _EMITTED_THIS_PROCESS:
                return rc
            if rc == 0:
                emit(audit_name, findings=0, severity="info")
            elif rc == 1:
                emit(audit_name, findings=1, severity="warn")
            else:
                emit(audit_name, findings=0, severity="critical")
            return rc
        return inner
    return wrap
