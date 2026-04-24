#!/usr/bin/env python3
"""audit_audit_telemetry_coverage.py — regression pin for the
/ops/audit-telemetry rollup.

Background
----------
Phase 1-3 of the TIER_2 observability sprint wired `record_run` into a
growing set of `audit_*.py` scripts via `_audit_telemetry_shim.emit`.
The /ops/audit-telemetry endpoint aggregates those emissions into a
per-audit fire-rate + findings trend.

If someone rewrites one of the wired audits and accidentally drops the
shim import, the telemetry rollup silently goes stale for that audit
with zero warning — you'd only notice by comparing the /ops endpoint
to an earlier snapshot, days later.

This preventer guarantees the wiring sticks. For every audit in
`WIRED_AUDITS`, the script parses the AST of the audit and confirms
`_audit_telemetry_shim` is imported. If the import disappears, preflight
fails with a clear diff.

New audits can be wired incrementally — just add them to `WIRED_AUDITS`
in the SAME commit that adds the `emit(...)` calls.

Exit codes:
    0  every wired audit still imports the shim
    1  one or more wired audits lost the import
    2  script error
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# Audits that HAVE been wired to record_run via _audit_telemetry_shim.
# The wiring is done via `@telemetered("audit_NAME")` decorator on
# `def main(...)` (preferred), or an inline `emit(...)` call. Either
# way the module-level `from _audit_telemetry_shim import ...` must
# be present — that's what this preventer checks.
#
# DO NOT remove an entry without also removing the shim call in the
# audit itself — the preventer's job is exactly to catch that mistake.
#
# This set is the authoritative list of every `audit_*.py` in preflight
# EXCEPT `audit_audit_telemetry_coverage.py` (the pin itself, which
# does not participate in telemetry by design).
WIRED_AUDITS: set[str] = {
    "audit_alembic_test_db_parity.py",
    "audit_bundle_budget.py",
    "audit_claude_md_pm2_map.py",
    "audit_claude_md_redis_keys.py",
    "audit_commit_devils_advocate.py",
    "audit_dashboard_api_base_env.py",
    "audit_dashboard_dead_code.py",
    "audit_dashboard_fetches.py",
    "audit_dashboard_live.py",
    "audit_data_truth.py",
    "audit_dead_endpoints.py",
    "audit_dev_flag_leaks.py",
    "audit_email_deliverability.py",
    "audit_email_registry.py",
    "audit_empty_path_fields.py",
    "audit_exception_debug.py",
    "audit_exception_sinks.py",
    "audit_gdpr_redact_coverage.py",
    "audit_input_bounds.py",
    "audit_landing_starter_shipped.py",
    "audit_llm_http_timeout.py",
    "audit_llm_model_version_freshness.py",
    "audit_llm_per_merchant_budget_gate.py",
    "audit_llm_pii_guard_coverage.py",
    "audit_llm_token_ground_truth.py",
    "audit_llm_truncation_rejection.py",
    "audit_merchant_voice_coherence.py",
    "audit_model_drift.py",
    "audit_multiworker_safety.py",
    "audit_n_plus_one.py",
    "audit_openapi_types_fresh.py",
    "audit_redis_client_imports.py",
    "audit_redis_footprint.py",
    "audit_response_models.py",
    "audit_safety_check_fail_closed.py",
    "audit_scheduled_jobs_map.py",
    "audit_sentry_alert_rules_drift.py",
    "audit_sentry_invariants.py",
    "audit_session_durability_invariants.py",
    "audit_session_hook_centralization.py",
    "audit_silent_returns.py",
    "audit_sql_columns.py",
    "audit_sql_schema.py",
    "audit_ssr_body_size.py",
    "audit_stale_doctrine_defaults.py",
    "audit_telegram_destructive_audited.py",
    "audit_tenant_isolation.py",
    "audit_test_hermeticity.py",
    "audit_tier_cost_literals.py",
    "audit_tier_naming_canonical.py",
    "audit_timezone.py",
}

_SHIM_MODULE = "_audit_telemetry_shim"


def _imports_shim(py_path: Path) -> bool:
    """Return True iff the audit script imports the telemetry shim.
    Accepts both `import _audit_telemetry_shim` and
    `from _audit_telemetry_shim import emit` at any scope (module-level
    OR function-level inside main()). AST-based — regex-robust."""
    try:
        tree = ast.parse(py_path.read_text(), filename=str(py_path))
    except Exception:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _SHIM_MODULE:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == _SHIM_MODULE:
                return True
    return False


def main(argv: list[str]) -> int:
    missing: list[str] = []
    not_found: list[str] = []

    for name in sorted(WIRED_AUDITS):
        path = SCRIPTS_DIR / name
        if not path.exists():
            not_found.append(name)
            continue
        if not _imports_shim(path):
            missing.append(name)

    if not_found:
        print(
            f"audit_audit_telemetry_coverage: {len(not_found)} audit(s) listed "
            "as WIRED but the file does not exist:"
        )
        for name in not_found:
            print(f"  - {name}")
        print(
            "\nFix: remove the entry from WIRED_AUDITS if the audit was "
            "renamed/deleted, or restore the missing file."
        )
        return 1

    if missing:
        print(
            f"audit_audit_telemetry_coverage: {len(missing)} audit(s) listed "
            "as WIRED but no longer import _audit_telemetry_shim:"
        )
        for name in missing:
            print(f"  - {name}")
        print(
            "\nEvery wired audit must import `_audit_telemetry_shim` and call "
            "`emit(...)` at each terminating path. Either restore the import "
            "(recommended) or remove the entry from WIRED_AUDITS if the "
            "telemetry was intentionally dropped."
        )
        return 1

    print(
        f"audit_audit_telemetry_coverage: {len(WIRED_AUDITS)} wired audit(s) "
        "all import _audit_telemetry_shim"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_audit_telemetry_coverage: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
