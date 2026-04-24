#!/usr/bin/env python
"""
audit_sentry_invariants.py — Pin the Sentry hardening contract.

Verifies the invariants shipped 2026-04-24 (C1..C4 sweep) are intact on
the live source tree:

  1. app/core/sentry_init.py exists and exports init_sentry, cron_monitor,
     sentry_span. (centralized init contract)
  2. app/main.py calls init_sentry(component="backend"). (backend coverage)
  3. All 6 PM2 workers call init_sentry(component=<their_name>) AND
     decorate run_cycle with @cron_monitor. (worker coverage + crons)
  4. sentry_init.py wires send_default_pii=False AND a before_send
     PII scrub callback. (PII contract)
  5. cron_monitor() implementation gates on SENTRY_CRON_MONITORING
     allowlist. (Team-plan quota gate — prevents the 2026-04-24
     accident where 6 workers saturated the 1-monitor base quota.)
  6. dashboard/ has sentry.{client,server,edge}.config.ts +
     instrumentation.ts. (frontend coverage)
  7. dashboard/next.config.ts wraps the export with withSentryConfig.
     (source-map upload + tunnel route)
  8. dashboard/next.config.ts CSP includes the EU Sentry ingest origin.
     (browser SDK can post events)

Exit codes:
  * 0 — all invariants intact
  * 1 — at least one violation (preflight blocks commit)

Flags: --strict (alias, accepted but always strict — there's no
"informational" mode for this audit).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DASHBOARD = ROOT / "dashboard"


WORKER_FILES = {
    "agent_worker": BACKEND / "app/workers/agent_worker.py",
    "intelligence_worker": BACKEND / "app/workers/intelligence_worker.py",
    "aggregation_worker": BACKEND / "app/workers/aggregation_worker.py",
    "segment_monitor_worker": BACKEND / "app/workers/segment_monitor_worker.py",
    "nudge_optimization_worker": BACKEND / "app/workers/nudge_optimization_worker.py",
    "gdpr_worker": BACKEND / "app/workers/gdpr_worker.py",
}


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except Exception:
        return None


def _check_sentry_init_module(failures: list[str]) -> None:
    p = BACKEND / "app/core/sentry_init.py"
    src = _read(p)
    if src is None:
        failures.append(f"missing: {p.relative_to(ROOT)}")
        return
    if "def init_sentry" not in src:
        failures.append(f"{p.relative_to(ROOT)}: init_sentry() not exported")
    if "def cron_monitor" not in src:
        failures.append(f"{p.relative_to(ROOT)}: cron_monitor() not exported")
    if "def sentry_span" not in src:
        failures.append(f"{p.relative_to(ROOT)}: sentry_span() not exported")
    if "send_default_pii=False" not in src:
        failures.append(f"{p.relative_to(ROOT)}: send_default_pii=False missing — PII contract broken")
    if "before_send=" not in src:
        failures.append(f"{p.relative_to(ROOT)}: before_send= callback not wired in init")
    if "SENTRY_CRON_MONITORING" not in src:
        failures.append(
            f"{p.relative_to(ROOT)}: SENTRY_CRON_MONITORING quota-gate missing — "
            "every worker would saturate Team-plan base 1-monitor quota"
        )


def _check_backend_main(failures: list[str]) -> None:
    p = BACKEND / "app/main.py"
    src = _read(p)
    if src is None:
        failures.append(f"missing: {p.relative_to(ROOT)}")
        return
    if 'init_sentry(component="backend")' not in src:
        failures.append(f"{p.relative_to(ROOT)}: init_sentry(component=\"backend\") not called")


def _check_workers(failures: list[str]) -> None:
    for name, path in WORKER_FILES.items():
        src = _read(path)
        if src is None:
            failures.append(f"missing worker: {path.relative_to(ROOT)}")
            continue
        if f'init_sentry(component="{name}")' not in src:
            failures.append(
                f"{path.relative_to(ROOT)}: init_sentry(component=\"{name}\") not called — worker blind to Sentry"
            )
        if "@cron_monitor(" not in src:
            failures.append(
                f"{path.relative_to(ROOT)}: @cron_monitor(...) decorator missing on run_cycle"
            )


def _check_dashboard(failures: list[str]) -> None:
    required = [
        DASHBOARD / "sentry.client.config.ts",
        DASHBOARD / "sentry.server.config.ts",
        DASHBOARD / "sentry.edge.config.ts",
        DASHBOARD / "instrumentation.ts",
    ]
    for p in required:
        if not p.is_file():
            failures.append(f"missing: {p.relative_to(ROOT)}")

    nc = DASHBOARD / "next.config.ts"
    src = _read(nc)
    if src is None:
        failures.append(f"missing: {nc.relative_to(ROOT)}")
        return
    if "withSentryConfig" not in src:
        failures.append(f"{nc.relative_to(ROOT)}: withSentryConfig wrapper missing")
    if "ingest.de.sentry.io" not in src and "ingest.us.sentry.io" not in src:
        failures.append(
            f"{nc.relative_to(ROOT)}: CSP connect-src missing Sentry ingest origin "
            "(*.ingest.de.sentry.io for EU region) — browser SDK can't reach Sentry"
        )

    # Replay should mask text + media
    cc = DASHBOARD / "sentry.client.config.ts"
    cc_src = _read(cc)
    if cc_src and "replayIntegration" in cc_src:
        if "maskAllText: true" not in cc_src:
            failures.append(f"{cc.relative_to(ROOT)}: replay integration must set maskAllText=true (PII)")
        if "blockAllMedia: true" not in cc_src:
            failures.append(f"{cc.relative_to(ROOT)}: replay integration must set blockAllMedia=true (PII)")


def main(argv: list[str] | None = None) -> int:
    failures: list[str] = []
    _check_sentry_init_module(failures)
    _check_backend_main(failures)
    _check_workers(failures)
    _check_dashboard(failures)

    if not failures:
        print("✅ Sentry invariants intact (init, workers, crons, PII, dashboard, CSP).")
        return 0

    print(f"❌ Sentry invariant violations ({len(failures)}):\n")
    for f in failures:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
