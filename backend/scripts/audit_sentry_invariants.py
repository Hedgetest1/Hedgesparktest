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


def _check_sentry_api_module(failures: list[str]) -> None:
    p = BACKEND / "app/services/sentry_api.py"
    src = _read(p)
    if src is None:
        failures.append(f"missing: {p.relative_to(ROOT)} (SENTRY-2 bidirectional API client)")
        return
    for needed in ("def add_issue_comment", "def set_issue_status",
                    "def extract_issue_id", "def notify_triage_outcome",
                    "def is_configured"):
        if needed not in src:
            failures.append(f"{p.relative_to(ROOT)}: {needed} not exported")
    # Wire-in check: sentry_triage must call notify_triage_outcome on the
    # terminal "linked" transition. Without this the bidirectional loop
    # is half-built — Sentry never learns we acted on the issue.
    triage = _read(BACKEND / "app/services/sentry_triage.py")
    if triage and "notify_triage_outcome" not in triage:
        failures.append(
            "backend/app/services/sentry_triage.py: notify_triage_outcome() not invoked "
            "after candidate creation — bidirectional Sentry loop broken (SENTRY-2)"
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


# ---------------------------------------------------------------------------
# Integration + cron-monitor enumeration pins
# ---------------------------------------------------------------------------
# Rationale: every new Sentry integration added to the client or backend
# init payload costs bundle size (frontend) or startup memory (backend)
# AND may produce quota-bearing events (transactions, profiles, replay
# segments). Every new `@cron_monitor(slug=...)` on a worker adds a
# cron-monitor quota consumer. We baseline the current set here so any
# diff that adds a NEW integration or slug trips preflight — forcing
# the author to answer the 4-question quota pre-check
# (feedback_sentry_quota_pre_check.md) before merging.
#
# Update these BASELINES when you INTENTIONALLY add a new entry. Commit
# message must cite: quota type / plan limit / volume estimate /
# headroom (4-question check from feedback_sentry_quota_pre_check.md).
# ---------------------------------------------------------------------------

# Backend Python integrations registered in app/core/sentry_init.py.
# Match against `<Name>Integration()` constructor calls in the module.
_BACKEND_INTEGRATIONS_BASELINE = {
    "FastApiIntegration",
    "SqlalchemyIntegration",
    "HttpxIntegration",
    "RedisIntegration",
    "LoggingIntegration",
}

# Frontend JS integrations in dashboard/sentry.client.config.ts.
# Match against `Sentry.<Name>Integration` / `replayIntegration` etc.
_FRONTEND_INTEGRATIONS_BASELINE = {
    "replayIntegration",  # Sentry.replayIntegration(...)
}

# @cron_monitor slugs across backend/app/workers/*.py.
_CRON_SLUGS_BASELINE = {
    "agent_worker_cycle",
    "intelligence_worker_cycle",
    "aggregation_worker_cycle",
    "segment_monitor_worker_cycle",
    "nudge_optimization_worker_cycle",
    "gdpr_worker_cycle",
}


def _extract_backend_integrations() -> set[str]:
    """Scan app/core/sentry_init.py for `XxxIntegration` class references."""
    src = _read(BACKEND / "app/core/sentry_init.py") or ""
    # Match `<Name>Integration` as a word (constructor call or class reference).
    return set(re.findall(r"\b([A-Z][A-Za-z0-9_]*Integration)\b", src))


def _extract_frontend_integrations() -> set[str]:
    """Scan dashboard/sentry.client.config.ts for integration references.
    Matches both `Sentry.xxxIntegration(` and bare `xxxIntegration(`."""
    src = _read(DASHBOARD / "sentry.client.config.ts") or ""
    # Match `<name>Integration(` as function call (module-local or Sentry.<name>).
    return set(re.findall(r"\b([a-zA-Z][A-Za-z0-9_]*Integration)\s*\(", src))


def _extract_cron_slugs() -> set[str]:
    """Scan all worker files for `@cron_monitor(slug="...")` literals."""
    slugs: set[str] = set()
    pat = re.compile(r'@cron_monitor\(\s*slug\s*=\s*"([^"]+)"')
    for worker in WORKER_FILES.values():
        src = _read(worker) or ""
        slugs.update(pat.findall(src))
    return slugs


def _check_integration_baselines(failures: list[str]) -> None:
    actual_be = _extract_backend_integrations()
    # Scanner picks up the class name appearing in an import/usage; we
    # accept the intersection with the baseline PLUS anything the
    # regex legitimately flags (the full set).
    added_be = actual_be - _BACKEND_INTEGRATIONS_BASELINE
    missing_be = _BACKEND_INTEGRATIONS_BASELINE - actual_be
    if added_be:
        failures.append(
            "backend/app/core/sentry_init.py: NEW Sentry integration(s) detected: "
            f"{sorted(added_be)} — update _BACKEND_INTEGRATIONS_BASELINE in this audit + "
            "cite quota pre-check (feedback_sentry_quota_pre_check.md 4-question check) in commit message"
        )
    if missing_be:
        failures.append(
            "backend/app/core/sentry_init.py: REMOVED Sentry integration(s): "
            f"{sorted(missing_be)} — update baseline if intentional, otherwise restore"
        )

    actual_fe = _extract_frontend_integrations()
    added_fe = actual_fe - _FRONTEND_INTEGRATIONS_BASELINE
    missing_fe = _FRONTEND_INTEGRATIONS_BASELINE - actual_fe
    if added_fe:
        failures.append(
            "dashboard/sentry.client.config.ts: NEW frontend integration(s): "
            f"{sorted(added_fe)} — update _FRONTEND_INTEGRATIONS_BASELINE + "
            "audit_bundle_budget.py headroom + cite quota pre-check in commit"
        )
    if missing_fe:
        failures.append(
            f"dashboard/sentry.client.config.ts: REMOVED frontend integration(s): {sorted(missing_fe)}"
        )

    actual_slugs = _extract_cron_slugs()
    added_slugs = actual_slugs - _CRON_SLUGS_BASELINE
    missing_slugs = _CRON_SLUGS_BASELINE - actual_slugs
    if added_slugs:
        failures.append(
            "backend/app/workers/*.py: NEW @cron_monitor slug(s): "
            f"{sorted(added_slugs)} — each slug is a Sentry cron-monitor quota consumer. "
            "Team-plan base = 1 monitor. Update _CRON_SLUGS_BASELINE + cite quota pre-check + "
            "confirm SENTRY_CRON_MONITORING allowlist excludes by default"
        )
    if missing_slugs:
        failures.append(
            "backend/app/workers/*.py: REMOVED @cron_monitor slug(s): "
            f"{sorted(missing_slugs)} — update baseline if intentional"
        )


def main(argv: list[str] | None = None) -> int:
    failures: list[str] = []
    _check_sentry_init_module(failures)
    _check_sentry_api_module(failures)
    _check_backend_main(failures)
    _check_workers(failures)
    _check_dashboard(failures)
    _check_integration_baselines(failures)

    if not failures:
        print(
            "✅ Sentry invariants intact "
            "(init, api, workers, crons, PII, dashboard, CSP, integration-pins, slug-pins)."
        )
        return 0

    print(f"❌ Sentry invariant violations ({len(failures)}):\n")
    for f in failures:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
