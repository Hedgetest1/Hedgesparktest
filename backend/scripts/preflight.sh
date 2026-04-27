#!/usr/bin/env bash
# preflight.sh — run before every commit to catch latent bugs static
# analysis can find that unit tests cannot.
#
# Runs in <10 seconds. Exits non-zero on any finding so git's pre-commit
# hook refuses the commit until the operator fixes it.
#
# Installed via backend/scripts/install_hooks.sh which symlinks this
# file (plus a small wrapper) into .git/hooks/pre-commit.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
PY="$BACKEND/venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "preflight: $PY not executable — is the venv set up?" >&2
    exit 2
fi

# Colors (TTY-aware)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YEL='\033[0;33m'; NC='\033[0m'
else
    GREEN=''; RED=''; YEL=''; NC=''
fi

fail=0
step() { printf "\n%bpreflight › %s%b\n" "$YEL" "$1" "$NC"; }
ok()   { printf "%b  ✓ %s%b\n"            "$GREEN" "$1" "$NC"; }
bad()  { printf "%b  ✗ %s%b\n"            "$RED"   "$1" "$NC"; fail=1; }
info() { printf "%b  ℹ %s%b\n"            "$YEL"   "$1" "$NC"; }

# ──────────────────────────────────────────────────────────────────────
# Autonomous self-fix wrapper.
#
# Founder directive 2026-04-26: "il sistema deve essere autonomo. Solo
# billing richiede approvazione." For deterministic audit failures
# (those whose --fix mode produces a complete repair), preflight
# auto-runs --fix, re-stages modified files, retries the audit, and
# only blocks the commit if --fix doesn't resolve it.
#
# Usage:
#   run_with_autofix <audit_name> <audit_script> [--fix-supported]
#
# When --fix-supported is passed, preflight runs --fix on first failure
# and re-stages all tracked files modified during --fix (`git add -u`).
# The audit re-runs; if clean, commit proceeds. If the audit still
# fails after --fix (e.g. semantic cases need human / LLM), preflight
# blocks with the remaining output for human review.
# ──────────────────────────────────────────────────────────────────────
run_with_autofix() {
    local audit_name="$1"
    local audit_script="$2"
    local fix_supported="${3:-}"
    # Optional extra args to pass to the audit (e.g. --strict). Pass via
    # AUDIT_EXTRA_ARGS env var; supports multi-arg via space-separated.
    local extra_args="${AUDIT_EXTRA_ARGS:-}"
    local logfile="/tmp/preflight_${audit_name}.log"
    local fixlog="/tmp/preflight_${audit_name}.fixlog"

    step "$audit_name (${audit_script})"
    # shellcheck disable=SC2086
    if "$PY" "scripts/$audit_script" $extra_args > "$logfile" 2>&1; then
        ok "$(head -1 "$logfile")"
        return 0
    fi

    if [ "$fix_supported" = "--fix-supported" ]; then
        info "audit failed — attempting deterministic --fix self-repair"
        # Snapshot files-with-tree-changes BEFORE --fix so we can identify
        # exactly which files --fix touched (vs sweeping unrelated WIP).
        local before_diff="/tmp/preflight_${audit_name}.before"
        git -C "$REPO_ROOT" diff --name-only > "$before_diff" 2>/dev/null || true

        # shellcheck disable=SC2086
        if "$PY" "scripts/$audit_script" $extra_args --fix > "$fixlog" 2>&1; then
            # Files with tree changes AFTER --fix
            local after_diff="/tmp/preflight_${audit_name}.after"
            git -C "$REPO_ROOT" diff --name-only > "$after_diff" 2>/dev/null || true

            # Files NEW in tree-changes since --fix ran = files --fix mutated.
            # Stage only those (not unrelated WIP).
            local fix_modified
            fix_modified=$(comm -13 <(sort "$before_diff") <(sort "$after_diff"))
            if [ -n "$fix_modified" ]; then
                echo "$fix_modified" | while read -r file; do
                    [ -n "$file" ] && git -C "$REPO_ROOT" add "$file" 2>/dev/null || true
                done
            fi
            # Also re-stage files that were already staged AND modified by --fix
            # (i.e. they changed in working tree since being staged)
            git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null | while read -r file; do
                if [ -n "$file" ] && ! git -C "$REPO_ROOT" diff --quiet -- "$file" 2>/dev/null; then
                    git -C "$REPO_ROOT" add "$file" 2>/dev/null || true
                fi
            done

            # shellcheck disable=SC2086
            if "$PY" "scripts/$audit_script" $extra_args > "$logfile" 2>&1; then
                ok "$(head -1 "$logfile") [auto-repaired]"
                grep "^auto-fix:" "$fixlog" | head -3 | while read -r line; do
                    info "$line"
                done
                if [ -n "$fix_modified" ]; then
                    info "auto-staged: $(echo "$fix_modified" | tr '\n' ' ')"
                fi
                return 0
            fi
        fi
        bad "$audit_name auto-fix did not fully resolve — manual review needed"
        echo "  --- audit output ---"
        head -20 "$logfile" || true
        echo "  --- --fix output ---"
        head -20 "$fixlog" || true
        exit 1
    fi

    bad "$audit_name failed — see $logfile"
    head -30 "$logfile" || true
    exit 1
}

cd "$BACKEND"

# ---------------------------------------------------------------------------
# 1. SQL schema audit — catches ghost tables
# ---------------------------------------------------------------------------
step "SQL schema audit (audit_sql_schema.py)"
if "$PY" scripts/audit_sql_schema.py > /tmp/preflight_schema.log 2>&1; then
    ok "no ghost tables"
else
    bad "ghost tables detected — see /tmp/preflight_schema.log"
    tail -30 /tmp/preflight_schema.log
fi

# ---------------------------------------------------------------------------
# 2. SQL column audit — catches ghost columns
# ---------------------------------------------------------------------------
step "SQL column audit (audit_sql_columns.py)"
if "$PY" scripts/audit_sql_columns.py > /tmp/preflight_columns.log 2>&1; then
    ok "no ghost columns"
else
    bad "ghost columns detected — see /tmp/preflight_columns.log"
    tail -30 /tmp/preflight_columns.log
fi

# ---------------------------------------------------------------------------
# 2b. Tenant isolation audit — catches unfiltered multi-tenant queries
# ---------------------------------------------------------------------------
step "Tenant isolation audit (audit_tenant_isolation.py)"
if "$PY" scripts/audit_tenant_isolation.py > /tmp/preflight_tenant.log 2>&1; then
    ok "no cross-tenant leaks"
else
    bad "tenant isolation risk — see /tmp/preflight_tenant.log"
    tail -40 /tmp/preflight_tenant.log
fi

# ---------------------------------------------------------------------------
# 2b-ter. Lite-orphan endpoint sweep — informational, never blocking.
# Catches Lite-accessible backends with no Lite-floor render path (the
# 2026-04-25 audit class). Prints findings; exit 0 always so a deliberate
# Pro-only render doesn't break commits.
# ---------------------------------------------------------------------------
step "Lite orphan endpoint sweep (audit_lite_orphan_endpoints.py — info only)"
if "$PY" scripts/audit_lite_orphan_endpoints.py > /tmp/preflight_lite_orphans.log 2>&1; then
    if grep -q "No Lite-orphan endpoints" /tmp/preflight_lite_orphans.log; then
        ok "no Lite-orphan endpoints"
    else
        echo "  ℹ candidates flagged — see /tmp/preflight_lite_orphans.log"
    fi
fi

# ---------------------------------------------------------------------------
# 2b-bis. Lite nav ↔ section parity — block when a `section-lite-*`
# anchor exists without matching NAV_ITEMS_LITE entry + SECTION_TO_NAV
# key. Born 2026-04-26 after founder caught the sidebar "going back to
# LITE" while scrolling past `lite-refunds` and `lite-audience`. See
# audit_lite_nav_section_parity.py header.
# ---------------------------------------------------------------------------
run_with_autofix "Lite nav ↔ section parity" "audit_lite_nav_section_parity.py" --fix-supported

# ---------------------------------------------------------------------------
# 2b-ter. Lite hardcoded currency — block when a Lite-floor JSX file
# embeds €/$/£/¥/₩/₹ in user-visible text (description, sample value,
# sublabel) instead of formatting via formatMoneyCompact with the
# merchant's displayCurrency. --fix-supported: deterministic value-
# literal rewrites (`value: "€N"` → `value: N`) auto-applied; semantic
# text rewrites (description/sublabel) flagged for human review.
# ---------------------------------------------------------------------------
run_with_autofix "Lite hardcoded currency" "audit_lite_hardcoded_currency.py" --fix-supported

# ---------------------------------------------------------------------------
# 2b-quater-bis. Backend currency drift — sibling to Lite hardcoded currency.
# Born 2026-04-27 after Phase 1 cosmetic audit found 4 backend sites with
# hardcoded "EUR" in user-visible response paths (revenue_genome AOV/RPC
# genes, klaviyo_events value_currency, instant_onboarding fallback,
# merchant_groups default param). Doctrine in app/core/currency.py:17
# states USD is the safer default. This audit grep-enforces it across
# app/services/*.py and app/api/*.py with an exemption list for
# legitimate uses (SQLAlchemy column defaults, env-var lookups,
# multi-currency maps, COALESCE SQL fallbacks).
# ---------------------------------------------------------------------------
AUDIT_EXTRA_ARGS=--strict run_with_autofix "Backend currency drift" "audit_backend_currency_drift.py"

# ---------------------------------------------------------------------------
# 2b-quater-ter. Lite/Cassettone state-primitive enforcement.
# Born 2026-04-27 after Phase 1 found LiteBaseAnalytics had rolled its own
# TileSkeleton/TileError without a11y attrs. Forces every new Lite or
# Cassettone component that fetches data to use the canonical primitives
# from _CardStates.tsx OR the compact tile variants from
# LiteBaseAnalytics.tsx. One-off heroes (LiteRarsHero) carry an explicit
# `// audit:card-states-ok` marker with a verbal reason.
# ---------------------------------------------------------------------------
AUDIT_EXTRA_ARGS=--strict run_with_autofix "Lite card-states usage" "audit_lite_card_states_usage.py"

# ---------------------------------------------------------------------------
# 2b-quater-quater. Analytics date-range coverage — Phase 3B Stage C
# preventer. Born 2026-04-27 to ensure every Lite analytics endpoint
# with a `days` window also accepts the shared DateRangeQuery
# dependency. Without this, a new endpoint added with `days` but
# without `range_q` silently no-ops the global picker on its tile.
# Exemption list maintained in the script itself for intentional
# non-range-aware surfaces (lifetime aggregate, today-relative model).
# ---------------------------------------------------------------------------
AUDIT_EXTRA_ARGS=--strict run_with_autofix "Analytics date-range coverage" "audit_analytics_date_range_coverage.py"

# ---------------------------------------------------------------------------
# 2b-quater. JSONB array-length guard — caught 2026-04-27 in Gap #8 close.
# psycopg2 may convert Python None to JSON null literal (a JSONB scalar)
# instead of SQL NULL; SQL `IS NULL` doesn't catch JSON null, then
# jsonb_array_length(<scalar>) panics. This static audit asserts every
# jsonb_array_length() call is preceded by a jsonb_typeof = 'array' guard
# within 4 lines (same SQL block).
# ---------------------------------------------------------------------------
AUDIT_EXTRA_ARGS=--strict run_with_autofix "JSONB array-length guard" "audit_jsonb_array_length_guard.py"

# ---------------------------------------------------------------------------
# 2b-quinquies. Dashboard a11y pattern scan — informational, never blocking.
# Catches the two violation classes axe flagged on /app routes during F6
# (icon-only buttons missing aria-label, low-contrast slate-500/600 small
# text on dark composited backgrounds). Run `npm run e2e:a11y` for the
# runtime axe baseline; this static check is the leading indicator.
# ---------------------------------------------------------------------------
step "Dashboard a11y pattern scan (audit_dashboard_a11y.py --strict)"
if "$PY" scripts/audit_dashboard_a11y.py --strict > /tmp/preflight_dash_a11y.log 2>&1; then
    ok "$(head -1 /tmp/preflight_dash_a11y.log)"
else
    bad "a11y pattern regression — see /tmp/preflight_dash_a11y.log"
    head -20 /tmp/preflight_dash_a11y.log || true
fi

# ---------------------------------------------------------------------------
# 2b-quater. §20 brutal-honesty law — block commits that ship with
# unresolved-flag phrases ("Cat-A logged", "minor improvement",
# "deferred", etc.) unless paired with an explicit R-blocker label.
# Born 2026-04-25 after a near-10/10 score claim was followed by
# the founder catching two latent theater bugs the prior turn's
# anemic Devil's-Advocate had missed. See CLAUDE.md §20.
# ---------------------------------------------------------------------------
step "Unresolved-flag scan (audit_unresolved_flags.py — §20 law)"
# We scan the staged commit message and diff, NOT HEAD, because at
# preflight time the new commit hasn't been recorded yet. Pull the
# message from the COMMIT_EDITMSG file written by git pre-commit.
COMMIT_MSG_FILE="$BACKEND/../.git/COMMIT_EDITMSG"
STAGED_DIFF="$(git -C "$BACKEND/.." diff --cached 2>/dev/null || true)"
COMMIT_MSG=""
if [[ -f "$COMMIT_MSG_FILE" ]]; then
    COMMIT_MSG="$(cat "$COMMIT_MSG_FILE")"
fi
SCAN_INPUT_FILE="$(mktemp -t preflight_unresolved_input.XXXXXX)"
trap 'rm -f "$SCAN_INPUT_FILE"' EXIT
{
    printf '%s\n' "$COMMIT_MSG"
    printf '%s\n' "$STAGED_DIFF"
} > "$SCAN_INPUT_FILE"
if "$PY" scripts/audit_unresolved_flags.py --text-file "$SCAN_INPUT_FILE" > /tmp/preflight_unresolved_flags.log 2>&1; then
    ok "no unresolved flags in commit message — §20 law satisfied"
else
    bad "unresolved flag(s) in commit — see /tmp/preflight_unresolved_flags.log"
    tail -25 /tmp/preflight_unresolved_flags.log || true
fi

# ---------------------------------------------------------------------------
# 2b-sexies. §19 Axis 5 reinforcement — every devil's-advocate lens
# in the commit message MUST cite executable verification (grep -n,
# pytest, curl, psql, fenced code block, Evidence: tag). Born after a
# turn-close where DA paragraphs read fine in prose but contained
# zero verification, and re-running them surfaced 50 silent
# regressions. See CLAUDE.md §19 Axis 5 + audit_da_evidence.py.
# ---------------------------------------------------------------------------
step "DA evidence scan (audit_da_evidence.py — §19 Axis 5)"
if "$PY" scripts/audit_da_evidence.py --text-file "$SCAN_INPUT_FILE" > /tmp/preflight_da_evidence.log 2>&1; then
    ok "every DA lens cites executable verification — §19 Axis 5 satisfied"
else
    bad "DA lens without evidence — see /tmp/preflight_da_evidence.log"
    tail -20 /tmp/preflight_da_evidence.log || true
fi

# ---------------------------------------------------------------------------
# 2b-bis. Data-truth audit — catches currency drift (hardcoded €/$, SUM
# without currency filter), DST-unsafe timezone SQL, hardcoded DB creds.
# Baseline 0-findings reached on 2026-04-17 after centralizing
# app/core/currency.py + fixing 11 callers. Strict mode blocks commits
# with ANY critical finding (money_aggregation_no_currency,
# double_timezone_conversion, hardcoded_credentials). Warnings are
# reported but do not block — they surface false positives to refine.
# ---------------------------------------------------------------------------
step "Data-truth audit (audit_data_truth.py --strict)"
if "$PY" scripts/audit_data_truth.py --strict > /tmp/preflight_data_truth.log 2>&1; then
    ok "no currency drift, tz leaks, or credential leaks"
else
    bad "data-truth regressions detected — see /tmp/preflight_data_truth.log"
    tail -30 /tmp/preflight_data_truth.log || true
fi

# ---------------------------------------------------------------------------
# 2c. Model drift audit — catches SQLAlchemy model ↔ DB schema drift
# ---------------------------------------------------------------------------
step "Model drift audit (audit_model_drift.py)"
if "$PY" scripts/audit_model_drift.py > /tmp/preflight_model_drift.log 2>&1; then
    ok "all models in sync with DB"
else
    bad "model drift detected — see /tmp/preflight_model_drift.log"
    tail -30 /tmp/preflight_model_drift.log
fi

# ---------------------------------------------------------------------------
# 2d. Alembic drift gate — the hard gate. Any drift between Base.metadata
# and the live DB schema blocks the commit. This is the top-1-world bar:
# the type system must be load-bearing, not decorative.
# ---------------------------------------------------------------------------
step "Alembic drift check (alembic check)"
if "$BACKEND/venv/bin/alembic" check > /tmp/preflight_alembic.log 2>&1; then
    ok "no model/DB drift"
else
    bad "alembic drift detected — see /tmp/preflight_alembic.log"
    grep -E "Detected (added|removed|type|NULL|changed|comment)" /tmp/preflight_alembic.log | head -30 || true
fi

# Orthogonal gate: wishspark vs wishspark_test alembic-version parity.
# Catches the class where a new migration was applied to prod but
# forgotten on test (or vice-versa). Root-caused + fixed 2026-04-23 in
# migrations/env.py; this audit is the belt + suspenders.
AUDIT_EXTRA_ARGS="--strict" run_with_autofix "Alembic test-DB parity" "audit_alembic_test_db_parity.py" --fix-supported

# ---------------------------------------------------------------------------
# 2e. Silent-fallback observability gate (Tier 2.1). Every `if rc is None`
# fast-path return in app/ must call record_silent_return() so prod Redis
# outages surface in /ops/silent-fallback instead of silently degrading
# subsystems. Baseline 0 bare reached on 2026-04-14 — keep it at 0.
# ---------------------------------------------------------------------------
step "Silent-fallback coverage (audit_silent_returns.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_silent_returns.py" --strict > /tmp/preflight_silent.log 2>&1; then
    ok "all silent fallbacks observed"
else
    bad "bare silent fallbacks detected — see /tmp/preflight_silent.log"
    tail -15 /tmp/preflight_silent.log || true
fi

# ---------------------------------------------------------------------------
# 2f. Exception-debug audit (Tier 2.2). Every debug-only swallow handler
# whose try-block touches a DB session or external client must escalate
# to log.warning (or write_alert) so operators see failures in prod.
# Baseline 0 prod-relevant reached on 2026-04-14.
# ---------------------------------------------------------------------------
step "Exception-debug audit (audit_exception_debug.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_exception_debug.py" --strict > /tmp/preflight_exc_debug.log 2>&1; then
    ok "no prod-relevant exception swallows at debug level"
else
    bad "prod-relevant debug-only swallows detected — see /tmp/preflight_exc_debug.log"
    tail -30 /tmp/preflight_exc_debug.log || true
fi

# ---------------------------------------------------------------------------
# 2f-bis. Exception-sinks audit (Tier 2.2.b). Blocks on CRITICAL kinds:
#   * write_no_rollback — try-block with `db.commit()` paired with
#     `except: log.warning(...)` and NO `db.rollback()`. Leaves the
#     SQLAlchemy session in PendingRollbackError state for the caller.
#     Pinned to ZERO on 2026-04-24 by the SINK-01..04 sweep.
#   * lying_return — `except: return True` without fail-open marker.
# bare_pass + catches_base are reported as INFO and do NOT block.
# ---------------------------------------------------------------------------
step "Exception-sinks audit (audit_exception_sinks.py --critical-only)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_exception_sinks.py" --critical-only > /tmp/preflight_exc_sinks.log 2>&1; then
    ok "no CRITICAL exception sinks (write_no_rollback / lying_return)"
else
    bad "CRITICAL exception sinks detected — see /tmp/preflight_exc_sinks.log"
    tail -40 /tmp/preflight_exc_sinks.log || true
fi

# ---------------------------------------------------------------------------
# 2f-ter. Sentry invariants audit (Tier 2.2.c). Pins the C1..C4 contract
# shipped 2026-04-24: centralized init module, 7-process coverage
# (backend + 6 workers), PII scrub, Team-plan quota gate, dashboard SDK
# files, CSP allowlist. Rebuild the Sentry hardening if this breaks.
# ---------------------------------------------------------------------------
step "Sentry invariants audit (audit_sentry_invariants.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_sentry_invariants.py" > /tmp/preflight_sentry.log 2>&1; then
    ok "Sentry init + workers + PII + dashboard + CSP invariants intact"
else
    bad "Sentry invariants violated — see /tmp/preflight_sentry.log"
    tail -30 /tmp/preflight_sentry.log || true
fi

# ---------------------------------------------------------------------------
# 2f-quater. Sentry alert-rules drift audit (D10 closure to 10/10).
# Blocks commits where backend/config/sentry_alert_rules.yaml content
# hash differs from the recorded sentry_alert_rules.applied.lock —
# i.e. someone edited the rules YAML but didn't run the sync script
# to push the change to Sentry. Bootstrap-friendly: passes when
# SENTRY_AUTH_TOKEN unset (founder hasn't activated yet).
# ---------------------------------------------------------------------------
step "Sentry alert-rules drift (audit_sentry_alert_rules_drift.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_sentry_alert_rules_drift.py" > /tmp/preflight_sentry_rules.log 2>&1; then
    ok "Sentry alert-rules YAML in sync with applied lock"
else
    bad "Sentry alert-rules drift — see /tmp/preflight_sentry_rules.log"
    tail -20 /tmp/preflight_sentry_rules.log || true
fi

# ---------------------------------------------------------------------------
# 2g. Input-bounds audit (Tier 2.3). Every Pydantic request model field
# of type str / list / dict must declare an upper bound (max_length,
# max_items, or pattern=). OWASP A03/A04 — no unbounded user input
# reaches the DB, the logs, or the LLM prompt.
# ---------------------------------------------------------------------------
step "Input-bounds audit (audit_input_bounds.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_input_bounds.py" --strict > /tmp/preflight_input_bounds.log 2>&1; then
    ok "all request fields have upper bounds"
else
    bad "unbounded request fields detected — see /tmp/preflight_input_bounds.log"
    tail -30 /tmp/preflight_input_bounds.log || true
fi

# ---------------------------------------------------------------------------
# 2h. Response-model coverage baseline (Tier 3.1). This is a REPORT-ONLY
# step until the full sweep lands — /pro/, /merchant/, /analytics/ routes
# must declare response_model so the dashboard TS client stays in sync.
# Baseline captured 2026-04-14 and driven down commit by commit.
# ---------------------------------------------------------------------------
step "Response-model coverage (audit_response_models.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_response_models.py" --strict > /tmp/preflight_resp_models.log 2>&1; then
    ok "every /pro|/merchant|/analytics route declares response_model"
else
    bad "untyped /pro|/merchant|/analytics routes detected — see /tmp/preflight_resp_models.log"
    tail -30 /tmp/preflight_resp_models.log || true
fi

# ---------------------------------------------------------------------------
# 2i. Dashboard fetch-call coverage (Tier 3.2). Every fetch() to a
# /pro|/merchant|/analytics path must route through the typed apiClient
# so URL + query + response shape are compile-time validated. Strict
# gate reached on 2026-04-14 — regressions are blocked from now on.
# ---------------------------------------------------------------------------
step "Dashboard fetch coverage (audit_dashboard_fetches.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_dashboard_fetches.py" --strict > /tmp/preflight_dash_fetch.log 2>&1; then
    ok "every fetch() to /pro|/merchant|/analytics routes via apiClient"
else
    bad "bare fetch() calls to typed endpoints detected — see /tmp/preflight_dash_fetch.log"
    tail -20 /tmp/preflight_dash_fetch.log || true
fi

# ---------------------------------------------------------------------------
# 2j. Runtime smoke harness (Tier 3.3). Hits every include_in_schema=True
# GET route on /pro|/merchant|/analytics with a real test merchant
# session via in-process TestClient. Asserts 2xx, Pydantic schema
# match, and p95 latency < 200 ms. ~3-4 seconds — worth it.
# Skippable via SKIP_PREFLIGHT_SMOKE=1 for offline work.
# ---------------------------------------------------------------------------
if [ "${SKIP_PREFLIGHT_SMOKE:-0}" = "1" ]; then
    step "Runtime smoke harness (skipped — SKIP_PREFLIGHT_SMOKE=1)"
    ok "smoke harness skipped by env override"
else
    step "Runtime smoke harness (smoke_endpoints.py --strict)"
    if "$BACKEND/venv/bin/python" "$BACKEND/scripts/smoke_endpoints.py" --strict > /tmp/preflight_smoke.log 2>&1; then
        _SMOKE_STATS=$(grep -E "^  (passed|p95)" /tmp/preflight_smoke.log | tr '\n' ' ' | sed 's/  */ /g')
        ok "smoke harness green —${_SMOKE_STATS}"
    else
        bad "smoke harness detected failing route(s) — see /tmp/preflight_smoke.log"
        grep -A 30 "^Failures:" /tmp/preflight_smoke.log 2>/dev/null || tail -40 /tmp/preflight_smoke.log
    fi
fi

# ---------------------------------------------------------------------------
# 2l. A11y baseline (Tier 6.2). Runs axe-core against every public route
# a cold-start visitor can reach (no Shopify session required). Fails
# on any Critical or Serious WCAG 2.1 AA violation. Requires a running
# dashboard at 127.0.0.1:3000 — skipped cleanly otherwise so backend-
# only commits don't force the dashboard to be up. Force-skip with
# SKIP_PREFLIGHT_A11Y=1 when e.g. Playwright chromium isn't installed.
# ---------------------------------------------------------------------------
if [ "${SKIP_PREFLIGHT_A11Y:-0}" = "1" ]; then
    step "A11y baseline (skipped — SKIP_PREFLIGHT_A11Y=1)"
    ok "a11y skipped by env override"
elif ! curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://127.0.0.1:3000/ | grep -q "^2"; then
    step "A11y baseline (skipped — dashboard not reachable at :3000)"
    ok "a11y skipped — no running dashboard"
else
    step "A11y baseline (e2e/a11y.spec.ts)"
    if ( cd /opt/wishspark/dashboard && CI=1 npx playwright test e2e/a11y.spec.ts --reporter=list ) > /tmp/preflight_a11y.log 2>&1; then
        _A11Y_STATS=$(grep -E "passed|failed" /tmp/preflight_a11y.log | tail -1 | tr -d '\r')
        ok "a11y baseline green — ${_A11Y_STATS:-all routes clean}"
    else
        bad "a11y violations detected — see /tmp/preflight_a11y.log"
        tail -40 /tmp/preflight_a11y.log || true
    fi
fi

# ---------------------------------------------------------------------------
# 2m. Lighthouse budget (Tier 6.3). Hits /, /pricing, /install with
# Lighthouse desktop preset and asserts Performance / Accessibility /
# Best-practices / SEO scores stay at or above the reviewed floor in
# dashboard/lighthouse-budget.json. Opt-in by default — the run takes
# ~45 s, too slow for every commit. Enable with RUN_PREFLIGHT_LH=1
# before releases, dashboard rebuilds, or any visual surgery. Still
# reachability-guarded so setting the flag with no dashboard is a
# clean skip.
# ---------------------------------------------------------------------------
if [ "${RUN_PREFLIGHT_LH:-0}" != "1" ]; then
    step "Lighthouse budget (skipped — set RUN_PREFLIGHT_LH=1 to enable)"
    ok "lighthouse opt-in, not run"
elif ! curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://127.0.0.1:3000/ | grep -q "^2"; then
    step "Lighthouse budget (skipped — dashboard not reachable at :3000)"
    ok "lighthouse skipped — no running dashboard"
else
    step "Lighthouse budget (scripts/run_lighthouse.mjs)"
    if ( cd /opt/wishspark/dashboard && node scripts/run_lighthouse.mjs ) > /tmp/preflight_lh.log 2>&1; then
        _LH_SUMMARY=$(grep -E "^  /" /tmp/preflight_lh.log | tr '\n' ' | ' | sed 's/  */ /g')
        ok "lighthouse within budget — ${_LH_SUMMARY:-all routes green}"
    else
        bad "lighthouse budget exceeded — see /tmp/preflight_lh.log"
        tail -30 /tmp/preflight_lh.log || true
    fi
fi

# ---------------------------------------------------------------------------
# 2o. Stale doctrine-default audit. Catches `.get("monthly_cap_eur", 5.0)`
# class patterns that silently go stale when doctrine moves (today: dev
# LLM cap went €5 → €10 but two callers kept the literal 5.0 as
# fallback). Guard-default zeros (divide-by-zero safety) are permitted;
# named constants (MONTHLY_EUR_CAP) are preferred. Born 2026-04-18
# from the B2 sibling hunt after commit 8bae843.
# ---------------------------------------------------------------------------
step "Stale doctrine-default audit (audit_stale_doctrine_defaults.py)"
if "$PY" "$BACKEND/scripts/audit_stale_doctrine_defaults.py" > /tmp/preflight_doctrine.log 2>&1; then
    ok "no stale literal fallbacks against doctrine keys"
else
    bad "stale doctrine defaults detected — see /tmp/preflight_doctrine.log"
    tail -20 /tmp/preflight_doctrine.log || true
fi

# ---------------------------------------------------------------------------
# 2o-quater. Dashboard dead-code audit. Flags React components + hooks
# exported but never imported anywhere — accumulated cruft from phased
# refactors. Phase 1.9.4 (2026-04-19 brutal audit close-out).
# ---------------------------------------------------------------------------
step "Dashboard dead-code audit (audit_dashboard_dead_code.py)"
if "$PY" "$BACKEND/scripts/audit_dashboard_dead_code.py" > /tmp/preflight_dead_code.log 2>&1; then
    ok "no orphan component/hook exports"
else
    bad "orphan dashboard exports detected — see /tmp/preflight_dead_code.log"
    tail -20 /tmp/preflight_dead_code.log || true
fi

# ---------------------------------------------------------------------------
# 2o-quinquies. Tier-cost literal audit. Catches hardcoded subscription
# / tier-cost numeric constants in arithmetic expressions. The 2026-04-19
# mega audit found `net_roi = prevented - 99.0` in multiple files
# independently; this audit forces every such cost to import from the
# `app.core.tier_pricing` doctrine module.
# ---------------------------------------------------------------------------
step "Tier-cost literal audit (audit_tier_cost_literals.py)"
if "$PY" "$BACKEND/scripts/audit_tier_cost_literals.py" > /tmp/preflight_tier_cost.log 2>&1; then
    ok "no hardcoded tier-cost literals in arithmetic"
else
    bad "hardcoded tier-cost literal(s) — see /tmp/preflight_tier_cost.log"
    tail -20 /tmp/preflight_tier_cost.log || true
fi

# ---------------------------------------------------------------------------
# 2o-sexties. Landing Starter bullets vs shipped dashboard. Asserts
# every feature bullet on the landing Starter card maps to a shipped
# component in the dashboard. Prevents "landing promises X but
# dashboard doesn't deliver X" drift.
# ---------------------------------------------------------------------------
step "Landing Starter shipped-state audit (audit_landing_starter_shipped.py)"
if "$PY" "$BACKEND/scripts/audit_landing_starter_shipped.py" > /tmp/preflight_landing_shipped.log 2>&1; then
    ok "every Starter bullet maps to a shipped dashboard component"
else
    bad "landing Starter bullet not wired — see /tmp/preflight_landing_shipped.log"
    tail -20 /tmp/preflight_landing_shipped.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies-bis. Tier naming canonical audit. Born 2026-04-23 after
# the Lite→Starter rename incident: a multi-file rename was built on
# a stale memory premise without running the cheap disconfirming grep.
# This audit asserts the canonical tier names (Lite/Pro/Scale) are
# present in the landing page.tsx so any future unintentional rename
# is blocked at commit time with a link to the memory explaining why.
# ---------------------------------------------------------------------------
step "Tier naming canonical (audit_tier_naming_canonical.py)"
if "$PY" "$BACKEND/scripts/audit_tier_naming_canonical.py" --strict > /tmp/preflight_tier_naming.log 2>&1; then
    ok "Lite/Pro/Scale canonical naming intact on landing"
else
    bad "tier naming drift detected — see /tmp/preflight_tier_naming.log"
    tail -20 /tmp/preflight_tier_naming.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies-ter. LLM PII guard coverage. Born 2026-04-23 during the
# Tier-A agent audit: 4 direct httpx.post call sites to anthropic/openai
# were shipping merchant-adjacent prompts without calling assert_clean.
# This audit asserts every LLM call site either imports llm_pii_guard
# OR carries a `# llm_pii_guard_audit: synthetic-only` opt-out line.
# ---------------------------------------------------------------------------
step "LLM token ground-truth audit (audit_llm_token_ground_truth.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_llm_token_ground_truth.py" --strict > /tmp/preflight_llm_tokens.log 2>&1; then
    ok "every record_usage uses ground-truth tokens"
else
    bad "LLM token approximation detected — see /tmp/preflight_llm_tokens.log"
    tail -20 /tmp/preflight_llm_tokens.log || true
fi

step "LLM truncation rejection audit (audit_llm_truncation_rejection.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_llm_truncation_rejection.py" --strict > /tmp/preflight_llm_trunc.log 2>&1; then
    ok "every LLM wrapper rejects truncated output"
else
    bad "LLM wrapper missing truncation rejection — see /tmp/preflight_llm_trunc.log"
    tail -20 /tmp/preflight_llm_trunc.log || true
fi

step "LLM model freshness (audit_llm_model_version_freshness.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_llm_model_version_freshness.py" --strict > /tmp/preflight_llm_fresh.log 2>&1; then
    ok "every Claude model string matches canonical lineup"
else
    bad "stale Claude model string detected — see /tmp/preflight_llm_fresh.log"
    tail -20 /tmp/preflight_llm_fresh.log || true
fi

step "LLM HTTP timeout presence (audit_llm_http_timeout.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_llm_http_timeout.py" --strict > /tmp/preflight_llm_timeout.log 2>&1; then
    ok "every httpx.post to LLM API has a timeout"
else
    bad "unbounded LLM httpx.post detected — see /tmp/preflight_llm_timeout.log"
    tail -20 /tmp/preflight_llm_timeout.log || true
fi

step "LLM per-merchant budget gate (audit_llm_per_merchant_budget_gate.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_llm_per_merchant_budget_gate.py" --strict > /tmp/preflight_llm_merchant.log 2>&1; then
    ok "every merchant-scoped LLM call passes through can_charge_merchant (or documented alt)"
else
    bad "ungated merchant-scoped LLM call — see /tmp/preflight_llm_merchant.log"
    tail -20 /tmp/preflight_llm_merchant.log || true
fi

step "LLM PII guard coverage (audit_llm_pii_guard_coverage.py)"
if "$PY" "$BACKEND/scripts/audit_llm_pii_guard_coverage.py" --strict > /tmp/preflight_llm_pii.log 2>&1; then
    ok "every LLM call site passes through PII guard (or opt-out annotated)"
else
    bad "LLM call site missing PII guard — see /tmp/preflight_llm_pii.log"
    tail -20 /tmp/preflight_llm_pii.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies-quater. GDPR shop_redact coverage. Born 2026-04-23 during
# the Tier-A gdpr_processor audit: discovered 23 tables with shop_domain
# were NOT in the hardcoded deletion list — GDPR Art. 17 non-compliance
# live. This audit asserts every shop_domain table in the DB is either
# in the redaction list OR explicitly preserved (audit_log, merchants).
# ---------------------------------------------------------------------------
step "GDPR shop_redact coverage (audit_gdpr_redact_coverage.py)"
if "$PY" "$BACKEND/scripts/audit_gdpr_redact_coverage.py" --strict > /tmp/preflight_gdpr.log 2>&1; then
    ok "every shop_domain table covered by shop_redact (or preserved)"
else
    bad "shop_redact coverage gap — see /tmp/preflight_gdpr.log"
    tail -20 /tmp/preflight_gdpr.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies-quinquies. Safety-check fail-closed audit. Born 2026-04-23
# after reviewer_layer was found with 2 silent-skip safety try/except
# blocks: the try body appended to `blocking` on condition, but the
# except body only logged without blocking-append — a failing check
# silently passed through. This audit walks AST on reviewer_layer,
# bugfix_pipeline, promotion_pipeline, invariant_monitor, orchestrator
# and flags any try/except where the try body has a safety signal
# (blocking.append or raise) but the except body does not.
# ---------------------------------------------------------------------------
step "Safety-check fail-closed (audit_safety_check_fail_closed.py)"
if "$PY" "$BACKEND/scripts/audit_safety_check_fail_closed.py" --strict > /tmp/preflight_safety_check.log 2>&1; then
    ok "every safety-check try/except is fail-closed or opt-out annotated"
else
    bad "silent-skip safety-check pattern — see /tmp/preflight_safety_check.log"
    tail -20 /tmp/preflight_safety_check.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies-sexies. Telegram destructive-command audit_log enforcement.
# Born 2026-04-23 after the Tier-A telegram_agent audit found 2
# destructive operator commands (_cmd_cleanup_confirm, _cmd_cleanup_safe)
# mutating DB state with only log.warning() instead of a hash-chained
# audit_log row. This audit scans every _cmd_* function in
# telegram_agent.py and asserts it either calls write_audit_log()
# when it touches UPDATE/DELETE/INSERT SQL OR is annotated
# `# audit-log: read-only — <reason>` for pure-read commands.
# ---------------------------------------------------------------------------
step "Telegram destructive audit_log (audit_telegram_destructive_audited.py)"
if "$PY" "$BACKEND/scripts/audit_telegram_destructive_audited.py" --strict > /tmp/preflight_tg_audit.log 2>&1; then
    ok "every destructive _cmd_* writes audit_log (or opt-out annotated)"
else
    bad "destructive _cmd_* missing audit_log — see /tmp/preflight_tg_audit.log"
    tail -20 /tmp/preflight_tg_audit.log || true
fi

# ---------------------------------------------------------------------------
# 2o-septies. Session hook centralization audit. Enforces that only
# `lib/useSession.ts` (and the legacy `/app/page.tsx` pre-Phase-2
# migration target) call /merchant/me or /merchant/plan directly.
# Every other component must read session state via `useSession()`.
# Born 2026-04-19 after the FloorLayout session-loss incident — the
# minimal useSession Phase 1.8.1 shipped without the fallback chain
# that /app/page.tsx has, and intermittent "Reconnect my store"
# prompts appeared. Centralizing the hook + forcing consumers through
# it eliminates the duplicate-implementation drift class.
# ---------------------------------------------------------------------------
step "Session hook centralization (audit_session_hook_centralization.py)"
if "$PY" "$BACKEND/scripts/audit_session_hook_centralization.py" > /tmp/preflight_session_hook.log 2>&1; then
    ok "session identity fetching centralized in useSession.ts"
else
    bad "unauthorized session-fetch call(s) — see /tmp/preflight_session_hook.log"
    tail -20 /tmp/preflight_session_hook.log || true
fi

# ---------------------------------------------------------------------------
# 2o-quater. Merchant voice coherence audit. Blocking on forbidden
# pricing phrases (CLAUDE.md §3) anywhere in dashboard source, and
# on third-person narration ("HedgeSpark noticed", "The system
# detected", "Our algorithm", "Our AI") in Spark-surface files
# (dashboard app + components, chat_voice, spark_voice, merchant
# chatbot). Warns on unglossed jargon + personality anti-patterns.
# Single source of truth: app/services/spark_voice.py constants.
# See /docs/HEDGESPARK_MERCHANT_COHERENCE_SPEC.md §5.
# ---------------------------------------------------------------------------
step "Merchant voice coherence (audit_merchant_voice_coherence.py)"
if "$PY" "$BACKEND/scripts/audit_merchant_voice_coherence.py" > /tmp/preflight_voice_coherence.log 2>&1; then
    # Script may print warnings to stdout; extract the final summary line.
    summary=$(tail -1 /tmp/preflight_voice_coherence.log)
    ok "$summary"
else
    bad "forbidden pricing phrase or third-person narration on a Spark surface — see /tmp/preflight_voice_coherence.log"
    tail -30 /tmp/preflight_voice_coherence.log || true
fi

# ---------------------------------------------------------------------------
# 2o-ter. OpenAPI types freshness audit. Catches the drift class where
# the backend adds/changes an endpoint but dashboard/src/app/lib/
# api-types.ts isn't regenerated. The component then ships with a
# hardcoded URL + local type, silently bypassing the typed apiClient.
# Detected on /analytics/visitor-intent-classification 2026-04-19.
# Skips gracefully when backend is unreachable (local dev without
# a backend process).
# ---------------------------------------------------------------------------
run_with_autofix "OpenAPI types freshness" "audit_openapi_types_fresh.py" --fix-supported

# ---------------------------------------------------------------------------
# 2o-bis. Dashboard env-var drift audit. Enforces the single canonical
# name NEXT_PUBLIC_API_BASE_URL across dashboard/src. Catches the bug
# class introduced on 2026-04-17/18/19 where four recently-added files
# used NEXT_PUBLIC_API_BASE (no `_URL`) with a hardcoded default that
# happens to equal the prod URL — silent drift in every non-prod env.
# ---------------------------------------------------------------------------
step "Dashboard API-base env-var audit (audit_dashboard_api_base_env.py)"
if "$PY" "$BACKEND/scripts/audit_dashboard_api_base_env.py" > /tmp/preflight_api_base_env.log 2>&1; then
    ok "dashboard uses NEXT_PUBLIC_API_BASE_URL everywhere"
else
    bad "dashboard env-var drift — see /tmp/preflight_api_base_env.log"
    tail -20 /tmp/preflight_api_base_env.log || true
fi

# ---------------------------------------------------------------------------
# 2p. Scheduled-jobs map sync. Verifies every `def _run_*` helper in
# agent_worker.py is documented in docs/reality_scheduled_jobs.md and
# vice-versa. Born 2026-04-18 after the B1 incident — the reality map
# is load-bearing (prevents proposing duplicate scheduled jobs), so
# drift is a structural bug not a documentation nit.
# ---------------------------------------------------------------------------
step "Scheduled-jobs map sync (audit_scheduled_jobs_map.py)"
if "$PY" "$BACKEND/scripts/audit_scheduled_jobs_map.py" > /tmp/preflight_jobs_map.log 2>&1; then
    ok "agent_worker _run_* helpers all documented"
else
    bad "scheduled-jobs map drift — see /tmp/preflight_jobs_map.log"
    tail -25 /tmp/preflight_jobs_map.log || true
fi

# ---------------------------------------------------------------------------
# 2q. redis_client import correctness. Born 2026-04-18 after three
# sibling bugs were found in the same sprint: frontend_errors.py and
# rum.py imported `get_redis` (never existed); segment_monitor_worker.py
# and action_candidates_engine.py imported `redis_client` (also never
# existed). Each hid inside `try/except Exception` blocks and fail-opened
# silently for weeks. Action_candidates was the worst — the Redis SETNX
# claim silently let every process think it won, defeating cross-process
# refresh serialization.
# This audit parses redis_client.py once, builds the allowlist of actual
# exports, then checks every `from app.core.redis_client import NAME`
# against it. Fast and narrow — catches the exact bug class we saw.
# ---------------------------------------------------------------------------
step "Redis-client import correctness (audit_redis_client_imports.py)"
if "$PY" "$BACKEND/scripts/audit_redis_client_imports.py" --strict > /tmp/preflight_redis_imports.log 2>&1; then
    ok "all redis_client imports resolve to real names"
else
    bad "non-existent redis_client import — see /tmp/preflight_redis_imports.log"
    tail -25 /tmp/preflight_redis_imports.log || true
fi

# ---------------------------------------------------------------------------
# 2r. CLAUDE.md §6 ↔ ecosystem.config.js drift gate. CLAUDE.md §6 is the
# first place an operator looks during an outage; a documented-but-missing
# (or running-but-undocumented) PM2 process wastes triage minutes. This
# audit parses both and fails on any name drift in either direction.
# Runs in milliseconds.
# ---------------------------------------------------------------------------
step "PM2 map sync (audit_claude_md_pm2_map.py)"
if "$PY" "$BACKEND/scripts/audit_claude_md_pm2_map.py" > /tmp/preflight_pm2_map.log 2>&1; then
    ok "CLAUDE.md §6 matches ecosystem.config.js"
else
    bad "PM2 map drift — see /tmp/preflight_pm2_map.log"
    tail -25 /tmp/preflight_pm2_map.log || true
fi

# ---------------------------------------------------------------------------
# 2s. Live dashboard asset audit. Born 2026-04-18 late after the landing
# rendered as unstyled white for the founder. Root cause: `npx next build`
# ran mid-lifetime of the `wishspark-dashboard` PM2 process; the running
# process's in-memory chunk manifest pointed at hashes that were deleted
# during rebuild, so served HTML referenced a CSS chunk that returned 500.
# `curl /` was 200 (HTML envelope fine) — only fetching the chunks caught
# it. This audit:
#   1) compares .next/BUILD_ID mtime vs dashboard PM2 process start time
#      (block if rebuild newer than process = forgot to restart),
#   2) fetches /, /app, /pricing and verifies every _next chunk 200s.
# Skips cleanly when dashboard is unreachable (backend-only commits).
# ---------------------------------------------------------------------------
step "Live dashboard asset audit (audit_dashboard_live.py)"
if "$PY" "$BACKEND/scripts/audit_dashboard_live.py" --strict > /tmp/preflight_dash_live.log 2>&1; then
    _DASH_SUMMARY=$(tail -1 /tmp/preflight_dash_live.log | tr -d '\r')
    ok "${_DASH_SUMMARY:-dashboard assets green}"
else
    bad "dashboard asset drift — see /tmp/preflight_dash_live.log"
    tail -15 /tmp/preflight_dash_live.log || true
fi

# ---------------------------------------------------------------------------
# 2n. SSR body-size floor. Locks in the 2026-04-15 landing SSR fix —
# every prerendered page under `.next/server/app/*.html` must ship
# > 3 KB of real body content. A broken "use client" component that
# returns null during SSR produces ~40 bytes of body and slips past
# every smoke/a11y/bundle gate because the HTML file still exists
# and the bundle still compiles. This gate catches the exact shape
# of that regression at the filesystem level. Runs in milliseconds.
# Skips cleanly when `.next/server/app/` is absent so backend-only
# commits don't force a dashboard rebuild.
# ---------------------------------------------------------------------------
step "SSR body-size floor (audit_ssr_body_size.py)"
if "$PY" "$BACKEND/scripts/audit_ssr_body_size.py" > /tmp/preflight_ssr.log 2>&1; then
    _SSR_SUMMARY=$(grep -E "^(OK|audit_ssr_body_size)" /tmp/preflight_ssr.log | tail -1)
    ok "SSR bodies above floor — ${_SSR_SUMMARY:-skipped, no build}"
else
    bad "SSR body regression detected — see /tmp/preflight_ssr.log"
    tail -20 /tmp/preflight_ssr.log || true
fi

# ---------------------------------------------------------------------------
# 2k. Bundle-size budget (Tier 6.4). Guards the dashboard from a silent
# first-load regression. Four caps: largest chunk, rootMainFiles total,
# chunks total, chunks count. Baseline recorded in
# dashboard/bundle-budget.json. The gate skips cleanly when .next/ is
# absent so backend-only commits don't force a dashboard rebuild; CI
# produces the build before invoking preflight so the gate still fires
# where it matters. Force-skip with SKIP_BUNDLE_BUDGET=1.
# ---------------------------------------------------------------------------
step "Bundle budget (audit_bundle_budget.py)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_bundle_budget.py" > /tmp/preflight_bundle.log 2>&1; then
    _BUNDLE_SUMMARY=$(grep -E "^(OK|audit_bundle_budget)" /tmp/preflight_bundle.log | tail -1)
    ok "bundle within budget — ${_BUNDLE_SUMMARY:-no build found, skipped}"
else
    bad "bundle budget exceeded — see /tmp/preflight_bundle.log"
    tail -20 /tmp/preflight_bundle.log || true
fi

# ---------------------------------------------------------------------------
# 2t. Email deliverability (Resend DNS verification). WARN-only gate — DNS
# lives at the registrar, not in code, so we cannot block commits on an
# external misconfig. But we surface the state on every commit so the
# operator knows whether merchant email is flowing or suppressed.
# Born 2026-04-22 after hedgesparkhq.com DNS flipped `failed` for 10 days
# silently. Companion to `app/services/email_deliverability.py` + the
# hourly `_run_email_dns_status_check` task + `/ops/email-health`.
# ---------------------------------------------------------------------------
step "Email deliverability (audit_email_deliverability.py)"
if "$PY" "$BACKEND/scripts/audit_email_deliverability.py" > /tmp/preflight_email_health.log 2>&1; then
    _EMAIL_SUMMARY=$(tail -1 /tmp/preflight_email_health.log | tr -d '\r')
    ok "${_EMAIL_SUMMARY:-email deliverability check passed}"
else
    bad "email deliverability audit errored — see /tmp/preflight_email_health.log"
    tail -15 /tmp/preflight_email_health.log || true
fi

# ---------------------------------------------------------------------------
# 2t. Email registry coherence — blocks drift between TEMPLATE_REGISTRY /
# IDENTITY_RULES / producer literals / baselines. Born 2026-04-22 after 8
# templates + 5 orphan types silently hard-blocked in prod.
# ---------------------------------------------------------------------------
step "Email registry coherence (audit_email_registry.py)"
if "$PY" "$BACKEND/scripts/audit_email_registry.py" > /tmp/preflight_email_registry.log 2>&1; then
    _EMAIL_REG_SUMMARY=$(tail -1 /tmp/preflight_email_registry.log | tr -d '\r')
    ok "${_EMAIL_REG_SUMMARY:-email registry coherent}"
else
    bad "email registry audit failed — see /tmp/preflight_email_registry.log"
    tail -30 /tmp/preflight_email_registry.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2u. Session-durability invariants — structural preventer for the E2E
# suite at dashboard/e2e/session_durability.spec.ts. Born 2026-04-22
# alongside the suite: each check maps 1:1 to an E2E scenario (S1..S11).
# If someone deletes the retry backoff, the hint-recovery path, the sv
# check, or the Reconnect UI copy, this audit blocks the commit long
# before an E2E run would flag it.
# ---------------------------------------------------------------------------
step "Session durability invariants (audit_session_durability_invariants.py)"
if "$PY" "$BACKEND/scripts/audit_session_durability_invariants.py" > /tmp/preflight_session_durability.log 2>&1; then
    _SD_COUNT=$(grep -c '^  ✓' /tmp/preflight_session_durability.log || echo "?")
    ok "${_SD_COUNT} session-durability invariants intact"
else
    bad "session-durability invariants broken — see /tmp/preflight_session_durability.log"
    tail -30 /tmp/preflight_session_durability.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# Multi-worker safety audit — blocks module-level state that silently
# assumes a single uvicorn worker. Flags suspicious `_*cache`, `_*bucket`,
# `_*counts`, `_cooldown*`, `_mem_*`, `_rate_*` + any module-level Lock().
# Overrideable with `# multi-worker: <disposition>` annotation on the
# declaration or within 6 lines above. Born 2026-04-23 alongside the
# uvicorn --workers 4 flip.
# ---------------------------------------------------------------------------
step "Multi-worker safety audit (audit_multiworker_safety.py)"
if "$PY" "$BACKEND/scripts/audit_multiworker_safety.py" --strict > /tmp/preflight_multiworker.log 2>&1; then
    ok "no unannotated multi-worker hazards in app/api|core|services"
else
    bad "multi-worker safety audit flagged hazards — see /tmp/preflight_multiworker.log"
    tail -30 /tmp/preflight_multiworker.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2g. Audit telemetry coverage regression pin (audit_audit_telemetry_coverage.py)
# ---------------------------------------------------------------------------
# Born 2026-04-24 alongside the /ops/audit-telemetry rollup. Ensures
# every audit listed in WIRED_AUDITS still imports _audit_telemetry_shim
# so the rollup doesn't silently go stale after a refactor.
step "Audit telemetry coverage (audit_audit_telemetry_coverage.py)"
if "$PY" "$BACKEND/scripts/audit_audit_telemetry_coverage.py" > /tmp/preflight_audit_telemetry.log 2>&1; then
    ok "$(tail -1 /tmp/preflight_audit_telemetry.log)"
else
    bad "audit telemetry coverage regression — see /tmp/preflight_audit_telemetry.log"
    tail -20 /tmp/preflight_audit_telemetry.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2h. Tier-gates preventer (audit_tier_gates.py --preventer)
# ---------------------------------------------------------------------------
# Born 2026-04-25 alongside Phase 1.1 of the v1.0 launch roadmap. Warns
# when a `Depends(require_pro_session)` call site lacks a `# tier:` tag
# so future Pro gates can't silently creep in without product review.
# Warn-only during bootstrap — flip to --strict once the 139 existing
# gates have been tagged.
step "Tier-gates preventer (audit_tier_gates.py --preventer)"
if "$PY" "$BACKEND/scripts/audit_tier_gates.py" --preventer > /tmp/preflight_tier_gates.log 2>&1; then
    ok "$(head -1 /tmp/preflight_tier_gates.log)"
else
    bad "tier-gates preventer regression — see /tmp/preflight_tier_gates.log"
    tail -20 /tmp/preflight_tier_gates.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2i. Backend→Frontend coverage preventer (audit_backend_frontend_coverage.py)
# ---------------------------------------------------------------------------
# Born 2026-04-25. Catches "fantasma" endpoints — merchant-facing
# backend routes with no UI consumer (excluding api-types.ts). Runs in
# survey mode today (always exits 0) so we see the 20-route drift
# without blocking; flip to --strict once either the drift is closed
# or every intentional-internal route is tagged `# ui-exempt: <reason>`.
step "Backend→Frontend coverage (audit_backend_frontend_coverage.py)"
if "$PY" "$BACKEND/scripts/audit_backend_frontend_coverage.py" > /tmp/preflight_be_fe_cov.log 2>&1; then
    # Extract headline counts from the human report (compact one-liner)
    scanned="$(grep -oP 'Routes scanned:\s+\*\*\K\d+' /tmp/preflight_be_fe_cov.log || echo '?')"
    covered="$(grep -oP 'Covered:\s+\*\*\K\d+' /tmp/preflight_be_fe_cov.log || echo '?')"
    exempted="$(grep -oP 'Exempted:\s+\*\*\K\d+' /tmp/preflight_be_fe_cov.log || echo '?')"
    uncovered="$(grep -oP 'Uncovered:\s+\*\*\K\d+' /tmp/preflight_be_fe_cov.log || echo '?')"
    ok "merchant routes: $scanned scanned, $covered covered, $exempted exempted, $uncovered uncovered"
else
    bad "backend-frontend coverage preventer error — see /tmp/preflight_be_fe_cov.log"
    tail -20 /tmp/preflight_be_fe_cov.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2j. Test hermeticity (audit_test_hermeticity.py --strict)
# ---------------------------------------------------------------------------
# Born 2026-04-25 as Phase-1.1 LOW-03 closure. All 10 literal-string
# hermeticity-risky tests either cleaned with .delete() inside SAVEPOINT
# or tagged with `# hermetic-ok: <valid-reason>`. Flipped to --strict
# so regressions block commits.
step "Test hermeticity (audit_test_hermeticity.py --strict)"
if "$PY" "$BACKEND/scripts/audit_test_hermeticity.py" --strict > /tmp/preflight_hermeticity.log 2>&1; then
    ok "$(tail -1 /tmp/preflight_hermeticity.log)"
else
    bad "test hermeticity regression — see /tmp/preflight_hermeticity.log"
    tail -30 /tmp/preflight_hermeticity.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 2k. Endpoint test coverage (audit_endpoint_test_coverage.py)
# ---------------------------------------------------------------------------
# Born 2026-04-25. Surfaces routes with no test-file path reference.
# Warn-only bootstrap — 198 routes uncovered at audit birth, visible
# as a telemetry trend via /ops/audit-telemetry. Flip to --strict once
# the gap is closed (either by writing tests or tagging with
# `# test-exempt: <valid-reason>`).
step "Endpoint test coverage (audit_endpoint_test_coverage.py)"
if "$PY" "$BACKEND/scripts/audit_endpoint_test_coverage.py" > /tmp/preflight_endpoint_test_cov.log 2>&1; then
    total="$(grep -oP 'Total distinct routes:\s+\*\*\K\d+' /tmp/preflight_endpoint_test_cov.log || echo '?')"
    covered="$(grep -oP 'Covered:\s+\*\*\K\d+' /tmp/preflight_endpoint_test_cov.log || echo '?')"
    exempted="$(grep -oP 'Exempted:\s+\*\*\K\d+' /tmp/preflight_endpoint_test_cov.log || echo '?')"
    uncovered="$(grep -oP 'Uncovered:\s+\*\*\K\d+' /tmp/preflight_endpoint_test_cov.log || echo '?')"
    ok "endpoints: $total total, $covered covered, $exempted exempted, $uncovered uncovered"
else
    bad "endpoint test coverage audit error — see /tmp/preflight_endpoint_test_cov.log"
    tail -20 /tmp/preflight_endpoint_test_cov.log || true
    fail=1
fi

# ---------------------------------------------------------------------------
# 3. Python AST parse check — any syntax error blocks commit
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Orphan-audit backfill — runs the 6 audits classified as "wire-as-info"
# in Phase B (2026-04-26). They report findings but DON'T block commits;
# the operator sees the surface and triages over time. Documented as
# Phase B in `project_autonomous_system_state_2026_04_26.md`.
#
# Why info-only: most have many existing findings (173 Redis prefixes,
# 14 empty-path fields, 11 N+1 candidates). Wiring blocking would freeze
# every commit until the backlog drains, which isn't pragmatic.
# Wiring info surfaces the live count so it can be driven down.
# ---------------------------------------------------------------------------
step "Orphan-audit backfill (info-only — see /tmp/preflight_orphans.log for detail)"
# Wire 6 previously-orphan scripts. Listed with .py extension so the
# meta-audit (audit_autonomy_coverage.py) can detect they're wired.
# Audits referenced: audit_dev_flag_leaks.py, audit_timezone.py,
# audit_claude_md_redis_keys.py, audit_empty_path_fields.py,
# audit_n_plus_one.py, audit_dead_endpoints.py.
{
    for orphan in audit_dev_flag_leaks.py audit_timezone.py audit_claude_md_redis_keys.py \
                  audit_empty_path_fields.py audit_n_plus_one.py audit_dead_endpoints.py; do
        echo "=== $orphan ==="
        "$PY" "scripts/${orphan}" 2>&1 | head -5 || true
        echo ""
    done
} > /tmp/preflight_orphans.log 2>&1
# Surface a single-line summary
clean_count=0
flagged_count=0
for orphan in audit_dev_flag_leaks.py audit_timezone.py audit_claude_md_redis_keys.py \
              audit_empty_path_fields.py audit_n_plus_one.py audit_dead_endpoints.py; do
    if "$PY" "scripts/${orphan}" 2>/dev/null | head -1 | grep -qE "clean|OK|0 (findings|hits|drift)|^✓"; then
        clean_count=$((clean_count + 1))
    else
        flagged_count=$((flagged_count + 1))
    fi
done
ok "orphan-audit: ${clean_count} clean, ${flagged_count} report findings (info)"

step "Python AST parse (staged .py files)"
cd "$REPO_ROOT"
STAGED_PY="$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.py$' || true)"
if [ -z "$STAGED_PY" ]; then
    ok "no Python files staged"
else
    if REPO_ROOT="$REPO_ROOT" STAGED_PY="$STAGED_PY" "$PY" -c "
import ast, os, sys
root = os.environ['REPO_ROOT']
files = os.environ['STAGED_PY'].strip().split()
for f in files:
    try:
        ast.parse(open(os.path.join(root, f)).read())
    except SyntaxError as e:
        print(f'SYNTAX ERROR: {f}:{e.lineno} {e.msg}')
        sys.exit(1)
print(f'parsed {len(files)} files')
"; then
        ok "all staged Python files parse"
    else
        bad "syntax error in staged Python files"
    fi
fi
cd "$BACKEND"

# ---------------------------------------------------------------------------
# 4. Result
# ---------------------------------------------------------------------------
if [ "$fail" -eq 0 ]; then
    printf "\n%bpreflight: OK — commit allowed%b\n\n" "$GREEN" "$NC"
    exit 0
else
    printf "\n%bpreflight: BLOCKED — commit refused%b\n" "$RED" "$NC"
    printf "%brun \`git commit --no-verify\` to force (not recommended)%b\n\n" "$YEL" "$NC"
    exit 1
fi
