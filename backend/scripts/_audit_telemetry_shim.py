"""Shared best-effort telemetry sink for preflight audit scripts.

Every `audit_*.py` script that wants to participate in the
`/ops/audit-telemetry` rollup imports `emit(audit_name, findings,
severity)` from this shim and calls it right before exiting main().

The shim is fail-safe: any import error, redis error, or config
error is swallowed. An audit MUST keep working when the telemetry
backend is unavailable (fresh clone before pip install, redis
stopped for maintenance, CI with no REDIS_URL, etc.).

Usage pattern at the bottom of an audit's main():

    from _audit_telemetry_shim import emit
    emit("audit_bundle_budget", findings=len(over),
         severity="warn" if over else "info")
    return 1 if over else 0
"""
from __future__ import annotations

import sys
from pathlib import Path

# Prepend backend/ so `from app...` resolves when a script is invoked
# directly from anywhere.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def emit(audit_name: str, findings: int, severity: str = "info") -> bool:
    """Record one audit run in the telemetry Redis HASH.

    Returns True on successful write, False otherwise. Never raises.
    """
    try:
        from app.services.audit_telemetry import record_run
        return record_run(audit_name, findings, severity=severity)
    except Exception:
        return False
