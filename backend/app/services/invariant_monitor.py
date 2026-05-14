"""
invariant_monitor.py — Periodic post-merge invariant check.

Problem solved
--------------
Preflight audits (backend/scripts/audit_*.py) block commits at git
pre-commit hook time. But if someone bypasses the hook (--no-verify,
merge conflict resolution, emergency fix), a structural regression
can land in main without the hook firing. No runtime signal exists
for that class of regression because the invariants are LATENT — no
merchant triggers them today, so nothing writes to ops_alerts, so the
bugfix pipeline never sees the problem.

This module runs the critical audits on the live source tree on a
schedule (agent_worker cycle, every 15 min) and writes an ops_alert
when any audit fails. From there, bug_triage Rule 7 (generic
≥3-recurrence catch-all) creates a BugFixCandidate after 45 minutes
of the invariant being broken, and the normal self-healing flow
takes it from there.

Design constraints
------------------
- Read-only: this module MUST NOT attempt to fix anything. Only
  detect + alert. Fixes are operator-driven through the standard
  preflight + commit-msg hook chain.
- Cheap: subprocess to existing audit scripts. No new LLM calls.
- Fail-safe: if the audit script itself errors, log but don't
  raise — never take down the worker loop.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
_PYTHON_BIN = str(_BACKEND_ROOT / "venv" / "bin" / "python")

# Registered audits to run on each cycle. Each entry is
# (script_name, alert_type_on_failure, source_key). source_key is the
# stable identifier in ops_alerts.source so dedup + thrash detection
# key off the same name across repeated failures.
_AUDITS: list[tuple[str, str, str]] = [
    (
        "audit_session_durability_invariants.py",
        "invariant_regression",
        "invariant:session_durability",
    ),
    # Multi-worker safety: added 2026-04-23 after the uvicorn --workers 4
    # flip. Runtime recognition for the class of bug that the 2026-04-23
    # sprint just fixed — any new module-level mutable state introduced
    # without a `# multi-worker:` annotation will trip this on live source.
    # Preflight catches at commit; this catches at runtime (fires within
    # 15min of a --no-verify merge).
    (
        "audit_multiworker_safety.py",
        "invariant_regression",
        "invariant:multiworker_safety",
    ),
    # Dev-flag leaks: added 2026-04-23 after the AUTO_DETECT_ENABLED=1
    # leak was found live in prod .env. Runtime recognition for the
    # class of bug where a dev-only env var is active while APP_URL
    # points at hedgesparkhq.com. Preflight cannot see .env (gitignored);
    # this is the only layer that catches an .env-driven leak — fires
    # within 15 min of backend boot with leaky env.
    (
        "audit_dev_flag_leaks.py",
        "invariant_regression",
        "invariant:dev_flag_leaks",
    ),
    # Exception-sinks: added 2026-04-24 after the SINK-01..04 sweep
    # closed all 4 CRITICAL write_no_rollback findings. Runtime
    # recognition for the class of bug where a try/db.commit + bare
    # except: log handler omits db.rollback(), leaving the SQLAlchemy
    # session unusable for the caller's next ORM op. Preflight catches
    # at commit; this catches at runtime within 15min of any
    # --no-verify or merge-conflict-resolution bypass.
    (
        "audit_exception_sinks.py",
        "invariant_regression",
        "invariant:exception_sinks",
    ),
    # Sentry invariants: added 2026-04-24 after the C1..C4 sweep
    # centralized init_sentry + wired all 7 PM2 processes + PII scrub +
    # dashboard SDK. Runtime recognition for the class of regression
    # where someone deletes the sentry_init module, removes init_sentry
    # from a worker, or drops the Sentry CSP allowlist entry — fires
    # within 15min instead of silently losing observability coverage.
    (
        "audit_sentry_invariants.py",
        "invariant_regression",
        "invariant:sentry_invariants",
    ),
    # Sentry alert-rules drift: added 2026-04-24 (D10 closure). Runtime
    # recognition for "YAML edited but never synced to Sentry" — same
    # class as Terraform-state-drift in IaC. Fires within 15min if a
    # commit slips through preflight via --no-verify with stale lock.
    (
        "audit_sentry_alert_rules_drift.py",
        "invariant_regression",
        "invariant:sentry_alert_rules_drift",
    ),
    # Dashboard a11y patterns: added 2026-04-25 night (F6 + post-DA
    # closure). Runtime recognition for low-contrast small text
    # (slate-500/600 + ≤13px), inline-style hex (#64748b / #45556c),
    # icon-only buttons missing aria-label. Preflight runs --strict;
    # this catches the same class within 15min of any --no-verify
    # bypass. The audit is fast (<1s), pure static scan of
    # dashboard/src.
    (
        "audit_dashboard_a11y.py",
        "invariant_regression",
        "invariant:dashboard_a11y",
    ),
    # JSONB array-length guard: added 2026-04-27 evening (Gap #8 close
    # sibling-hunt). Runtime recognition for the class where psycopg2
    # converts Python None to JSON null literal (a JSONB scalar)
    # instead of SQL NULL on JSONB column inserts; SQL `IS NULL`
    # doesn't catch JSON null, so unguarded jsonb_array_length(scalar)
    # panics. Preflight blocks at commit; this catches the same class
    # within 15min of any --no-verify bypass + any merge that
    # resurrects unguarded calls. See audit_jsonb_array_length_guard.py
    # for the regex + 4-line proximity rule.
    (
        "audit_jsonb_array_length_guard.py",
        "invariant_regression",
        "invariant:jsonb_array_length_guard",
    ),
    # Models-without-migrations: added 2026-04-29 (Gap #5 close). Runtime
    # recognition for the class where an SQLAlchemy ORM model lands
    # (with API + frontend wired) but no Alembic migration covers its
    # table. Tables get auto-created by Base.metadata.create_all on
    # boot in dev, hiding the missing migration — but a fresh prod
    # deploy has no such safety net. Audit catches the gap within
    # 15min of any --no-verify or new-model commit. Currently 22
    # baseline drift findings tracked separately.
    (
        "audit_models_without_migrations.py",
        "invariant_regression",
        "invariant:models_without_migrations",
    ),
    # Sprint 3 #3 GDPR k-anonymity invariant: cross_shop_patterns table
    # must never contain a shop_domain column and must enforce n_shops>=3
    # at SQL CHECK level. Wired here so any future drift (constraint
    # removed, PII column added, row-level violation) surfaces within
    # 15min of landing instead of waiting for the next preflight.
    (
        "audit_cross_shop_anonymity.py",
        "invariant_regression",
        "invariant:cross_shop_anonymity",
    ),
    # Dashboard redirect paths: added 2026-04-29 (G4 retro hardening).
    # Catches RedirectResponse/redirect_to URLs in app/api/* that point
    # to /app/<path> where no Next.js page.tsx exists — i.e. the bug
    # founder hit when callback redirected to /app/settings/integrations
    # (which 404'd) instead of /app/settings/google-sheets.
    (
        "audit_dashboard_redirect_paths.py",
        "invariant_regression",
        "invariant:dashboard_redirect_paths",
    ),
    # Naive CSV split: added 2026-04-29 (G5 retro). Catches dashboard
    # files that fetch text/csv content and call .split(',') without
    # routing through parseCsvRfc4180 — bug class breaks on quoted
    # commas in product titles ("Beer, IPA Edition").
    (
        "audit_csv_naive_split.py",
        "invariant_regression",
        "invariant:csv_naive_split",
    ),
    # Currency mixing SUM: added 2026-04-29 (Gap #5 retro). Catches
    # SUM(total_price) aggregated across multiple shop_domain values
    # without currency filter or aggregate_by_currency routing — the
    # exact bug that made revenue_eur = €10k+$8k+£5k = "€23k" before
    # the multi_currency_rollup helper was extracted.
    (
        "audit_currency_mixing_sum.py",
        "invariant_regression",
        "invariant:currency_mixing_sum",
    ),
    # Pro-gate on Lite-rendered tile: added 2026-04-29 (G6 retro).
    # Catches tier-mismatch where a component rendered under
    # {isLiteFloor && <X />} fetches an endpoint with require_pro_session.
    # Lite users hit 403 + see error states. Bug class shipped with
    # UnitEconomicsCard pre-G6-fix.
    (
        "audit_pro_gate_on_lite_tile.py",
        "invariant_regression",
        "invariant:pro_gate_on_lite_tile",
    ),
    # Tracker version bump: added 2026-04-29 (tracker surface retro).
    # Catches the silent class where tracker/*.js is modified without
    # bumping TRACKER_VERSION → merchants serve stale cached old JS.
    # Standalone-mode (timestamp-based) is what runtime sees.
    (
        "audit_tracker_version_bump.py",
        "invariant_regression",
        "invariant:tracker_version_bump",
    ),
    # OAuth state Redis-backed: added 2026-04-29 (OAuth surface retro).
    # Catches future OAuth integrations that store state in module-level
    # dicts instead of Redis (multi-worker-broken). Generalizes the
    # G4 Google OAuth retro fix to all current + future OAuth flows.
    (
        "audit_oauth_state_redis_backed.py",
        "invariant_regression",
        "invariant:oauth_state_redis_backed",
    ),
    # Token storage encrypted: added 2026-04-29 (OAuth surface retro).
    # Catches new merchants.* secret-bearing columns that don't follow
    # the `encrypted_<service>_<kind>` naming convention OR don't have
    # paired encrypt_token/decrypt_token usage. Defense-in-depth on
    # token_crypto adoption — every new credential integration must
    # round-trip through AES-256-GCM, no plaintext slip.
    (
        "audit_token_storage_encrypted.py",
        "invariant_regression",
        "invariant:token_storage_encrypted",
    ),
    # Tracker XSS vectors: added 2026-04-29 (CTO autonomy mandate retro).
    # Catches eval/Function/setTimeout-string/document.write/innerHTML-
    # dynamic in tracker/*.js — XSS surface across all merchant browsers.
    (
        "audit_tracker_xss_vectors.py",
        "invariant_regression",
        "invariant:tracker_xss_vectors",
    ),
    # OAuth scope drift: added 2026-04-29. Catches OAuth scope constants
    # added or expanded without an inline SCOPE-REVIEW: <date> marker.
    # Each scope addition changes provider consent screen + threat model.
    (
        "audit_oauth_scope_drift.py",
        "invariant_regression",
        "invariant:oauth_scope_drift",
    ),
    # OAuth refresh-token rotation: added 2026-04-29. Catches services
    # that store a refresh_token but lack the canonical get_access_token
    # pattern (refresh on expiry + cache + decrypt round-trip). Without
    # this, access_tokens silently expire and API calls 401.
    (
        "audit_oauth_refresh_rotation.py",
        "invariant_regression",
        "invariant:oauth_refresh_rotation",
    ),
    # CLAUDE.md drift: added 2026-04-29. Catches the silent class where
    # CLAUDE.md sections (§6 PM2 process count, §10 TIER_2 file list)
    # diverge from codebase reality. CLAUDE.md is auto-loaded every
    # session as authoritative — drift poisons future decisions.
    (
        "audit_claude_md_drift.py",
        "invariant_regression",
        "invariant:claude_md_drift",
    ),
    # Critical-secrets consistency: added 2026-05-02 after the multidim
    # audit_hardening sweep caught two env-var-name drifts the single-
    # dim R-fix had missed. Catches the class where _CRITICAL_SECRETS
    # in auth_hardening.py lists an env name that no os.getenv reads —
    # making /ops/auth/posture report a key as "missing" when it is
    # actually configured under a different name. Preflight blocks at
    # commit time; this catches the same class within 15min of any
    # --no-verify bypass or future drift introduced via a
    # post-merge-conflict resolution.
    (
        "audit_critical_secrets_consistency.py",
        "invariant_regression",
        "invariant:critical_secrets_consistency",
    ),
    # Shopify api_version pinning: added 2026-05-02 (was preflight-only
    # since b2116ab on 2026-04-30). Catches the class where the survey
    # extension toml or an SDK pin drifts off the {2024-10, 2025-01,
    # 2025-04, 2025-07} allowlist — Shopify CLI does not validate
    # api_version against published SDKs, so the v12 survey-extension
    # disaster (api_version=2026-04 = nonexistent) shipped silent for
    # 13 deploys. Periodic recognition closes the class beyond commit.
    (
        "audit_shopify_api_version_pinned.py",
        "invariant_regression",
        "invariant:shopify_api_version_pinned",
    ),
    # Dashboard dead code: added 2026-05-02. Catches the class where a
    # Lite/Pro card or hook becomes orphan after a tier-partition
    # change but stays on disk because no commit touches it. Caught
    # the VisitorIntentExplorerCard.tsx orphan during the 2026-04-30
    # signals-cassettone hunt (commit 2c3e27d). Preflight catches at
    # commit; this catches drift introduced between commits or via
    # branch merges that touch tier nav without grep-checking
    # downstream consumers.
    (
        "audit_dashboard_dead_code.py",
        "invariant_regression",
        "invariant:dashboard_dead_code",
    ),
    # Frontend never-crash architecture: added 2026-05-02 after founder
    # mandate "FRONT END CHE NON CRASHA MAI". Verifies the 4 error-
    # boundary layers (global-error / app/error / SectionErrorBoundary
    # / ErrorReporterInstaller) + Sentry config files are present AND
    # load-bearing — regressions like commenting out the install call
    # or removing the JSX mount from layout.tsx fire an alert. This is
    # the merchant-facing crash safety net; periodic recognition closes
    # the class against silent removal between commits.
    (
        "audit_route_error_boundary_coverage.py",
        "invariant_regression",
        "invariant:frontend_never_crash",
    ),
    # Lite card-states usage: added 2026-05-02 (was preflight-only).
    # Per CLAUDE.md §4 Phase Ω⁷, every Lite card MUST use the unified
    # CardSkeleton/CardError/CardEmpty primitives + useCardFetch hook
    # so loading/failure/empty states never surface as silent .catch()
    # white-space. Periodic scan catches regressions where a future
    # card lands without those primitives.
    (
        "audit_lite_card_states_usage.py",
        "invariant_regression",
        "invariant:lite_card_states_usage",
    ),
    # Env-var registries class-of-class (added 2026-05-02 after the
    # brutal-CTO 10/10 sprint). Generalises the single-class
    # critical_secrets_consistency audit to ALL module-level
    # bindings matching *_SECRETS / *_KEYS / *_ENV_VARS / *_REQUIRED_ENV
    # / *ENV_REGISTRY naming. Fires when ANY env-var name in such a
    # registry is not read as os.getenv elsewhere — same root cause
    # as the auth_hardening drift, generalised across the codebase.
    (
        "audit_env_var_registries_consistency.py",
        "invariant_regression",
        "invariant:env_var_registries",
    ),
    # ── Bulk-wire batch (2026-05-02 brutal-CTO 10/10 sprint) ──
    # All entries below are state-based audits classified safe-to-wire
    # by audit_invariant_monitor_coverage (no DB/HTTP/subproc side
    # effects). Periodic scan closes the post-merge drift gap that
    # preflight catches at commit. Per-audit doctrine lives in each
    # script's module docstring; this block trades verbose commentary
    # for coverage breadth. Re-classify with `audit_invariant_monitor_coverage`
    # and tag `# invariant-eligible: false` on any audit that should
    # NOT run periodically (e.g. ones discovered to have side effects).
    ("audit_alembic_test_db_parity.py", "invariant_regression", "invariant:alembic_test_db_parity"),
    ("audit_analytics_date_range_coverage.py", "invariant_regression", "invariant:analytics_date_range_coverage"),
    ("audit_audit_io_safety.py", "invariant_regression", "invariant:audit_io_safety"),
    ("audit_audit_telemetry_coverage.py", "invariant_regression", "invariant:audit_telemetry_coverage"),
    ("audit_autonomy_coverage.py", "invariant_regression", "invariant:autonomy_coverage"),
    ("audit_backend_currency_drift.py", "invariant_regression", "invariant:backend_currency_drift"),
    ("audit_backend_frontend_coverage.py", "invariant_regression", "invariant:backend_frontend_coverage"),
    ("audit_bundle_budget.py", "invariant_regression", "invariant:bundle_budget"),
    ("audit_claude_md_pm2_map.py", "invariant_regression", "invariant:claude_md_pm2_map"),
    ("audit_claude_md_redis_keys.py", "invariant_regression", "invariant:claude_md_redis_keys"),
    ("audit_cte_missing_comma.py", "invariant_regression", "invariant:cte_missing_comma"),
    ("audit_dashboard_api_base_env.py", "invariant_regression", "invariant:dashboard_api_base_env"),
    ("audit_dashboard_fetches.py", "invariant_regression", "invariant:dashboard_fetches"),
    ("audit_data_truth.py", "invariant_regression", "invariant:data_truth"),
    ("audit_dead_endpoints.py", "invariant_regression", "invariant:dead_endpoints"),
    ("audit_email_deliverability.py", "invariant_regression", "invariant:email_deliverability"),
    ("audit_email_registry.py", "invariant_regression", "invariant:email_registry"),
    ("audit_empty_path_fields.py", "invariant_regression", "invariant:empty_path_fields"),
    ("audit_endpoint_test_coverage.py", "invariant_regression", "invariant:endpoint_test_coverage"),
    ("audit_exception_debug.py", "invariant_regression", "invariant:exception_debug"),
    ("audit_gdpr_redact_coverage.py", "invariant_regression", "invariant:gdpr_redact_coverage"),
    ("audit_input_bounds.py", "invariant_regression", "invariant:input_bounds"),
    ("audit_landing_lite_shipped.py", "invariant_regression", "invariant:landing_lite_shipped"),
    ("audit_lite_hardcoded_currency.py", "invariant_regression", "invariant:lite_hardcoded_currency"),
    ("audit_lite_nav_section_parity.py", "invariant_regression", "invariant:lite_nav_section_parity"),
    ("audit_lite_orphan_endpoints.py", "invariant_regression", "invariant:lite_orphan_endpoints"),
    ("audit_llm_http_timeout.py", "invariant_regression", "invariant:llm_http_timeout"),
    ("audit_llm_model_version_freshness.py", "invariant_regression", "invariant:llm_model_version_freshness"),
    ("audit_llm_per_merchant_budget_gate.py", "invariant_regression", "invariant:llm_per_merchant_budget_gate"),
    ("audit_llm_pii_guard_coverage.py", "invariant_regression", "invariant:llm_pii_guard_coverage"),
    ("audit_llm_token_ground_truth.py", "invariant_regression", "invariant:llm_token_ground_truth"),
    ("audit_llm_truncation_rejection.py", "invariant_regression", "invariant:llm_truncation_rejection"),
    ("audit_merchant_voice_coherence.py", "invariant_regression", "invariant:merchant_voice_coherence"),
    ("audit_model_drift.py", "invariant_regression", "invariant:model_drift"),
    ("audit_n_plus_one.py", "invariant_regression", "invariant:n_plus_one"),
    ("audit_orphan_card_components.py", "invariant_regression", "invariant:orphan_card_components"),
    ("audit_pro_nav_section_parity.py", "invariant_regression", "invariant:pro_nav_section_parity"),
    ("audit_redis_client_imports.py", "invariant_regression", "invariant:redis_client_imports"),
    ("audit_response_models.py", "invariant_regression", "invariant:response_models"),
    ("audit_scheduled_jobs_map.py", "invariant_regression", "invariant:scheduled_jobs_map"),
    ("audit_session_hook_centralization.py", "invariant_regression", "invariant:session_hook_centralization"),
    ("audit_sidebar_floor_hardcoding.py", "invariant_regression", "invariant:sidebar_floor_hardcoding"),
    ("audit_silent_returns.py", "invariant_regression", "invariant:silent_returns"),
    ("audit_sql_columns.py", "invariant_regression", "invariant:sql_columns"),
    ("audit_sql_schema.py", "invariant_regression", "invariant:sql_schema"),
    ("audit_ssr_body_size.py", "invariant_regression", "invariant:ssr_body_size"),
    ("audit_stale_doctrine_defaults.py", "invariant_regression", "invariant:stale_doctrine_defaults"),
    ("audit_tenant_isolation.py", "invariant_regression", "invariant:tenant_isolation"),
    ("audit_test_hermeticity.py", "invariant_regression", "invariant:test_hermeticity"),
    ("audit_tier_cost_literals.py", "invariant_regression", "invariant:tier_cost_literals"),
    ("audit_tier_gates.py", "invariant_regression", "invariant:tier_gates"),
    ("audit_tier_naming_canonical.py", "invariant_regression", "invariant:tier_naming_canonical"),
    ("audit_timezone.py", "invariant_regression", "invariant:timezone"),
    # Kill-switches wired (added 2026-05-02 elite-tier sprint Gap 3).
    # Catches the bug class where a CLAUDE.md-documented kill switch
    # has zero os.getenv readers in app/. The original bug:
    # PIPELINE_AUTO_PROPOSE_DISABLED was doc-only, never wired.
    ("audit_kill_switches_wired.py", "invariant_regression", "invariant:kill_switches_wired"),
    # reviewer_layer_integrity REMOVED 2026-05-07 — old-brain audit
    # superseded by Brain Vero pivot.
    # Read-replica routing drift (added 2026-05-04 evening flag-closure
    # round). Walks app/api/*.py and flags pure-read GET endpoints still
    # using Depends(get_db) instead of Depends(get_read_db). Catches
    # drift sneaking via --no-verify or merge-conflict resolution that
    # would mean the day a Postgres read replica is provisioned, those
    # GETs don't get the free 2× capacity.
    ("audit_read_replica_routing_drift.py", "invariant_regression", "invariant:read_replica_routing"),
    # Worker-scope coverage (added 2026-05-04 evening). Walks per-shop
    # for-loops in workers + nudge_optimizer service and flags any
    # missing the worker_scope context-manager wrap. Worker_scope is
    # the runtime N+1 detector for background workers (CLAUDE.md §12.1).
    # Drift here = silently lost N+1 visibility.
    ("audit_worker_scope_coverage.py", "invariant_regression", "invariant:worker_scope_coverage"),
    # Client-IP unified extraction (added 2026-05-05). Pins that every
    # site reading the real client IP routes via app/core/client_ip.py
    # — no bare `request.client.host` or raw XFF/CF-Connecting-IP reads.
    # Without this, the day Cloudflare goes in front of the API every
    # rate-limit collapses onto CF POP IPs, audit logs lose attribution,
    # and tracker visitor identity flattens to one per POP. Drift here
    # is a silent latent regression that only fires AFTER the CDN flip.
    ("audit_client_ip_unified.py", "invariant_regression", "invariant:client_ip_unified"),
    # Telegram strategic-only gate (added 2026-05-05 evening — founder
    # direttiva "Telegram solo strategico, tutto il resto autonomo").
    # Catches regression where someone removes the strategic_alert gate
    # from on_alert_responder._ping_founder_p0 OR
    # system_health_synthesizer.send_telegram_signal. Without the gate
    # operational alerts (invariant_regression / sentry / slo /
    # circuit_breaker) re-spam the founder — exactly the noise that
    # 7dc3098 + this sprint closed.
    ("audit_telegram_strategic_only.py", "invariant_regression", "invariant:telegram_strategic_only"),
    # Strategic-dim emitter parity (G3 close 2026-05-06). The
    # _STRATEGIC_DIMENSIONS frozenset in system_health_synthesizer.py
    # MUST match the `name=` field of each emitter. Mismatch silently
    # suppresses every Telegram signal — founder gets ZERO pings on
    # real capacity blowout. Static-parse audit; runtime-monitor here
    # catches drift between emitter renames and constant updates.
    ("audit_strategic_dimension_names_match_emitters.py", "invariant_regression", "invariant:strategic_dim_emitter_parity"),
    # Telegram allowlist ground-truth (G2 close 2026-05-06). Every
    # entry in _TELEGRAM_STRATEGIC_ALLOWLIST must have a real emitter
    # in app/. Phantom entries lie about founder-page coverage and
    # violate principle §2 rule 2 (no half-truths).
    ("audit_telegram_allowlist_ground_truth.py", "invariant_regression", "invariant:telegram_allowlist_ground_truth"),
    # Alert-writer heal-detection (G6 close 2026-05-06). Promoted to
    # invariant-eligible after migration sweep reached 100% coverage
    # (59 writers, 7 heal-wired, 52 truthful opt-out). Catches new
    # writer files that lack heal call OR opt-out comment — runtime
    # recognition of the heal-but-stay-open class regression.
    ("audit_alert_writer_heal_detection.py", "invariant_regression", "invariant:alert_writer_heal_detection"),
    # Synthetic-test-shop alert guard (added 2026-05-06 after a brutal
    # capillary audit found 1079 orphan rows leaked from test fixtures
    # over 25 days). Catches regression of the write_alert synthetic-
    # shop guard at runtime — agent_worker re-fires the audit
    # periodically; if synthetic-shop orphans start growing again, the
    # write_alert guard has been broken (refactored away, exception
    # path bypass, or new test prefix not in test_shop_blocklist).
    ("audit_orphan_alerts_no_growth.py", "invariant_regression", "invariant:orphan_alerts_no_growth"),
    # Operator/dev-shop no-outbound (founder direttiva 2026-05-06):
    # `hedgespark-dev.myshopify.com` (founder's dev tenant) MUST NOT
    # receive merchant-facing email. Catches regression of the
    # email_orchestrator + send_email + merchant-resolution-query
    # gates at runtime if any outbound channel ever ships an email
    # to a known operator address/shop.
    ("audit_operator_dev_shop_no_outbound.py", "invariant_regression", "invariant:operator_dev_shop_no_outbound"),
    # §21.6 brain hooks (added 2026-05-06 founder follow-up):
    # autonomous brain pipeline must operate with same discipline
    # as interactive Claude — macchia d'olio + triple-DA TIER_0 +
    # preventer-wiring + tool-spawn + semantic-ramification. The
    # audit flags missing hooks; goes from info to --strict before
    # pipeline reopens (first paying merchant landing).
    # brain_propagation_hooks + apply_path_adversarial_gate +
    # brain_dormant_flag_coverage REMOVED 2026-05-07 — old-brain
    # audits superseded by Brain Vero pivot. Heal-coverage audit
    # KEPT — relevant for merchant-facing alerts.
    ("audit_alert_heal_coverage.py", "invariant_regression", "invariant:alert_heal_coverage"),
    # PM2 config drift — born 2026-05-07 after wishspark-backend ran
    # with stale args (no --workers 4) silently. Audit skips cleanly
    # when pm2/node absent (CI). HARD FAIL on drift triggers within
    # 15 min of any config-vs-running divergence.
    ("audit_pm2_config_drift.py", "invariant_regression", "invariant:pm2_config_drift"),
    # server_default literal-string drift — born 2026-05-07 closing
    # the `DEFAULT 'now()'` literal-string bug class. Catches any
    # SQLAlchemy model that re-introduces `server_default="<func>()"`
    # without text() wrapping (would break fresh deploys).
    ("audit_server_default_literal_strings.py", "invariant_regression", "invariant:server_default_literal"),
    # Telegram founder-digest scope — born 2026-05-07 closing the 2-
    # day-old "Revenue at risk" merchant-style content leak (founder
    # verbatim "Mi prendi per il culo?"). Ensures build_daily_digest
    # never re-acquires merchant-aggregate revenue / RARS / churn /
    # proven-savings symbols.
    ("audit_telegram_founder_digest_scope.py", "invariant_regression", "invariant:telegram_digest_scope"),
    # Brutal-CTO-inspection follow-up (added 2026-05-02 evening).
    # 1. DB pool doctrine catches code-default drift from CLAUDE.md
    #    §6 (the bug that produced 20× QueuePool exhaustions live).
    # 2. Log rotation health catches pm2-logrotate regression OR any
    #    log file ballooning past 200 MB (the 104 MB unrotated bug).
    # 3. Runtime exception recurrence catches NameError /
    #    UnboundLocalError / AttributeError firing >= 3×/24h in the
    #    backend error log (the 4118 historical NameError class).
    ("audit_db_pool_doctrine.py", "invariant_regression", "invariant:db_pool_doctrine"),
    ("audit_log_rotation_health.py", "invariant_regression", "invariant:log_rotation_health"),
    ("audit_runtime_exception_recurrence.py", "invariant_regression", "invariant:runtime_exception_recurrence"),
    # Brutal-CTO follow-up wave 2 (added 2026-05-02 evening).
    # Worker memory growth catches silent OOM-leak class — workers
    # running for days without restart accumulating heap. Threshold
    # 100% over 14d window minimum. DB table growth catches runaway
    # append-only growth before it degrades query latency.
    ("audit_worker_memory_growth.py", "invariant_regression", "invariant:worker_memory_growth"),
    ("audit_db_table_growth.py", "invariant_regression", "invariant:db_table_growth"),
    # Route runtime coverage (added 2026-05-02 after the brutal-CTO
    # silent_audits alarm caught it as orphan — the script was not in
    # preflight nor invariant_monitor, so nothing periodically refreshed
    # its telemetry. Self-skips with exit 0 when /tmp/cov.json is missing,
    # so wiring it here is safe — telemetry stays fresh + the audit
    # actually fires when a full pytest --cov run produces real coverage
    # data.
    ("audit_route_runtime_coverage.py", "invariant_regression", "invariant:route_runtime_coverage"),
]

# 60s budget per audit subprocess. Born 2026-05-07 after #128658
# (invariant_audit_timeout for audit_dead_endpoints) fired under 4-way
# parallel contention — empirical wall-clock at 4 concurrent audits
# = ~28.7s (subprocess startup + Python import overhead × CPU+DB pool
# pressure), right at the 30s edge. Bump to 60s gives 2× empirical
# margin without per-audit-override scaffolding. If a second audit
# class regresses past 60s in the future, switch to per-audit map
# rather than another global bump.
_TIMEOUT_SECONDS = 60

# Per-audit args override. Born 2026-05-07 closing #129082:
# `audit_exception_sinks.py` has an inverse-contract severity model —
# default mode treats INFO findings (bare_pass / catches_base) as
# blocking, --critical-only restricts blocking to CRITICAL kinds
# (write_no_rollback / lying_return). preflight.sh already invokes
# it with --critical-only (95 INFO baseline accepted post-2026-04-24
# SINK sweep). invariant_monitor must use the same invocation,
# otherwise the 95-finding baseline fires CRITICAL `invariant_
# regression` every cycle and pollutes ops_alerts.
#
# This map is the surgical alternative to the universal --strict
# removal of 2026-05-05 — most audits do NOT have this inverse
# contract, so a global flag flip is wrong; a per-audit override
# is right.
_AUDIT_ARGS_OVERRIDE: dict[str, list[str]] = {
    "audit_exception_sinks.py": ["--critical-only"],
}


def _auto_resolve_prior_invariant(db: Session, source: str) -> int:
    """Resolve prior unresolved invariant alerts (regression OR timeout)
    with this source.

    Thin shim that delegates to the generic
    `app.services.alerting.auto_resolve_alerts` helper (born 2026-05-05).
    Kept as named entry-point for the 9 ok-branches in run_invariant_check
    + inline _check_* probes that were wired before the generic existed.

    Heals BOTH alert classes (born 2026-05-06 after a real
    `invariant_audit_timeout` accumulated under preflight load and never
    auto-healed because the prior version of this shim hardcoded
    `invariant_regression`):
      * `invariant_regression` — audit exited non-zero
      * `invariant_audit_timeout` — audit exceeded _TIMEOUT_SECONDS
    Both clear when the audit subsequently exits 0 within budget.
    """
    from app.services.alerting import auto_resolve_alerts
    n = 0
    for at in ("invariant_regression", "invariant_audit_timeout"):
        n += auto_resolve_alerts(db, source=source, alert_type=at)
    return n


def run_invariant_check(db: Session) -> dict:
    """
    Run every registered audit once. Emit an ops_alert for each
    failure. Returns a summary dict for agent_worker logging.

    Never raises — a broken audit script writes a `critical` alert
    rather than crashing the worker loop.
    """
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    if not os.path.isdir(_SCRIPTS_DIR):
        log.warning("invariant_monitor: scripts dir missing at %s", _SCRIPTS_DIR)
        return summary
    if not os.path.isfile(_PYTHON_BIN):
        log.warning("invariant_monitor: venv python missing at %s", _PYTHON_BIN)
        return summary

    from app.services.alerting import write_alert

    # Runtime checks that are NOT subprocess-audits (live state queries).
    # Each appends directly to summary and optionally writes an alert.
    try:
        _check_fleet_workers_reporting(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: fleet-workers check failed: %s", exc)
    try:
        _check_redis_durability(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: redis-durability check failed: %s", exc)
    try:
        _check_postgres_capacity(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: postgres-capacity check failed: %s", exc)
    try:
        _check_silent_audits(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits check failed: %s", exc)
    try:
        _check_audit_findings_trend(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: audit-findings-trend check failed: %s", exc)
    try:
        _check_reports_invariants(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: reports-invariants check failed: %s", exc)
    try:
        _check_inventory_snapshot_freshness(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: inventory-snapshot check failed: %s", exc)
    try:
        _check_operator_shop_drift(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: operator-drift check failed: %s", exc)
    # preventer-regression check removed Stage 2-E (depended on old-brain
    # bugfix_pipeline.check_preventer_regressions, deleted with the
    # supersession sweep). Brain Vero owns regression detection now via
    # outcome_status on brain_decisions.

    # Run subprocess audits IN PARALLEL — sequential execution at 86 audits ×
    # 30s timeout = 2580s worst case > 15min cycle. Born 2026-05-02 from
    # the brutal-CTO scale audit. ThreadPoolExecutor with bounded
    # concurrency: 4 workers limits Postgres pool pressure (each audit
    # may open its own DB connection via SessionLocal).
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_one_audit(triple: tuple[str, str, str]):
        """Run one audit subprocess. Returns (triple, status, result_obj)
        where status ∈ {"ok","timeout","missing","subprocess_error","fail"}.
        Pure compute — no DB writes here; main thread serialises those."""
        sname, _atype, _src = triple
        spath = _SCRIPTS_DIR / sname
        if not spath.is_file():
            return triple, "missing", None
        try:
            # Born 2026-05-05: do NOT pass --strict here. Each audit
            # script has its own default-mode contract — passing
            # --strict universally caused two failure modes:
            #   1. Audits that don't accept --strict (no argparse flag)
            #      exit 2 with "unrecognized arguments", registering as
            #      a structural-invariant fail when nothing is broken.
            #   2. Audits with a tolerated baseline (e.g. 22 model-
            #      drift findings tracked separately, 184 uncovered
            #      endpoints) flag the baseline as a regression under
            #      --strict, polluting ops_alerts every cycle.
            # Each audit's exit code under default invocation is the
            # truth about structural drift.
            extra_args = _AUDIT_ARGS_OVERRIDE.get(sname, [])
            # subprocess-allowlist: `_PYTHON_BIN` is a module constant
            # (venv python path); `spath` is iterated from a hardcoded
            # directory glob (only `scripts/audit_*.py` files); `extra_
            # args` is read from the hardcoded `_AUDIT_ARGS_OVERRIDE`
            # dict. No external/user-supplied input.
            res = subprocess.run(
                [_PYTHON_BIN, str(spath), *extra_args],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                cwd=str(_BACKEND_ROOT),
            )
        except subprocess.TimeoutExpired:
            return triple, "timeout", None
        except Exception as exc:
            log.error("invariant_monitor: subprocess failed for %s: %s", sname, exc)
            return triple, "subprocess_error", None
        return triple, ("ok" if res.returncode == 0 else "fail"), res

    audit_results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_run_one_audit, t): t for t in _AUDITS}
        for fut in as_completed(futures):
            try:
                audit_results.append(fut.result())
            except Exception as exc:
                triple = futures[fut]
                log.error("invariant_monitor: future failed for %s: %s", triple[0], exc)

    # Now serialise the alert-write phase (single DB session, no thread sharing)
    for triple, status, result in audit_results:
        script_name, alert_type, source = triple
        summary["checked"] += 1
        if status == "missing":
            log.warning("invariant_monitor: audit script missing: %s/%s", _SCRIPTS_DIR, script_name)
            continue
        if status == "subprocess_error":
            continue  # already logged inside _run_one_audit
        if status == "timeout":
            summary["failed"] += 1
            try:
                write_alert(
                    db,
                    severity="critical",
                    source=source,
                    alert_type="invariant_audit_timeout",
                    summary=f"{script_name} timed out after {_TIMEOUT_SECONDS}s",
                    detail={"script": script_name, "timeout": _TIMEOUT_SECONDS},
                )
                summary["alerts_written"] += 1
            except Exception as exc:
                log.error("invariant_monitor: failed to write timeout alert: %s", exc)
            continue

        if status == "ok":
            # Audit green — auto-resolve any prior unresolved alert for
            # this source. Closes the heal-but-stay-open class that
            # left 38 stale alerts piled up across 4 days (5/2-5/5).
            resolved = _auto_resolve_prior_invariant(db, source)
            if resolved:
                summary.setdefault("auto_resolved", 0)
                summary["auto_resolved"] += resolved
            continue

        # Sentry breadcrumb — invariant audit fired. Lands on the active
        # scope so any subsequent agent_worker capture sees the trail.
        try:
            from app.core.sentry_init import pipeline_breadcrumb
            pipeline_breadcrumb(
                "pipeline.invariant",
                f"invariant audit fired: {script_name}",
                level="warning",
                data={
                    "script": script_name,
                    "alert_type": alert_type,
                    "source": source,
                    "exit_code": result.returncode,
                },
            )
        except Exception:
            pass  # SILENT-EXCEPT-OK: sentry breadcrumb is best-effort observability; raising here would mask the audit-script failure being recorded.

        summary["failed"] += 1
        # Trim audit output to a reasonable detail size
        stdout_tail = "\n".join(result.stdout.splitlines()[-40:])
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
        try:
            write_alert(
                db,
                severity="critical",
                source=source,
                alert_type=alert_type,
                summary=f"{script_name} failed — structural invariant broken on main",
                detail={
                    "script": script_name,
                    "exit_code": result.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "remediation": (
                        "Restore the invariant in source OR update the E2E "
                        "suite + audit to reflect the intentional design "
                        "change. See dashboard/e2e/session_durability.spec.ts "
                        "and backend/scripts/audit_session_durability_invariants.py."
                    ),
                },
            )
            summary["alerts_written"] += 1
        except Exception as exc:
            log.error("invariant_monitor: failed to write invariant_regression alert: %s", exc)

    return summary


# ---------------------------------------------------------------------------
# Live-state runtime checks (added 2026-04-23 post --workers 4 flip)
# ---------------------------------------------------------------------------
#
# Per `feedback_post_fix_pipeline_recognition.md`: every hardening fix
# must teach the self-debug pipeline to recognize the class at runtime.
# The 2026-04-23 sprint closed 4 classes — each has a detector below.

def _check_fleet_workers_reporting(db: Session, summary: dict) -> None:
    """Expect 4 uvicorn workers reporting to /metrics within last 60s.

    If fewer, either a worker crashed silently or the fleet metrics
    aggregator (commit 7dace25) regressed.
    """
    expected_min = int(os.getenv("EXPECTED_UVICORN_WORKERS", "4"))
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("invariant_monitor.fleet_workers.no_redis")
            return
        reporting = 0
        for _ in rc.scan_iter(match="hs:metrics:worker:*", count=50):
            reporting += 1
    except Exception as exc:
        record_silent_return("invariant_monitor.fleet_workers.redis_error")
        log.warning("invariant_monitor: fleet-workers scan failed: %s", exc)
        return

    summary["checked"] += 1
    if reporting >= expected_min:
        _auto_resolve_prior_invariant(db, "invariant:fleet_workers_reporting")
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="critical",
            source="invariant:fleet_workers_reporting",
            alert_type="invariant_regression",
            summary=(
                f"Fleet workers reporting to /metrics: {reporting} "
                f"(expected >= {expected_min})"
            ),
            detail={
                "reporting": reporting,
                "expected_min": expected_min,
                "remediation": (
                    "Check pm2 logs wishspark-backend — a worker may have "
                    "crashed silently. Restart backend if needed. If the "
                    "value is persistently low, /metrics aggregator "
                    "(app/core/metrics.py) may have regressed."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: fleet-workers alert write failed: %s", exc)


def _check_redis_durability(db: Session, summary: dict) -> None:
    """Redis must have AOF enabled + maxmemory-policy not noeviction.

    Closes the 2026-04-23 gap where Redis was RDB-snapshot-only (1h data
    loss window) and had no eviction policy (crash-on-OOM risk).
    """
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("invariant_monitor.redis_durability.no_redis")
            return
        info = rc.info("persistence")
        aof_enabled = int(info.get("aof_enabled", 0)) == 1
        policy = rc.config_get("maxmemory-policy").get("maxmemory-policy", "")
    except Exception as exc:
        record_silent_return("invariant_monitor.redis_durability.redis_error")
        log.warning("invariant_monitor: redis-durability probe failed: %s", exc)
        return

    summary["checked"] += 1
    problems = []
    if not aof_enabled:
        problems.append("aof_disabled")
    if policy == "noeviction":
        problems.append(f"maxmemory_policy_unsafe={policy}")

    if not problems:
        _auto_resolve_prior_invariant(db, "invariant:redis_durability")
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="critical",
            source="invariant:redis_durability",
            alert_type="invariant_regression",
            summary=f"Redis durability regressed: {', '.join(problems)}",
            detail={
                "problems": problems,
                "aof_enabled": aof_enabled,
                "maxmemory_policy": policy,
                "remediation": (
                    "redis-cli CONFIG SET appendonly yes && "
                    "redis-cli CONFIG SET maxmemory-policy volatile-lru && "
                    "redis-cli CONFIG REWRITE"
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: redis-durability alert write failed: %s", exc)



def _check_postgres_capacity(db: Session, summary: dict) -> None:
    """Postgres max_connections must be >= 200 (bumped from 100 on 2026-04-23)."""
    expected_min = int(os.getenv("EXPECTED_PG_MAX_CONNECTIONS", "200"))
    try:
        from sqlalchemy import text as _text
        val = db.execute(_text("SHOW max_connections")).scalar()
        current = int(val or 0)
    except Exception as exc:
        log.warning("invariant_monitor: postgres-capacity probe failed: %s", exc)
        return

    summary["checked"] += 1
    if current >= expected_min:
        _auto_resolve_prior_invariant(db, "invariant:postgres_capacity")
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="invariant:postgres_capacity",
            alert_type="invariant_regression",
            summary=f"Postgres max_connections={current} < expected {expected_min}",
            detail={
                "current": current,
                "expected_min": expected_min,
                "remediation": (
                    "Edit /etc/postgresql/*/main/postgresql.conf, set "
                    "max_connections = 200, systemctl restart postgresql"
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: postgres-capacity alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Silent-audit detection — catches regression where a wired audit stops
# emitting telemetry to /ops/audit-telemetry.
# ---------------------------------------------------------------------------
#
# Two failure modes detected:
#  1. "Silent with history" — audit emitted within the last 14 days but the
#     most recent emission is older than SILENT_THRESHOLD_DAYS days. Most
#     likely: audit was removed from preflight, renamed without updating
#     the shim, or its call path short-circuits before reaching the
#     decorator.
#  2. "Never observed" — audit is listed in WIRED_AUDITS but has ZERO
#     telemetry entries in the last INITIAL_GRACE_DAYS. Fires only after
#     a generous grace window so the first run of a freshly-wired audit
#     doesn't page us.
#
# Cooldown: ops_alerts dedups by (alert_type, source) for 24h, so firing
# every 15min per cycle doesn't spam. Alert stays open until resolved
# manually OR the audit starts emitting again (dedup source resolves).

_SILENT_THRESHOLD_DAYS = int(os.getenv("AUDIT_SILENT_THRESHOLD_DAYS", "7"))
_INITIAL_GRACE_DAYS = int(os.getenv("AUDIT_INITIAL_GRACE_DAYS", "14"))
_TELEMETRY_WINDOW_DAYS = 30  # how far back we look for history


def _check_silent_audits(db: Session, summary: dict) -> None:
    """Alert on wired audits that stopped (or never started) emitting
    telemetry to the /ops/audit-telemetry rollup.

    Operator-only audits (heavy, on-demand-only; see
    `wired_audits.OPERATOR_ONLY_AUDITS`) are excluded — silence is
    expected for them, not a regression. Source-of-truth filter
    lives in `wired_audits.silence_monitored_audits()`."""
    try:
        from app.core.wired_audits import silence_monitored_audits
        from app.services.audit_telemetry import read_all_audits
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits import failed: %s", exc)
        return

    try:
        telemetry = read_all_audits(days=_TELEMETRY_WINDOW_DAYS)
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits read failed: %s", exc)
        return

    summary["checked"] += 1

    from datetime import date
    today = date.today()

    silent_with_history: list[tuple[str, int]] = []
    never_observed: list[str] = []

    monitored_audits = silence_monitored_audits()
    for audit_file in monitored_audits:
        audit_name = audit_file[:-3] if audit_file.endswith(".py") else audit_file
        entry = telemetry.get(audit_name)
        if entry is None:
            never_observed.append(audit_name)
            continue
        last_day_str = entry.get("last_day", "")
        if not last_day_str:
            never_observed.append(audit_name)
            continue
        try:
            last_day = date.fromisoformat(last_day_str)
        except (ValueError, TypeError):
            continue
        gap_days = (today - last_day).days
        if gap_days > _SILENT_THRESHOLD_DAYS:
            silent_with_history.append((audit_name, gap_days))

    # Grace window for the "never observed" bucket: only alert after the
    # telemetry system has been running long enough that every wired
    # audit SHOULD have had at least one preflight cycle to emit. Gate:
    # at least ONE audit has `days_seen >= INITIAL_GRACE_DAYS` within
    # the TELEMETRY_WINDOW_DAYS query window — that proves the system
    # has been live for at least that many distinct days.
    grace_window_passed = any(
        entry.get("days_seen", 0) >= _INITIAL_GRACE_DAYS
        for entry in telemetry.values()
    )

    reportable_never = never_observed if grace_window_passed else []

    if not silent_with_history and not reportable_never:
        _auto_resolve_prior_invariant(db, "invariant:silent_audits")
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="invariant:silent_audits",
            alert_type="invariant_regression",
            summary=(
                f"Audit telemetry gaps: "
                f"{len(silent_with_history)} silent >"
                f"{_SILENT_THRESHOLD_DAYS}d, "
                f"{len(reportable_never)} never observed"
            ),
            detail={
                "silent_with_history": [
                    {"audit": n, "days_since_last_emission": g}
                    for n, g in sorted(silent_with_history, key=lambda x: -x[1])
                ],
                "never_observed": sorted(reportable_never),
                "threshold_days": _SILENT_THRESHOLD_DAYS,
                "initial_grace_days": _INITIAL_GRACE_DAYS,
                "window_days": _TELEMETRY_WINDOW_DAYS,
                "remediation": (
                    "Check /ops/audit-telemetry. For each silent audit: "
                    "(a) confirm it still runs in preflight.sh, (b) confirm "
                    "the @telemetered decorator is present on main(), "
                    "(c) restart backend if the shim module itself was "
                    "changed and cached imports are stale."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: silent-audits alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Audit findings-trend detection — catches the "slow accumulation" class.
# ---------------------------------------------------------------------------
#
# Complements silent detection: a silent audit stopped emitting, a trending
# audit is accumulating findings without tripping the preflight gate. The
# detection compares two halves of a 14-day window:
#
#   first_half:  days [8..14] ago (7 days)
#   second_half: days [0..7]  ago (7 days)
#
# If second_half findings > first_half findings AND exceeds absolute +
# relative thresholds, emit an alert. Thresholds tuned to avoid noise:
#   - At least AUDIT_TREND_MIN_ABSOLUTE_DELTA new findings in second half
#     (default 5) so a 0→1 blip doesn't page.
#   - Second-half sum must be >= AUDIT_TREND_MIN_SECOND_HALF (default 3)
#     so a "always noisy" audit with jitter doesn't trip.

_TREND_WINDOW_DAYS = 14
_TREND_MIN_ABSOLUTE_DELTA = int(os.getenv("AUDIT_TREND_MIN_ABSOLUTE_DELTA", "5"))
_TREND_MIN_SECOND_HALF = int(os.getenv("AUDIT_TREND_MIN_SECOND_HALF", "3"))


def _check_audit_findings_trend(db: Session, summary: dict) -> None:
    """Alert on wired audits where findings count is trending up —
    i.e., accumulating a regression that doesn't individually trip
    preflight."""
    try:
        from app.core.wired_audits import WIRED_AUDITS
        from app.services.audit_telemetry import read_audit_history
    except Exception as exc:
        log.warning("invariant_monitor: trend import failed: %s", exc)
        return

    from datetime import date, timedelta
    today = date.today()
    half = _TREND_WINDOW_DAYS // 2
    # Symmetric split: first_half covers the OLDER 7 days, second_half
    # covers the NEWER 7 days. The exact boundary day (today - 7) is
    # excluded so both halves are strictly equal-length.
    first_cutoff = (today - timedelta(days=half)).isoformat()
    second_cutoff_start = (today - timedelta(days=half - 1)).isoformat()

    trending: list[dict] = []
    for audit_file in WIRED_AUDITS:
        audit_name = audit_file[:-3] if audit_file.endswith(".py") else audit_file
        try:
            history = read_audit_history(audit_name, days=_TREND_WINDOW_DAYS)
        except Exception:
            continue
        if not history:
            continue

        first_half_total = 0
        second_half_total = 0
        for rec in history:
            day = rec.get("day", "")
            findings = rec.get("findings", 0) or 0
            if day < first_cutoff:
                first_half_total += findings
            elif day >= second_cutoff_start:
                second_half_total += findings
            # day == first_cutoff is the boundary day — skipped so halves stay equal-length

        delta = second_half_total - first_half_total
        if (
            delta >= _TREND_MIN_ABSOLUTE_DELTA
            and second_half_total >= _TREND_MIN_SECOND_HALF
        ):
            trending.append({
                "audit": audit_name,
                "first_half_findings": first_half_total,
                "second_half_findings": second_half_total,
                "delta": delta,
            })

    summary["checked"] += 1
    if not trending:
        _auto_resolve_prior_invariant(db, "invariant:audit_findings_trend")
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        # Cap detail to top 20 worst trends to avoid payload bloat in
        # the ops_alerts row.
        trending.sort(key=lambda x: -x["delta"])
        top_trends = trending[:20]
        write_alert(
            db,
            severity="warning",
            source="invariant:audit_findings_trend",
            alert_type="invariant_regression",
            summary=(
                f"Audit findings trending up: {len(trending)} audit(s) "
                f"with >= {_TREND_MIN_ABSOLUTE_DELTA} more findings in "
                f"last {_TREND_WINDOW_DAYS // 2}d vs prior {_TREND_WINDOW_DAYS // 2}d"
            ),
            detail={
                "trending_audits": top_trends,
                "total_trending": len(trending),
                "window_days": _TREND_WINDOW_DAYS,
                "min_absolute_delta": _TREND_MIN_ABSOLUTE_DELTA,
                "min_second_half": _TREND_MIN_SECOND_HALF,
                "remediation": (
                    "Check /ops/audit-telemetry for each trending audit. "
                    "A regression class is accumulating without tripping "
                    "preflight. Investigate and either fix the regressions "
                    "or tighten the preflight gate to catch them earlier."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: trend alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Reports feature invariants (Gap #1, 2026-04-28)
# ---------------------------------------------------------------------------
#
# Two failure modes guarded:
#   1. Critical indexes on merchant_saved_reports are missing — the partial
#      UNIQUE on (shop_domain, scheduled_cadence) is the SCHEDULE CAP
#      enforcement; without it merchants could schedule unlimited reports
#      and break the 1-daily / 1-weekly email exemption.
#   2. Cap leak — even with the index, a hypothetical bypass would surface
#      as a shop having >1 active scheduled report at the same cadence.
#
# Both are belt-and-suspenders: the schema enforces, this monitor verifies.

_REPORTS_REQUIRED_INDEXES = {
    "idx_msr_shop_updated",
    "idx_msr_scheduled",
    "uq_msr_shop_name",
    "uq_msr_shop_cadence",
}


def _check_reports_invariants(db: Session, summary: dict) -> None:
    """Reports feature: schema indexes + schedule-cap integrity."""
    summary["checked"] += 1
    try:
        from sqlalchemy import inspect as sql_inspect, func
        from app.services.alerting import write_alert
        from app.models.merchant_saved_report import MerchantSavedReport
    except Exception as exc:
        log.warning("invariant_monitor: reports check imports failed: %s", exc)
        return

    # 1. Required indexes present (via reflection — no raw SQL on system tables)
    try:
        insp = sql_inspect(db.bind)
        present = {idx["name"] for idx in insp.get_indexes("merchant_saved_reports")}
        for uc in insp.get_unique_constraints("merchant_saved_reports"):
            if uc.get("name"):
                present.add(uc["name"])
    except Exception as exc:
        log.warning("invariant_monitor: index reflection failed: %s", exc)
        return
    missing = _REPORTS_REQUIRED_INDEXES - present
    if not missing:
        _auto_resolve_prior_invariant(db, "invariant:reports_indexes")
    if missing:
        summary["failed"] += 1
        try:
            write_alert(
                db,
                severity="critical",
                source="invariant:reports_indexes",
                alert_type="invariant_regression",
                summary=f"Required reports indexes missing: {sorted(missing)}",
                detail={
                    "missing_indexes": sorted(missing),
                    "table": "merchant_saved_reports",
                    "remediation": (
                        "Re-run alembic head — the migration "
                        "zzzb_merchant_saved_reports installs all four. "
                        "If indexes were dropped manually, recreate via "
                        "the model __table_args__ definitions."
                    ),
                },
            )
            summary["alerts_written"] += 1
        except Exception as exc:
            log.error("invariant_monitor: reports-indexes alert write failed: %s", exc)
        return

    # 2. Schedule-cap integrity — cross-tenant by design
    #
    # tenant-isolation-exempt: monitoring query.
    # The check scans across ALL shops to find any shop with >1 active
    # scheduled report at the same cadence — that's exactly the bug class
    # this guards (a missing partial UNIQUE constraint, or someone
    # bypassing it via raw SQL). A shop-scoped query would defeat the
    # purpose. Aggregate metadata only (no PII or row content); fires a
    # CRITICAL alert if violated.
    leak_rows = (
        db.query(
            MerchantSavedReport.shop_domain,
            MerchantSavedReport.scheduled_cadence,
            func.count(MerchantSavedReport.id).label("n"),
        )
        .filter(
            MerchantSavedReport.scheduled.is_(True),
            MerchantSavedReport.deleted_at.is_(None),
        )
        .group_by(
            MerchantSavedReport.shop_domain,
            MerchantSavedReport.scheduled_cadence,
        )
        .having(func.count(MerchantSavedReport.id) > 1)
        .limit(5)
        .all()
    )
    if not leak_rows:
        _auto_resolve_prior_invariant(db, "invariant:reports_schedule_cap")
    if leak_rows:
        summary["failed"] += 1
        try:
            write_alert(
                db,
                severity="critical",
                source="invariant:reports_schedule_cap",
                alert_type="invariant_regression",
                summary=(
                    f"Reports schedule-cap leak: {len(leak_rows)} shop/cadence "
                    f"pair(s) have >1 active scheduled report"
                ),
                detail={
                    "leaks": [
                        {"shop_domain": r.shop_domain, "cadence": r.scheduled_cadence, "count": int(r.n)}
                        for r in leak_rows
                    ],
                    "remediation": (
                        "The partial UNIQUE constraint uq_msr_shop_cadence "
                        "should prevent this. Re-create via alembic; if "
                        "violated, manually unschedule the duplicates "
                        "(UPDATE merchant_saved_reports SET scheduled=false "
                        "WHERE id IN (...))."
                    ),
                },
            )
            summary["alerts_written"] += 1
        except Exception as exc:
            log.error("invariant_monitor: reports-cap alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Inventory snapshot freshness (Gap #4, 2026-04-28)
# ---------------------------------------------------------------------------
#
# Catches the worker drift where the daily inventory_snapshots phase
# stops running for some merchants. We alert if there's any active
# merchant whose most-recent snapshot is older than _STALE_HOURS.

_INVENTORY_STALE_HOURS = 36


def _check_inventory_snapshot_freshness(db: Session, summary: dict) -> None:
    """Stale-snapshot detector for the inventory pipeline.

    tenant-isolation-exempt: monitoring query.
    Scans across all active merchants to find any whose most-recent
    inventory snapshot is older than _STALE_HOURS. Aggregate metadata
    only (no PII).
    """
    summary["checked"] += 1
    try:
        from sqlalchemy import text as _text
        from app.services.alerting import write_alert
    except Exception as exc:
        log.warning("invariant_monitor: inventory check imports failed: %s", exc)
        return

    cutoff_hours = _INVENTORY_STALE_HOURS
    try:
        rows = db.execute(_text(
            f"""
            SELECT
                m.shop_domain,
                MAX(ins.fetched_at) AS last_at
            FROM merchants m
            LEFT JOIN inventory_snapshots ins
              ON ins.shop_domain = m.shop_domain
            WHERE m.install_status = 'active'
              AND m.access_token IS NOT NULL
              AND m.installed_at < (now() - interval '24 hours')
            GROUP BY m.shop_domain
            HAVING (
                MAX(ins.fetched_at) IS NULL
                OR MAX(ins.fetched_at) < (now() - interval '{cutoff_hours} hours')
            )
            LIMIT 5
            """
        )).fetchall()
    except Exception as exc:
        log.warning("invariant_monitor: inventory freshness probe failed: %s", exc)
        return

    if not rows:
        _auto_resolve_prior_invariant(db, "invariant:inventory_freshness")
        return

    # Don't fire for fresh installs (they need 24h to receive their first
    # snapshot via the worker). HAVING `m.installed_at < now() - 24h`
    # already handles that; this defensive log ensures we have a clear
    # audit trail.
    summary["failed"] += 1
    try:
        write_alert(
            db,
            severity="warning",
            source="invariant:inventory_freshness",
            alert_type="invariant_regression",
            summary=(
                f"Inventory pipeline stale: {len(rows)} active merchant(s) "
                f"have no snapshot in the last {cutoff_hours}h"
            ),
            detail={
                "cutoff_hours": cutoff_hours,
                "stale_shops": [
                    {
                        "shop_domain": r.shop_domain,
                        "last_snapshot_at": r.last_at.isoformat() if r.last_at else None,
                    }
                    for r in rows
                ],
                "remediation": (
                    "Check pm2 logs wishspark-aggregation-worker for the "
                    "inventory_snapshot phase. Common causes: Shopify token "
                    "revoked (stale install), API rate-limit backoff loop, "
                    "or worker singleton dropped from PM2."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: inventory freshness alert write failed: %s", exc)


def _check_operator_shop_drift(db: Session, summary: dict) -> None:
    """Detect operator-class shops in the merchants table that are NOT
    declared in `app.core.operator_blocklist._OPERATOR_DEV_SHOPS`.

    tenant-isolation-exempt: monitoring query.

    Operator-class detection (either signal qualifies):
      * `contact_email` ends with `@hedgesparkhq.com` (founder's
        company domain — by policy, every shop with this contact is
        founder-owned)
      * `shop_domain` starts with `hedgespark-` (founder-owned naming
        convention; observed pattern: hedgespark-dev, hedgespark-smoke)

    Why this exists: 2026-05-14 found `hedgespark-smoke.myshopify.com`
    in the DB with `smoke@hedgesparkhq.com` contact, but missing from
    `_OPERATOR_DEV_SHOPS`. Symptom: 25+ noise `pixel_abandonment` alerts
    accumulated over 702h. Fixed by adding the entry. This check fires
    invariant_regression CRITICAL if a future founder-tenant lands in
    DB without the matching declaration — closes the class.

    Conservative: any shop already in the declared frozenset is
    explicitly EXCLUDED from the drift report (the declaration is the
    sufficient act). Only undeclared operator-class shops trigger.
    """
    summary["checked"] += 1
    try:
        from sqlalchemy import text as _text
        from app.core.operator_blocklist import operator_dev_shops
        from app.services.alerting import write_alert
    except Exception as exc:
        log.warning("invariant_monitor: operator-drift imports failed: %s", exc)
        return

    declared = {s.lower() for s in operator_dev_shops()}
    try:
        rows = db.execute(_text(
            """
            SELECT shop_domain, contact_email, install_status, installed_at
            FROM merchants
            WHERE (
                LOWER(COALESCE(contact_email, '')) LIKE '%@hedgesparkhq.com'
                OR LOWER(shop_domain) LIKE 'hedgespark-%'
            )
            ORDER BY installed_at DESC
            LIMIT 50
            """
        )).fetchall()
    except Exception as exc:
        log.warning("invariant_monitor: operator-drift probe failed: %s", exc)
        return

    drifted = [
        r for r in rows
        if (r.shop_domain or "").strip().lower() not in declared
    ]
    if not drifted:
        _auto_resolve_prior_invariant(db, "invariant:operator_shop_drift")
        return

    summary["failed"] += 1
    try:
        write_alert(
            db,
            severity="critical",
            source="invariant:operator_shop_drift",
            alert_type="invariant_regression",
            summary=(
                f"Operator-class shop in DB not declared in "
                f"_OPERATOR_DEV_SHOPS: {len(drifted)} shop(s)"
            ),
            detail={
                "drifted_shops": [
                    {
                        "shop_domain": r.shop_domain,
                        "contact_email": r.contact_email,
                        "install_status": r.install_status,
                        "installed_at": (
                            r.installed_at.isoformat()
                            if r.installed_at else None
                        ),
                    }
                    for r in drifted
                ],
                "remediation": (
                    "Add each `shop_domain` to "
                    "`app/core/operator_blocklist.py::_OPERATOR_DEV_SHOPS` "
                    "and the matching contact_email to "
                    "`_OPERATOR_EMAIL_ADDRESSES`. Without the declaration, "
                    "merchant-funnel-state alerts fire for the founder's "
                    "own test tenants (noise + ops_alerts pressure) AND "
                    "outbound merchant-shaped emails may leak."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: operator-drift alert write failed: %s", exc)
