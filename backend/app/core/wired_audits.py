"""Single source of truth for the set of `audit_*.py` scripts that are
wired to /ops/audit-telemetry via `_audit_telemetry_shim`.

Referenced by two places:
  * `backend/scripts/audit_audit_telemetry_coverage.py` — preflight pin
    that asserts every listed audit still imports the shim.
  * `app/services/invariant_monitor.py::_check_silent_audits` — runtime
    check that alerts when a wired audit hasn't emitted for >=
    `SILENT_THRESHOLD_DAYS`.

When wiring a new audit, add one line here AND apply
`@telemetered("audit_NAME")` to its `main()`. The preventer catches the
second-half mistake; this file catches the first-half mistake (add
decorator, forget to register in the canonical list).
"""
from __future__ import annotations


# Every audit listed below:
#   - imports `_audit_telemetry_shim` at module level
#   - has `@telemetered("audit_NAME")` on `main()` OR an inline
#     `emit("audit_NAME", ...)` call
#
# `audit_audit_telemetry_coverage.py` (the pin itself) is EXCLUDED by
# design — pins verify wiring, they don't participate in it.
WIRED_AUDITS: frozenset[str] = frozenset({
    "audit_alembic_test_db_parity.py",
    "audit_backend_frontend_coverage.py",
    "audit_bundle_budget.py",
    "audit_claude_md_pm2_map.py",
    "audit_claude_md_redis_keys.py",
    "audit_commit_devils_advocate.py",
    "audit_cross_shop_anonymity.py",
    "audit_da_evidence.py",
    "audit_dashboard_a11y.py",
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
    "audit_endpoint_test_coverage.py",
    "audit_exception_debug.py",
    "audit_exception_sinks.py",
    "audit_gdpr_redact_coverage.py",
    "audit_input_bounds.py",
    "audit_landing_lite_shipped.py",
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
    "audit_route_runtime_coverage.py",
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
    "audit_test_flake_detection.py",
    "audit_test_hermeticity.py",
    "audit_tier_cost_literals.py",
    "audit_tier_gates.py",
    "audit_tier_naming_canonical.py",
    "audit_timezone.py",
})


def audit_names() -> frozenset[str]:
    """Return the set of audit names (filenames WITHOUT the .py suffix).
    Matches the `audit_name` key used by `audit_telemetry.record_run`."""
    return frozenset(n[:-3] for n in WIRED_AUDITS)


# Operator-only audits — heavy, on-demand, NEVER run automatically:
#   - audit_redis_footprint: simulates Redis memory at N=10k merchants
#     (read-side projection, no DB writes); operator runs at scale-
#     planning gates, not in preflight.
#   - audit_test_flake_detection: runs the test suite N times
#     (default 3, --runs 5+); operator runs at release-gate stability
#     checks, not in preflight.
#
# These MUST still import `_audit_telemetry_shim` so that operator
# invocations are observable (and the
# `audit_audit_telemetry_coverage` preventer keeps the import in
# place). But they're exempted from the `never_observed` alarm in
# `invariant_monitor._check_silent_audits` because expecting them
# to emit on a schedule is a category error — they emit on operator
# decision, not on cycle.
#
# Born 2026-05-13 after `invariant:silent_audits` fired a warning
# for these 2 audits being never_observed. The shim-import discipline
# is preserved, the silence-detection alarm scope is corrected.
OPERATOR_ONLY_AUDITS: frozenset[str] = frozenset({
    "audit_redis_footprint.py",
    "audit_test_flake_detection.py",
    # §23.1 trim (2026-05-07) moved these from hard-gate to ADVISORY
    # (exit 0 by default; emit on `--strict`/operator demand, not every
    # commit). They keep the shim import (still in WIRED_AUDITS, so
    # audit_audit_telemetry_coverage still enforces it) but expecting
    # them to emit on a SCHEDULE is now a category error — the same
    # reasoning that exempted the 2 above. Added 2026-05-16 to close
    # the recurring `invariant:silent_audits` false-warning the trim
    # created but never reconciled (§24.3 trim-review reflex).
    "audit_commit_devils_advocate.py",
    "audit_da_evidence.py",
})


def silence_monitored_audits() -> frozenset[str]:
    """Subset of WIRED_AUDITS that the invariant_monitor's
    `_check_silent_audits` is expected to observe regularly.
    Operator-only audits are excluded — they emit on demand,
    not on a schedule, so silence != regression for them."""
    return WIRED_AUDITS - OPERATOR_ONLY_AUDITS
