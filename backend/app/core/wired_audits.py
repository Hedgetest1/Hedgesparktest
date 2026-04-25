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
    "audit_route_runtime_coverage.py",
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
